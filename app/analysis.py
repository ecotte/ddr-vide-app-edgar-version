"""Ties video frames + regions + OCR/recognition + events together.

This is the one place that knows the full per-frame pipeline. The UI layer
calls into this; it does not talk to OCR, image-similarity, or recognition
modules directly.

Region kinds:
  * "score" / "combo" - OCR + TemporalValueTracker (a value must hold
    steady for N frames before being accepted).
  * "arrow" - image-similarity classification against user-captured
    reference snapshots + ArrowStateTracker (an edge-triggered detector,
    since activity flashes are often brief rather than steady). Outcomes
    require a matching player judgment; activity alone cannot emit hits/misses.
  * "game_end" - a shared reference-matched end indicator that rotates
    game GUIDs once per confirmed appearance.
  * "judgment" - per-player Perfect/Great/Good/Miss reference pulses correlated with
    arrow activity; uncertain directions remain player-level outcomes.
"""
from __future__ import annotations

import math
import threading
import uuid
import time
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import numpy as np

from app import config, image_utils
from app.event_bus import EventBus
from app.models import Event, Region, share_player_references, shared_reference_source
from app.ocr_engine import OcrEngine
from app.recognition import (
    ArrowStateTracker, TemporalValueTracker, classify_arrow_state, match_judgment, parse_digits,
)

Tracker = Union[TemporalValueTracker, ArrowStateTracker]


@dataclass
class JudgmentEvidence:
    region: Region
    state: str
    onset: int
    confidence: float


@dataclass
class LaneEvidence:
    region: Region
    onset: int
    confidence: float


@dataclass
class RegionFrameResult:
    region: Region
    raw_text: str = ""
    confidence: Optional[float] = None  # OCR confidence, or arrow similarity score
    error: Optional[str] = None         # OCR error, or an arrow calibration/crop note
    validation_note: Optional[str] = None  # numeric reading pending or rejected by acceptance rules
    candidate_value: Optional[int] = None  # score/combo only
    accepted_value: Optional[int] = None   # score/combo only
    arrow_state: Optional[str] = None      # activity, judgment tier, ended, or idle
    is_unreadable: bool = False
    crop: Optional[np.ndarray] = None  # kept only for UI preview, not required for logic
    needs_confirmation: bool = False
    frame_index: Optional[int] = None
    video_time_ms: Optional[int] = None
    judgment_diagnostics: Optional[str] = None


@dataclass
class FrameAnalysisResult:
    region_results: List[RegionFrameResult]
    new_events: List[Event]
    processing_ms: float

    @property
    def needs_confirmation(self) -> bool:
        return any(result.needs_confirmation for result in self.region_results)


class AnalysisSession:
    def __init__(
        self, event_bus: EventBus, ocr_engine: Optional[OcrEngine] = None,
        *, shared_player_references: bool = False,
    ):
        self.event_bus = event_bus
        self.ocr_engine = ocr_engine or OcrEngine()
        self.regions: List[Region] = []
        self.shared_player_references = shared_player_references
        self._trackers: Dict[str, Tracker] = {}
        self._arrow_references: Dict[str, Dict[str, List[np.ndarray]]] = {}
        self._arrow_state_started: Dict[str, int] = {}
        self._last_arrow_frame: Dict[str, tuple[int, int]] = {}
        self._pending_judgments: List[JudgmentEvidence] = []
        self._judgment_absent_since: Dict[str, int] = {}
        self._pending_lanes: List[LaneEvidence] = []
        self._last_correlation_frame: Optional[tuple[int, int]] = None
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
        self.judgment_similarity_threshold = config.DEFAULT_JUDGMENT_SIMILARITY_THRESHOLD
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
            if self.shared_player_references:
                share_player_references(self.regions)
            self._trackers = {}
            self._arrow_references = {}
            self._arrow_state_started.clear()
            self._last_arrow_frame.clear()
            self._clear_correlation()
            self._score_templates = {}
            self._last_numeric_accept_time_ms = {}
            self._numeric_evidence = {}
            self._last_numeric_frame = {}
            self._last_observed_score = {}
            for r in self.regions:
                if r.kind in ("arrow", "judgment", "game_end"):
                    confirm_frames = (
                        self.game_end_confirm_frames if r.kind == "game_end"
                        else self.arrow_min_confirm_frames
                    )
                    self._trackers[r.key()] = ArrowStateTracker(
                        confirm_frames, require_idle=r.kind == "judgment",
                    )
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
                    if r.kind in ("score", "combo"):
                        self.update_score_templates(r)

    def set_required_consecutive_frames(self, n: int) -> None:
        with self._score_lock, self._combo_lock:
            self.required_consecutive_frames = n
            self._numeric_evidence.clear()

    def set_arrow_similarity_threshold(self, value: float) -> None:
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Arrow similarity must be a finite number between 0 and 1.")
        with self._arrow_lock:
            self.arrow_similarity_threshold = value

    def set_judgment_similarity_threshold(self, value: float) -> None:
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Judgment similarity must be a finite number between 0 and 1.")
        with self._arrow_lock:
            self.judgment_similarity_threshold = value

    def set_arrow_min_confirm_frames(self, n: int) -> None:
        with self._arrow_lock:
            self.arrow_min_confirm_frames = n
            for region in self.regions:
                tracker = self._trackers[region.key()]
                if region.kind in ("arrow", "judgment") and isinstance(tracker, ArrowStateTracker):
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

    def try_reset_tracking(self) -> bool:
        """Reset only when recognition locks are available without waiting."""
        with ExitStack() as stack:
            for lock in (self._score_lock, self._combo_lock, self._arrow_lock):
                if not lock.acquire(blocking=False):
                    return False
                stack.callback(lock.release)
            self.reset_tracking()
            return True

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
            self._arrow_state_started.clear()
            self._last_arrow_frame.clear()
            self._clear_correlation()

    def set_recording_enabled(self, enabled: bool) -> None:
        with self._arrow_lock:
            self.recording_enabled = enabled
            self._clear_correlation()

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
            if clear_events or reason == "manual":
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
            self._clear_correlation()

    def tracker_for(self, region: Region) -> Tracker:
        if region.kind in ("arrow", "judgment", "game_end"):
            lock = self._arrow_lock
        elif region.kind == "score":
            lock = self._score_lock
        else:
            lock = self._combo_lock
        with lock:
            return self._trackers[region.key()]

    def has_complete_score_templates(self, region: Region) -> bool:
        with self._combo_lock if region.kind == "combo" else self._score_lock:
            return self.uses_score_templates(region) and _has_complete_score_templates(
                self._score_templates.get(region.key(), {}), min_examples=1
            )

    def uses_score_templates(self, region: Region) -> bool:
        with self._combo_lock if region.kind == "combo" else self._score_lock:
            return (
                region.digit_templates_reviewed
                and region.score_template_version == config.DIGIT_TEMPLATE_VERSIONS[region.kind]
                and any(
                    self._score_templates.get(region.key(), {}).values()
                )
            )

    def update_score_templates(self, region: Region) -> None:
        """Refresh calibration without resetting accepted values or event history."""
        with self._combo_lock if region.kind == "combo" else self._score_lock:
            if self.shared_player_references:
                source = shared_reference_source(region, self.regions)
                source.digit_templates = {
                    digit: list(images) for digit, images in region.digit_templates.items()
                }
                source.digit_templates_reviewed = region.digit_templates_reviewed
                source.score_template_version = region.score_template_version
                share_player_references(self.regions)
                targets = [
                    other for other in self.regions
                    if shared_reference_source(other, self.regions) is source
                ]
            else:
                targets = [region]
            for target in targets:
                decoded = {}
                for digit, examples in target.digit_templates.items():
                    images = []
                    for encoded in examples:
                        image = image_utils.decode_base64_png_to_crop(encoded)
                        if image is None or image.shape[:2] != (32, 24):
                            raise ValueError(f"Invalid {target.kind} template for digit {digit}.")
                        images.append(image[:, :, 0])
                    decoded[digit] = images
                self._score_templates[target.key()] = decoded
                self._numeric_evidence.pop(target.key(), None)
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
            regions = [region for region in self.regions if region.kind in ("arrow", "judgment")]
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
                previous_count = len(self.event_bus.events)
                self.new_session(
                    frame_index, video_time_ms, reason="game_end", clear_events=False,
                )
                events.extend(self.event_bus.events[previous_count:])
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
        correlating = any(region.kind in ("arrow", "judgment") for region in regions)
        if correlating:
            previous = self._last_correlation_frame
            if previous and (frame_index < previous[0] or video_time_ms < previous[1]):
                self._clear_correlation()
                for region in regions:
                    if region.kind in ("arrow", "judgment"):
                        self._trackers[region.key()].reset()
                self._last_arrow_frame.clear()
            self._last_correlation_frame = (frame_index, video_time_ms)

        for region in regions:
            crop = _safe_crop(frame, region, frame_w, frame_h)
            if region.kind == "arrow":
                result, event = self._process_arrow_region(region, crop, frame_index, video_time_ms)
                events = [event] if event is not None else []
            elif region.kind == "judgment":
                result = self._process_judgment_region(region, crop, frame_index, video_time_ms)
                events = []
            else:
                result, events = self._process_numeric_region(region, crop, frame_index, video_time_ms)
            result.frame_index = frame_index
            result.video_time_ms = video_time_ms
            region_results.append(result)
            new_events.extend(events)
        if correlating:
            outcomes = self._correlate_judgments(frame_index, video_time_ms)
            new_events.extend(outcomes)
            for result in region_results:
                if result.region.kind == "judgment":
                    player_outcomes = [event for event in outcomes if event.player == result.region.player]
                    if player_outcomes:
                        event = player_outcomes[-1]
                        directions = event.payload["step_directions"]
                        judgment = event.payload.get("judgment") or event.payload["state"]
                        result.validation_note = (
                            f"Counted {judgment.upper()}: {', '.join(directions) or 'direction unknown'}."
                        )

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
            ocr_result = self.ocr_engine.recognize_score_digits(crop)
        elif region.combo_fast_mode:
            ocr_result = self.ocr_engine.recognize_digits(
                crop, yellow_filter=region.combo_yellow_filter, fast_mode=True,
            )
        elif region.combo_yellow_filter:
            ocr_result = self.ocr_engine.recognize_digits(crop, yellow_filter=True)
        else:
            ocr_result = self.ocr_engine.recognize_digits(crop)
        if self.uses_score_templates(region) and ocr_result.error is None:
            template_result = self.ocr_engine.recognize_score_from_templates(
                crop, self._score_templates.get(region.key(), {}),
                **({"kind": "combo"} if region.kind == "combo" else {}),
            )
            template_verified = (
                template_result.error is None
                and parse_digits(template_result.raw_text) is not None
                and parse_digits(template_result.raw_text) == parse_digits(ocr_result.raw_text)
            )
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
                    **({"value": new_value} if region.kind == "combo" else {
                        "previous_value": previous_value, "new_value": new_value,
                    }),
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

    def _clear_correlation(self) -> None:
        self._pending_judgments.clear()
        self._judgment_absent_since.clear()
        self._pending_lanes.clear()
        self._last_correlation_frame = None

    def _process_judgment_region(self, region, crop, frame_index, video_time_ms):
        tracker = self._trackers[region.key()]
        references = self._arrow_references.get(region.key(), {})
        previous = self._last_arrow_frame.get(region.key())
        error = None
        graded = any(references.get(state) for state in ("perfect", "great", "good"))
        categories = ("perfect", "great", "good", "miss") if graded or not references.get("hit") else ("hit", "miss")
        if not any(references.get(state) for state in categories):
            error = "No judgment outcome references. Capture a Perfect, Great, Good, or Miss example for this player."
        elif crop.size == 0:
            error = "Empty judgment crop (region out of frame bounds?)"
        match = match_judgment(crop, references, self.judgment_similarity_threshold) if error is None else None
        state, score = (match.state, match.similarity) if match is not None else (None, 0.0)
        latched_before = tracker.status().active_state
        repeated_frame = previous == (frame_index, video_time_ms)
        confirmed = None
        if previous != (frame_index, video_time_ms):
            self._last_arrow_frame[region.key()] = (frame_index, video_time_ms)
            observation = state
            if state == "idle":
                absent_since = self._judgment_absent_since.setdefault(region.key(), video_time_ms)
                observation = None
                if video_time_ms - absent_since >= config.JUDGMENT_REARM_MS:
                    tracker.reset()
            else:
                self._judgment_absent_since.pop(region.key(), None)
            if tracker.status().pending_state != observation:
                self._arrow_state_started[region.key()] = video_time_ms
            confirmed = tracker.observe(observation)
            if confirmed in ("perfect", "great", "good", "hit", "miss") and self.recording_enabled:
                self._pending_judgments.append(JudgmentEvidence(
                    region, confirmed, self._arrow_state_started[region.key()], score,
                ))
        note = (
                "Waiting for arrow correlation."
                if any(item.region.key() == region.key() for item in self._pending_judgments)
                else "Ambiguous judgment; holding previous state."
                if state is None and error is None
                else f"No judgment match; waiting for {config.JUDGMENT_REARM_MS} ms of video time to rearm."
                if state == "idle" and tracker.status().active_state is not None
                else "Ready for the next judgment." if state == "idle"
                else f"Holding judgment; waiting for {config.JUDGMENT_REARM_MS} ms without a match before counting again."
                if state in ("perfect", "great", "good", "hit", "miss") else None
        )
        if error is None:
            missing = [name.capitalize() for name in categories if not references.get(name)]
            warnings = []
            if missing:
                warnings.append(f"Untaught judgments: {', '.join(missing)}; taught outcomes still count.")
            note = " ".join([*warnings, *([note] if note else [])]) or None
        status = tracker.status()
        absent_since = self._judgment_absent_since.get(region.key())
        absent_ms = 0 if absent_since is None else max(0, video_time_ms - absent_since)
        if error is not None:
            decision = error
        elif repeated_frame:
            decision = "Repeated frame; confirmation and rearm do not advance."
        elif state == "idle":
            decision = "Below judgment threshold; no outcome match."
        elif state is None:
            decision = "Ambiguous match; top categories are too close. Rearm interval restarted."
        elif not self.recording_enabled:
            decision = "Recording off; match tracked but no judgment event queued."
        elif latched_before is not None:
            decision = f"Latch blocked {state.upper()}; previous {latched_before.upper()} has not rearmed."
        elif confirmed is None:
            decision = f"Confirming {state.upper()}: {status.pending_count}/{status.required_count} frames."
        else:
            decision = f"Accepted {state.upper()}; queued for judgment correlation."
        match_detail = (
            f"Best: {match.best_state.upper()} {score:.3f}; threshold {self.judgment_similarity_threshold:.3f}."
            if match is not None and match.best_state is not None else "Best: unavailable."
        )
        if match is not None and match.runner_up_similarity is not None and match.runner_up_state is not None:
            match_detail += (
                f" Next: {match.runner_up_state.upper()} {match.runner_up_similarity:.3f};"
                f" gap {score - match.runner_up_similarity:.3f}"
                f" (minimum {config.JUDGMENT_AMBIGUITY_MARGIN:.3f})."
            )
        diagnostics = (
            f"Frame {frame_index} | {video_time_ms} ms. {match_detail}\n"
            f"{decision}\n"
            f"Latch: {status.active_state or 'ready'}; no-match {absent_ms}/{config.JUDGMENT_REARM_MS} ms;"
            f" confirmation {status.pending_count}/{status.required_count};"
            f" recording {'on' if self.recording_enabled else 'off'}."
        )
        return RegionFrameResult(
            region=region, crop=crop, arrow_state=state, confidence=score,
            error=error, is_unreadable=error is not None, validation_note=note,
            judgment_diagnostics=diagnostics,
        )

    def _correlate_judgments(self, frame_index, video_time_ms):
        lookback = config.JUDGMENT_CORRELATION_LOOKBACK_MS
        lookahead = config.JUDGMENT_CORRELATION_LOOKAHEAD_MS
        settle = lookback + lookahead
        events = []
        pending = list(self._pending_judgments)
        consumed = set()
        for judgment in pending:
            # The latest matching arrow can be claimed by a judgment up to lookback later.
            if video_time_ms < judgment.onset + settle:
                continue
            nearby = [
                (index, lane) for index, lane in enumerate(self._pending_lanes)
                if lane.region.player == judgment.region.player
                and -lookback <= lane.onset - judgment.onset <= lookahead
            ]
            unique = bool(nearby) and all(
                index not in consumed and sum(
                    other.region.player == judgment.region.player
                    and -lookback <= lane.onset - other.onset <= lookahead
                    for other in pending
                ) == 1
                for index, lane in nearby
            )
            directions = [lane.region.direction for _, lane in nearby]
            simultaneous = bool(nearby) and (
                len(set(directions)) == len(directions)
                and max(lane.onset for _, lane in nearby) - min(lane.onset for _, lane in nearby)
                <= config.SIMULTANEOUS_STEP_WINDOW_MS
            )
            lanes = [lane for _, lane in nearby] if unique and simultaneous else []
            consumed.update(index for index, _ in nearby)
            outcome = "miss" if judgment.state == "miss" else "hit"
            judgment_id = str(uuid.uuid4())
            step_directions = [lane.region.direction for lane in lanes]
            payload = {
                "field": "judgment", "region_label": judgment.region.label,
                "direction": step_directions[0] if len(step_directions) == 1 else None,
                "outcome": outcome, "state": outcome,
                "judgment": judgment.state,
                "judgment_id": judgment_id,
                "step_directions": step_directions,
                "step_arrow_count": len(lanes),
                "evidence": "judgment_and_arrow" if lanes else "judgment_only",
                "judgment_video_time_ms": judgment.onset,
                "correlation_window_ms": max(lookback, lookahead),
                "correlation_lookback_ms": lookback,
                "correlation_lookahead_ms": lookahead,
            }
            judgement_event = self.event_bus.make_event(
                event_type=config.JUDGEMENT_EVENT_TYPE,
                payload=payload,
                frame_index=frame_index, video_time_ms=video_time_ms,
                player=judgment.region.player, confidence=judgment.confidence,
            )
            self.event_bus.record(judgement_event)
            events.append(judgement_event)
            self._pending_judgments.remove(judgment)
        self._pending_lanes = [
            lane for index, lane in enumerate(self._pending_lanes)
            if index not in consumed and video_time_ms - lane.onset <= lookback + settle
        ]
        return events

    def _process_arrow_region(self, region, crop, frame_index, video_time_ms):
        key = region.key()
        tracker: ArrowStateTracker = self._trackers[key]
        references = self._arrow_references.get(key, {})
        previous_frame = self._last_arrow_frame.get(key)
        if previous_frame and (
            frame_index < previous_frame[0] or video_time_ms < previous_frame[1]
        ):
            tracker.reset()
            self._arrow_state_started.pop(key, None)
        self._last_arrow_frame[key] = (frame_index, video_time_ms)

        if crop is None or crop.size == 0:
            tracker.observe(None)
            result = RegionFrameResult(
                region=region,
                error="Empty crop (region out of frame bounds?)",
                is_unreadable=True,
                crop=crop,
            )
            return result, None

        if not references.get("active"):
            tracker.observe(None)
            return RegionFrameResult(
                region=region, crop=crop, arrow_state=None, is_unreadable=True,
                error="Capture a fresh Active Arrow reference for this player and direction. "
                "Old Hit/Miss references are ignored.",
            ), None

        has_judgment = any(r.kind == "judgment" and r.player == region.player for r in self.regions)
        note = None if has_judgment else (
            f"Activity only. Add and calibrate a judgment box for {region.player} to determine outcomes and score."
        )

        state, score = classify_arrow_state(crop, references, self.arrow_similarity_threshold)
        empty_note = (
            "Empty reference wins or is too close to Active; treating this arrow as idle."
            if state == "idle" and references.get("empty") and score >= self.arrow_similarity_threshold
            else None
        )
        if previous_frame == (frame_index, video_time_ms):
            return RegionFrameResult(
                region=region, confidence=score, arrow_state=state, crop=crop,
                validation_note=empty_note or note,
            ), None
        if tracker.status().pending_state != state:
            self._arrow_state_started[key] = video_time_ms
        confirmed = tracker.observe(state)

        if confirmed is not None and self.recording_enabled:
            onset = self._arrow_state_started[key]
            if has_judgment:
                self._pending_lanes.append(LaneEvidence(region, onset, score))
            event = self.event_bus.make_event(
                event_type=config.ARROW_EVENT_TYPE,
                payload={
                    "field": "arrow", "region_label": region.label,
                    "direction": region.direction, "state": "active",
                    "evidence": "arrow_activity", "activity_video_time_ms": onset,
                },
                frame_index=frame_index, video_time_ms=video_time_ms,
                player=region.player, confidence=score,
            )
            self.event_bus.record(event)
            return RegionFrameResult(
                region=region, confidence=score, arrow_state=state, crop=crop,
                validation_note=note or "Arrow activity recorded; outcome requires a matching player judgment.",
            ), event

        result = RegionFrameResult(
            region=region,
            confidence=score,
            arrow_state=state,
            validation_note=empty_note or note,
            is_unreadable=False,
            crop=crop,
        )
        return result, None


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
