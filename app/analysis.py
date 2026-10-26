"""Ties video frames + regions + OCR/recognition + events together.

This is the one place that knows the full per-frame pipeline. The UI layer
calls into this; it does not talk to OCR, image-similarity, or recognition
modules directly.

Region kinds:
  * "score" / "combo" - OCR + TemporalValueTracker (a value must hold
    steady for N frames before being accepted).
  * "arrow" - image-similarity classification against user-captured
    reference snapshots + ArrowStateTracker (an edge-triggered detector,
    since hit/miss flashes are often brief rather than steady).
  * "game_end" - a shared reference-matched end indicator that rotates
    game GUIDs once per confirmed appearance.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import numpy as np

from app import config, image_utils
from app.event_bus import EventBus
from app.models import Event, Region
from app.ocr_engine import OcrEngine
from app.recognition import ArrowStateTracker, TemporalValueTracker, classify_arrow_state, parse_digits

Tracker = Union[TemporalValueTracker, ArrowStateTracker]


@dataclass
class RegionFrameResult:
    region: Region
    raw_text: str = ""
    confidence: Optional[float] = None  # OCR confidence, or arrow similarity score
    error: Optional[str] = None         # OCR error, or an arrow calibration/crop note
    validation_note: Optional[str] = None  # numeric reading pending or rejected by acceptance rules
    candidate_value: Optional[int] = None  # score/combo only
    accepted_value: Optional[int] = None   # score/combo only
    arrow_state: Optional[str] = None      # image match: "hit" / "miss" / "ended" / "idle"
    is_unreadable: bool = False
    crop: Optional[np.ndarray] = None  # kept only for UI preview, not required for logic
    needs_confirmation: bool = False
    frame_index: Optional[int] = None
    video_time_ms: Optional[int] = None


@dataclass
class FrameAnalysisResult:
    region_results: List[RegionFrameResult]
    new_events: List[Event]
    processing_ms: float

    @property
    def needs_confirmation(self) -> bool:
        return any(result.needs_confirmation for result in self.region_results)


class AnalysisSession:
    def __init__(self, event_bus: EventBus, ocr_engine: Optional[OcrEngine] = None):
        self.event_bus = event_bus
        self.ocr_engine = ocr_engine or OcrEngine()
        self.regions: List[Region] = []
        self._trackers: Dict[str, Tracker] = {}
        self._arrow_references: Dict[str, Dict[str, List[np.ndarray]]] = {}
        self._score_templates: Dict[str, Dict[str, List[np.ndarray]]] = {}
        self._last_numeric_accept_time_ms: Dict[str, int] = {}
        self._numeric_evidence: Dict[str, tuple[int, int, int]] = {}
        self._last_numeric_frame: Dict[str, tuple[int, int]] = {}
        self._last_observed_score: Dict[str, int] = {}
        self._generation = 0
        self._score_lock = threading.RLock()
        self._combo_lock = threading.RLock()
        self._arrow_lock = threading.RLock()
        self.required_consecutive_frames = config.DEFAULT_REQUIRED_CONSECUTIVE_FRAMES
        self.arrow_similarity_threshold = config.DEFAULT_ARROW_SIMILARITY_THRESHOLD
        self.arrow_min_confirm_frames = config.DEFAULT_ARROW_MIN_CONFIRM_FRAMES
        self.game_end_similarity_threshold = config.DEFAULT_GAME_END_SIMILARITY_THRESHOLD
        self.game_end_confirm_frames = config.DEFAULT_GAME_END_CONFIRM_FRAMES
        self._last_game_end_frame = -1
        self._game_end_active = False
        self.is_running = False
        self.recording_enabled = True

    # -- configuration -------------------------------------------------
    @property
    def generation(self) -> int:
        return self._generation

    def set_regions(self, regions: List[Region]) -> None:
        """Replace the active region set. Always resets tracking state,
        since previous trackers no longer correspond 1:1 to new regions.

        Also (re)decodes any arrow reference images from the region data,
        so calling this again after capturing a new reference picks it up.
        """
        with self._score_lock, self._combo_lock, self._arrow_lock:
            self._generation += 1
            self.regions = list(regions)
            self._trackers = {}
            self._arrow_references = {}
            self._score_templates = {}
            self._last_numeric_accept_time_ms = {}
            self._numeric_evidence = {}
            self._last_numeric_frame = {}
            self._last_observed_score = {}
            for r in self.regions:
                if r.kind in ("arrow", "game_end"):
                    confirm_frames = (
                        self.game_end_confirm_frames if r.kind == "game_end"
                        else self.arrow_min_confirm_frames
                    )
                    self._trackers[r.key()] = ArrowStateTracker(confirm_frames)
                    decoded = {}
                    for state, encoded_images in r.reference_images.items():
                        images = []
                        for b64 in encoded_images:
                            img = image_utils.decode_base64_png_to_crop(b64)
                            if img is not None:
                                images.append(img)
                        if images:
                            decoded[state] = images
                    self._arrow_references[r.key()] = decoded
                else:
                    self._trackers[r.key()] = TemporalValueTracker(1)
                    if r.kind == "score":
                        self.update_score_templates(r)

    def set_required_consecutive_frames(self, n: int) -> None:
        with self._score_lock, self._combo_lock:
            self.required_consecutive_frames = n
            self._numeric_evidence.clear()

    def set_arrow_similarity_threshold(self, value: float) -> None:
        with self._arrow_lock:
            self.arrow_similarity_threshold = value

    def set_arrow_min_confirm_frames(self, n: int) -> None:
        with self._arrow_lock:
            self.arrow_min_confirm_frames = n
            for region in self.regions:
                tracker = self._trackers[region.key()]
                if region.kind == "arrow" and isinstance(tracker, ArrowStateTracker):
                    tracker.min_confirm_frames = n

    def set_game_end_settings(self, threshold: float, confirm_frames: int) -> None:
        if not 0.5 <= threshold <= 1 or confirm_frames < 1:
            raise ValueError("Game-end threshold must be 0.5..1 and confirmation frames must be positive.")
        with self._arrow_lock:
            self.game_end_similarity_threshold = threshold
            self.game_end_confirm_frames = confirm_frames
            for region in self.regions:
                tracker = self._trackers[region.key()]
                if region.kind == "game_end" and isinstance(tracker, ArrowStateTracker):
                    tracker.min_confirm_frames = confirm_frames

    def reset_tracking(self) -> None:
        """Call on seek / new video load / new session."""
        with self._score_lock, self._combo_lock, self._arrow_lock:
            self._generation += 1
            for region in self.regions:
                if region.kind != "game_end":
                    self._trackers[region.key()].reset()
            self._last_numeric_accept_time_ms = {}
            self._numeric_evidence = {}
            self._last_numeric_frame = {}
            self._last_observed_score = {}

    def new_session(
        self, frame_index: Optional[int] = None, video_time_ms: Optional[int] = None,
        *, reason: str = "manual", clear_events: bool = True,
    ) -> Event:
        """Drain active analysis before resetting its session and tracking."""
        with self._score_lock, self._combo_lock, self._arrow_lock:
            event = self.event_bus.new_session(
                frame_index, video_time_ms, reason=reason, clear_events=clear_events,
            )
            self.reset_tracking()
            if clear_events:
                self._last_game_end_frame = -1
                self._game_end_active = False
                for region in self.regions:
                    if region.kind == "game_end":
                        self._trackers[region.key()].reset()
            return event

    def start(self) -> None:
        with self._score_lock, self._combo_lock, self._arrow_lock:
            self.is_running = True

    def stop(self) -> None:
        with self._score_lock, self._combo_lock, self._arrow_lock:
            self._generation += 1
            self.is_running = False

    def tracker_for(self, region: Region) -> Tracker:
        if region.kind in ("arrow", "game_end"):
            lock = self._arrow_lock
        elif region.kind == "score":
            lock = self._score_lock
        else:
            lock = self._combo_lock
        with lock:
            return self._trackers[region.key()]

    def has_complete_score_templates(self, region: Region) -> bool:
        with self._score_lock:
            return self.uses_score_templates(region) and _has_complete_score_templates(
                self._score_templates.get(region.key(), {}), min_examples=1
            )

    def uses_score_templates(self, region: Region) -> bool:
        with self._score_lock:
            return (
                region.digit_templates_reviewed
                and region.score_template_version == config.SCORE_TEMPLATE_VERSION
                and any(
                    self._score_templates.get(region.key(), {}).values()
                )
            )

    def update_score_templates(self, region: Region) -> None:
        """Refresh calibration without resetting accepted values or event history."""
        with self._score_lock:
            decoded = {}
            for digit, examples in region.digit_templates.items():
                images = []
                for encoded in examples:
                    image = image_utils.decode_base64_png_to_crop(encoded)
                    if image is None or image.shape[:2] != (32, 24):
                        raise ValueError(f"Invalid score template for digit {digit}.")
                    images.append(image[:, :, 0])
                decoded[digit] = images
            self._score_templates[region.key()] = decoded
            self._numeric_evidence.pop(region.key(), None)
            self._generation += 1

    def set_combo_yellow_filter(self, region: Region, enabled: bool) -> None:
        """Change preprocessing without mixing evidence from the two modes."""
        if region.kind != "combo":
            raise ValueError("The yellow-digit filter is only available for combo regions.")
        with self._combo_lock:
            region.combo_yellow_filter = enabled
            self._numeric_evidence.pop(region.key(), None)
            self._last_numeric_frame.pop(region.key(), None)
            self._generation += 1

    def set_combo_fast_mode(self, region: Region, enabled: bool) -> None:
        if region.kind != "combo":
            raise ValueError("Fast live mode is only available for combo regions.")
        with self._combo_lock:
            region.combo_fast_mode = enabled
            self._numeric_evidence.pop(region.key(), None)
            self._last_numeric_frame.pop(region.key(), None)
            self._generation += 1

    # -- per-frame processing -------------------------------------------
    def process_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        """Run recognition for all regions on one frame.

        Returns None (does nothing) if analysis is not currently running -
        callers must explicitly Start analysis; not every screen in a video
        is gameplay, so we never assume recognition should run continuously.
        """
        with self._score_lock, self._combo_lock, self._arrow_lock:
            boundary = self.process_game_end_frame(frame, frame_index, video_time_ms)
            gameplay = self._process_frame(
                frame, frame_index, video_time_ms,
                [region for region in self.regions if region.kind != "game_end"],
            )
            return _merge_frame_results([result for result in (boundary, gameplay) if result is not None])

    def process_numeric_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        with self._score_lock, self._combo_lock:
            regions = [region for region in self.regions if region.kind in ("score", "combo")]
            return self._process_frame(frame, frame_index, video_time_ms, regions)

    def process_background_numeric_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int,
        *, expected_session_id: Optional[str] = None,
        expected_generation: Optional[int] = None,
        region_keys: Optional[frozenset[str]] = None,
    ) -> Optional[FrameAnalysisResult]:
        session_id = expected_session_id if expected_session_id is not None else self.event_bus.session_id
        generation = expected_generation if expected_generation is not None else self._generation
        with self._score_lock, self._combo_lock:
            if session_id != self.event_bus.session_id or generation != self._generation:
                return None
            results = []
            score_regions = [
                region
                for region in self.regions
                if region.kind == "score" and (region_keys is None or region.key() in region_keys)
            ]
            score_result = self._process_frame(
                frame, frame_index, video_time_ms, score_regions
            )
            if score_result is not None:
                results.append(score_result)
            combo_regions = [
                region for region in self.regions
                if region.kind == "combo" and (region_keys is None or region.key() in region_keys)
            ]
            combo_result = self._process_frame(
                frame, frame_index, video_time_ms, combo_regions
            )
            if combo_result is not None:
                results.append(combo_result)
            return _merge_frame_results(results)

    def process_fast_score_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        """Numeric events require background OCR, never template-only guesses."""
        with self._score_lock:
            return self._process_frame(frame, frame_index, video_time_ms, [])

    def process_arrow_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        with self._arrow_lock:
            regions = [region for region in self.regions if region.kind == "arrow"]
            return self._process_frame(frame, frame_index, video_time_ms, regions)

    def process_game_end_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int,
    ) -> Optional[FrameAnalysisResult]:
        """Detect boundaries at video rate; drain OCR only when rotating a session."""
        if not self.is_running:
            return None
        start = time.perf_counter()
        results = []
        ended = False
        with self._arrow_lock:
            if frame_index <= self._last_game_end_frame:
                return None
            regions = [region for region in self.regions if region.kind == "game_end"]
            if not regions:
                return None
            initial_event = self.event_bus.ensure_session(frame_index, video_time_ms)
            self._last_game_end_frame = frame_index
            for region in regions:
                crop = _safe_crop(frame, region, frame.shape[1], frame.shape[0])
                references = self._arrow_references.get(region.key(), {}).get("ended", [])
                tracker: ArrowStateTracker = self._trackers[region.key()]
                error = None
                state = None
                score = None
                if crop.size == 0:
                    error = "Empty game-end crop (region out of frame bounds?)"
                elif not references:
                    error = "Capture a game-end reference before using this detector."
                else:
                    score = max(image_utils.similarity(crop, reference) for reference in references)
                    state = "ended" if score >= self.game_end_similarity_threshold else "idle"
                confirmed = tracker.observe(state)
                ended = ended or confirmed == "ended"
                if state == "idle" and tracker.status().pending_count >= self.game_end_confirm_frames:
                    self._game_end_active = False
                results.append(RegionFrameResult(
                    region=region, confidence=score, error=error, arrow_state=state,
                    is_unreadable=error is not None, crop=crop,
                    frame_index=frame_index, video_time_ms=video_time_ms,
                ))
        events = [initial_event] if initial_event is not None else []
        if ended and not self._game_end_active:
            with self._score_lock, self._combo_lock, self._arrow_lock:
                events.append(self.new_session(
                    frame_index, video_time_ms, reason="game_end", clear_events=False,
                ))
                self._game_end_active = True
        return FrameAnalysisResult(results, events, (time.perf_counter() - start) * 1000)

    def _process_frame(
        self,
        frame: np.ndarray,
        frame_index: int,
        video_time_ms: int,
        regions: List[Region],
    ) -> Optional[FrameAnalysisResult]:
        if not self.is_running:
            return None

        start_time = time.perf_counter()
        region_results: List[RegionFrameResult] = []
        initial_event = self.event_bus.ensure_session(frame_index, video_time_ms)
        new_events: List[Event] = [initial_event] if initial_event is not None else []

        if self._game_end_active:
            return FrameAnalysisResult([], new_events, 0)

        frame_h, frame_w = frame.shape[:2]

        for region in regions:
            crop = _safe_crop(frame, region, frame_w, frame_h)
            if region.kind == "arrow":
                result, event = self._process_arrow_region(region, crop, frame_index, video_time_ms)
                events = [event] if event is not None else []
            else:
                result, events = self._process_numeric_region(region, crop, frame_index, video_time_ms)
            result.frame_index = frame_index
            result.video_time_ms = video_time_ms
            region_results.append(result)
            new_events.extend(events)

        processing_ms = (time.perf_counter() - start_time) * 1000.0
        return FrameAnalysisResult(
            region_results=region_results, new_events=new_events, processing_ms=processing_ms
        )

    def _process_numeric_region(self, region, crop, frame_index, video_time_ms):
        tracker: TemporalValueTracker = self._trackers[region.key()]
        previous_frame = self._last_numeric_frame.get(region.key())
        if previous_frame is not None and (
            frame_index <= previous_frame[0] or video_time_ms <= previous_frame[1]
        ):
            return RegionFrameResult(
                region=region, accepted_value=tracker.accepted_value, crop=crop,
                validation_note="Waiting for a new video frame; repeated frames cannot confirm a value.",
            ), []
        self._last_numeric_frame[region.key()] = (frame_index, video_time_ms)
        template_verified = False
        if region.kind == "score":
            templates = self._score_templates.get(region.key(), {})
            ocr_result = self.ocr_engine.recognize_score_digits(crop)
            if self.uses_score_templates(region) and ocr_result.error is None:
                template_result = self.ocr_engine.recognize_score_from_templates(crop, templates)
                template_verified = (
                    template_result.error is None
                    and parse_digits(template_result.raw_text) is not None
                    and parse_digits(template_result.raw_text) == parse_digits(ocr_result.raw_text)
                )
        elif region.combo_fast_mode:
            ocr_result = self.ocr_engine.recognize_digits(
                crop, yellow_filter=region.combo_yellow_filter, fast_mode=True,
            )
        elif region.combo_yellow_filter:
            ocr_result = self.ocr_engine.recognize_digits(crop, yellow_filter=True)
        else:
            ocr_result = self.ocr_engine.recognize_digits(crop)
        max_digits = config.FIELD_MAX_DIGITS.get(region.kind)
        candidate = None
        parsed_candidate = None
        validation_error = None
        if ocr_result.error is None:
            candidate = parse_digits(ocr_result.raw_text, max_digits=max_digits)
            parsed_candidate = candidate
            if (
                candidate is not None
                and (
                    ocr_result.confidence is None
                    or (
                        ocr_result.confidence < (
                            config.COMBO_MIN_CONFIDENCE if region.kind == "combo"
                            else config.SCORE_EVIDENCE_MIN_CONFIDENCE
                        )
                        and not template_verified
                    )
                )
            ):
                validation_error = (
                    f"Ignored low-confidence {region.kind} reading {candidate}; "
                    "keeping the previous accepted value."
                )
                candidate = None
            if (
                region.kind == "score"
                and candidate is not None
                and tracker.accepted_value is not None
                and candidate < tracker.accepted_value
                and ocr_result.confidence < config.SCORE_DIRECT_ACCEPT_CONFIDENCE
            ):
                validation_error = (
                    f"Ignored low-confidence score correction {candidate}; "
                    f"corrections require confidence >= {config.SCORE_DIRECT_ACCEPT_CONFIDENCE:.2f}; "
                    f"keeping {tracker.accepted_value}."
                )
                candidate = None
            if (
                region.kind == "combo"
                and candidate is not None
                and tracker.accepted_value is not None
                and candidate >= tracker.accepted_value
            ):
                previous_time_ms = self._last_numeric_accept_time_ms.get(region.key(), video_time_ms)
                elapsed_ms = max(0, video_time_ms - previous_time_ms)
                max_increase = max(
                    config.COMBO_MIN_ALLOWED_INCREASE,
                    math.ceil(elapsed_ms * config.COMBO_MAX_INCREASE_PER_SECOND / 1000),
                )
                if not _is_plausible_combo_change(
                    tracker.accepted_value, candidate, max_increase
                ):
                    validation_error = (
                        f"Ignored implausible combo reading {candidate} while arrows obscure "
                        f"the digits; keeping {tracker.accepted_value}."
                    )
                    candidate = None

        if candidate is None:
            self._numeric_evidence.pop(region.key(), None)
        elif candidate == tracker.accepted_value:
            self._numeric_evidence.pop(region.key(), None)
        else:
            direct_threshold = (
                config.COMBO_MIN_CONFIDENCE if region.kind == "combo"
                else config.SCORE_DIRECT_ACCEPT_CONFIDENCE
            )
            high_confidence_reading = ocr_result.confidence >= direct_threshold
            is_decrease = (
                tracker.accepted_value is not None
                and candidate < tracker.accepted_value
            )
            required = max(
                self.required_consecutive_frames,
                config.NUMERIC_CONFIRMATION_SAMPLES
                if is_decrease or not high_confidence_reading else 1,
            )
            previous_value, count, first_time = self._numeric_evidence.get(
                region.key(), (candidate, 0, video_time_ms)
            )
            if previous_value != candidate or video_time_ms - first_time > config.NUMERIC_CONFIRMATION_WINDOW_MS:
                count, first_time = 0, video_time_ms
            count += 1
            self._numeric_evidence[region.key()] = (candidate, count, first_time)
            if count < required:
                validation_error = (
                    f"Waiting for repeated evidence for {region.kind} {candidate} "
                    f"({count}/{required})."
                )
                if is_decrease:
                    validation_error = (
                        f"Waiting for repeated evidence to lower {region.kind} from "
                        f"{tracker.accepted_value} to {candidate} ({count}/{required})."
                    )
                candidate = None
            else:
                self._numeric_evidence.pop(region.key(), None)
                if is_decrease:
                    validation_error = (
                        f"{region.kind.capitalize()} corrected from {tracker.accepted_value} to {candidate} "
                        f"after {count} matching high-confidence readings."
                    )

        events = []
        if (
            region.kind == "score" and parsed_candidate is not None
            and self.recording_enabled
            and self._last_observed_score.get(region.key()) != parsed_candidate
        ):
            observation = self.event_bus.make_event(
                event_type=config.SCORE_OBSERVED_EVENT_TYPE,
                payload={
                    "field": "score",
                    "region_label": region.label,
                    "observed_value": parsed_candidate,
                    "raw_text": ocr_result.raw_text,
                    "confirmed": False,
                    "accepted_value": tracker.accepted_value,
                    "validation_note": validation_error,
                },
                frame_index=frame_index,
                video_time_ms=video_time_ms,
                player=None if region.player == "Unknown" else region.player,
                confidence=ocr_result.confidence,
            )
            self.event_bus.record(observation)
            events.append(observation)
            self._last_observed_score[region.key()] = parsed_candidate
        change = tracker.observe(candidate)
        if change is not None:
            self._last_numeric_accept_time_ms[region.key()] = video_time_ms
        if change is not None and self.recording_enabled:
            previous_value, new_value = change
            event_type = config.FIELD_EVENT_TYPES[region.kind]
            player = None if region.player == "Unknown" else region.player
            event = self.event_bus.make_event(
                event_type=event_type,
                payload={
                    "field": region.kind,
                    "region_label": region.label,
                    "previous_value": previous_value,
                    "new_value": new_value,
                },
                frame_index=frame_index,
                video_time_ms=video_time_ms,
                player=player,
                confidence=ocr_result.confidence,
            )
            self.event_bus.record(event)
            events.append(event)

        result = RegionFrameResult(
            region=region,
            raw_text=ocr_result.raw_text,
            confidence=ocr_result.confidence,
            error=ocr_result.error,
            validation_note=validation_error,
            candidate_value=parsed_candidate,
            accepted_value=tracker.accepted_value,
            is_unreadable=parsed_candidate is None,
            crop=crop,
            needs_confirmation=region.key() in self._numeric_evidence,
        )
        return result, events

    def _process_arrow_region(self, region, crop, frame_index, video_time_ms):
        tracker: ArrowStateTracker = self._trackers[region.key()]
        references = self._arrow_references.get(region.key(), {})

        if crop is None or crop.size == 0:
            tracker.observe(None)
            result = RegionFrameResult(
                region=region,
                error="Empty crop (region out of frame bounds?)",
                is_unreadable=True,
                crop=crop,
            )
            return result, None

        note = None
        if not references:
            note = "No hit/miss reference captured yet for this lane - see the Capture buttons."

        state, score = classify_arrow_state(crop, references, self.arrow_similarity_threshold)
        confirmed = tracker.observe(state)

        event = None
        if confirmed is not None and self.recording_enabled:
            event_type = config.ARROW_EVENT_TYPES[confirmed]
            player = None if region.player == "Unknown" else region.player
            event = self.event_bus.make_event(
                event_type=event_type,
                payload={
                    "field": "arrow",
                    "region_label": region.label,
                    "direction": region.direction,
                    "state": confirmed,
                },
                frame_index=frame_index,
                video_time_ms=video_time_ms,
                player=player,
                confidence=score,
            )
            self.event_bus.record(event)

        result = RegionFrameResult(
            region=region,
            confidence=score,
            error=note,
            arrow_state=state,
            is_unreadable=False,
            crop=crop,
        )
        return result, event


def _is_plausible_combo_change(previous: int, candidate: int, max_increase: int) -> bool:
    return previous <= candidate <= previous + max_increase


def _safe_crop(frame: np.ndarray, region: Region, frame_w: int, frame_h: int) -> np.ndarray:
    x0 = max(0, min(region.x, frame_w))
    y0 = max(0, min(region.y, frame_h))
    x1 = max(0, min(region.x + region.w, frame_w))
    y1 = max(0, min(region.y + region.h, frame_h))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0, 3), dtype=frame.dtype)
    return frame[y0:y1, x0:x1]


def _merge_frame_results(results: List[FrameAnalysisResult]) -> Optional[FrameAnalysisResult]:
    if not results:
        return None
    return FrameAnalysisResult(
        region_results=[
            region_result
            for result in results
            for region_result in result.region_results
        ],
        new_events=[event for result in results for event in result.new_events],
        processing_ms=sum(result.processing_ms for result in results),
    )


def _has_complete_score_templates(
    templates: Dict[str, List[np.ndarray]], min_examples: int = 3,
) -> bool:
    return all(len(templates.get(digit, [])) >= min_examples for digit in "0123456789")
