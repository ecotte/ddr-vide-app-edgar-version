"""Ties video frames + regions + OCR/recognition + events together.

This is the one place that knows the full per-frame pipeline. The UI layer
calls into this; it does not talk to OCR, image-similarity, or recognition
modules directly.

Two region kinds are supported:
  * "score" / "combo" - OCR + TemporalValueTracker (a value must hold
    steady for N frames before being accepted).
  * "arrow" - image-similarity classification against user-captured
    reference snapshots + ArrowStateTracker (an edge-triggered detector,
    since hit/miss flashes are often brief rather than steady).
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
    candidate_value: Optional[int] = None  # score/combo only
    accepted_value: Optional[int] = None   # score/combo only
    arrow_state: Optional[str] = None      # arrow only: "hit" / "miss" / "idle"
    is_unreadable: bool = False
    crop: Optional[np.ndarray] = None  # kept only for UI preview, not required for logic


@dataclass
class FrameAnalysisResult:
    region_results: List[RegionFrameResult]
    new_events: List[Event]
    processing_ms: float


class AnalysisSession:
    def __init__(self, event_bus: EventBus, ocr_engine: Optional[OcrEngine] = None):
        self.event_bus = event_bus
        self.ocr_engine = ocr_engine or OcrEngine()
        self.regions: List[Region] = []
        self._trackers: Dict[str, Tracker] = {}
        self._arrow_references: Dict[str, Dict[str, List[np.ndarray]]] = {}
        self._last_numeric_accept_time_ms: Dict[str, int] = {}
        self._numeric_lock = threading.RLock()
        self._arrow_lock = threading.RLock()
        self.required_consecutive_frames = config.DEFAULT_REQUIRED_CONSECUTIVE_FRAMES
        self.arrow_similarity_threshold = config.DEFAULT_ARROW_SIMILARITY_THRESHOLD
        self.arrow_min_confirm_frames = config.DEFAULT_ARROW_MIN_CONFIRM_FRAMES
        self.is_running = False
        self.recording_enabled = True

    # -- configuration -------------------------------------------------
    def set_regions(self, regions: List[Region]) -> None:
        """Replace the active region set. Always resets tracking state,
        since previous trackers no longer correspond 1:1 to new regions.

        Also (re)decodes any arrow reference images from the region data,
        so calling this again after capturing a new reference picks it up.
        """
        with self._numeric_lock, self._arrow_lock:
            self.regions = list(regions)
            self._trackers = {}
            self._arrow_references = {}
            self._last_numeric_accept_time_ms = {}
            for r in self.regions:
                if r.kind == "arrow":
                    self._trackers[r.key()] = ArrowStateTracker(self.arrow_min_confirm_frames)
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
                    self._trackers[r.key()] = TemporalValueTracker(self.required_consecutive_frames)

    def set_required_consecutive_frames(self, n: int) -> None:
        with self._numeric_lock:
            self.required_consecutive_frames = n
            for tracker in self._trackers.values():
                if isinstance(tracker, TemporalValueTracker):
                    tracker.required_consecutive_frames = n

    def set_arrow_similarity_threshold(self, value: float) -> None:
        with self._arrow_lock:
            self.arrow_similarity_threshold = value

    def set_arrow_min_confirm_frames(self, n: int) -> None:
        with self._arrow_lock:
            self.arrow_min_confirm_frames = n
            for tracker in self._trackers.values():
                if isinstance(tracker, ArrowStateTracker):
                    tracker.min_confirm_frames = n

    def reset_tracking(self) -> None:
        """Call on seek / new video load / new session."""
        with self._numeric_lock, self._arrow_lock:
            for tracker in self._trackers.values():
                tracker.reset()
            self._last_numeric_accept_time_ms = {}

    def start(self) -> None:
        with self._numeric_lock, self._arrow_lock:
            self.is_running = True

    def stop(self) -> None:
        with self._numeric_lock, self._arrow_lock:
            self.is_running = False

    def tracker_for(self, region: Region) -> Tracker:
        lock = self._arrow_lock if region.kind == "arrow" else self._numeric_lock
        with lock:
            return self._trackers[region.key()]

    # -- per-frame processing -------------------------------------------
    def process_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        """Run recognition for all regions on one frame.

        Returns None (does nothing) if analysis is not currently running -
        callers must explicitly Start analysis; not every screen in a video
        is gameplay, so we never assume recognition should run continuously.
        """
        with self._numeric_lock, self._arrow_lock:
            return self._process_frame(frame, frame_index, video_time_ms, self.regions)

    def process_numeric_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        with self._numeric_lock:
            regions = [region for region in self.regions if region.kind != "arrow"]
            return self._process_frame(frame, frame_index, video_time_ms, regions)

    def process_arrow_frame(
        self, frame: np.ndarray, frame_index: int, video_time_ms: int
    ) -> Optional[FrameAnalysisResult]:
        with self._arrow_lock:
            regions = [region for region in self.regions if region.kind == "arrow"]
            return self._process_frame(frame, frame_index, video_time_ms, regions)

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
        new_events: List[Event] = []

        frame_h, frame_w = frame.shape[:2]

        for region in regions:
            crop = _safe_crop(frame, region, frame_w, frame_h)
            if region.kind == "arrow":
                result, event = self._process_arrow_region(region, crop, frame_index, video_time_ms)
            else:
                result, event = self._process_numeric_region(region, crop, frame_index, video_time_ms)
            region_results.append(result)
            if event is not None:
                new_events.append(event)

        processing_ms = (time.perf_counter() - start_time) * 1000.0
        return FrameAnalysisResult(
            region_results=region_results, new_events=new_events, processing_ms=processing_ms
        )

    def _process_numeric_region(self, region, crop, frame_index, video_time_ms):
        tracker: TemporalValueTracker = self._trackers[region.key()]
        if region.kind == "score":
            ocr_result = self.ocr_engine.recognize_score_digits(crop)
        else:
            ocr_result = self.ocr_engine.recognize_digits(crop)
        max_digits = config.FIELD_MAX_DIGITS.get(region.kind)
        candidate = None
        validation_error = None
        if ocr_result.error is None:
            candidate = parse_digits(ocr_result.raw_text, max_digits=max_digits)
            if (
                region.kind == "score"
                and candidate is not None
                and (
                    ocr_result.confidence is None
                    or ocr_result.confidence < config.SCORE_MIN_GLYPH_CONFIDENCE
                )
            ):
                validation_error = (
                    f"Ignored low-confidence score reading {candidate}; "
                    "keeping the previous accepted score."
                )
                candidate = None
            if (
                region.kind == "score"
                and candidate is not None
                and tracker.accepted_value is not None
                and candidate < tracker.accepted_value
            ):
                validation_error = (
                    f"Ignored decreasing score reading {candidate}; "
                    f"keeping {tracker.accepted_value}."
                )
                candidate = None
            if region.kind == "combo" and candidate is not None and tracker.accepted_value is not None:
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

        event = None
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

        result = RegionFrameResult(
            region=region,
            raw_text=ocr_result.raw_text,
            confidence=ocr_result.confidence,
            error=ocr_result.error or validation_error,
            candidate_value=candidate,
            accepted_value=tracker.accepted_value,
            is_unreadable=candidate is None,
            crop=crop,
        )
        return result, event

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
    if candidate >= previous:
        return candidate - previous <= max_increase
    return candidate <= config.COMBO_RESET_MAX_VALUE


def _safe_crop(frame: np.ndarray, region: Region, frame_w: int, frame_h: int) -> np.ndarray:
    x0 = max(0, min(region.x, frame_w))
    y0 = max(0, min(region.y, frame_h))
    x1 = max(0, min(region.x + region.w, frame_w))
    y1 = max(0, min(region.y + region.h, frame_h))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0, 3), dtype=frame.dtype)
    return frame[y0:y1, x0:x1]
