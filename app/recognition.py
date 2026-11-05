"""Recognition state tracking: turning raw OCR text into accepted values.

This module contains no OCR or UI code. It only knows how to take a
per-frame "candidate" reading (already parsed into a number, or None if
unreadable) and decide whether it should become the new accepted value.

Rules encoded here (see project README for the rationale):
  * An unreadable frame (candidate=None) never resets or overwrites the
    accepted value - the previous accepted value simply stands.
  * A new value is only accepted after it has been observed for
    ``required_consecutive_frames`` consecutive readable frames in a row.
    This rejects one-off digit-animation glitches at the cost of a small
    detection delay - a real, fast change can in theory be missed if it
    never holds steady for that many frames.
  * An accepted change is reported exactly once (when it crosses the
    threshold), not on every subsequent frame that still shows that value.
  * ``reset()`` must be called on seek / new video / new session, and
    afterwards the *next* accepted value - even if numerically identical to
    what was accepted before - is reported as a change from "unknown".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class TrackerStatus:
    """A snapshot of tracker state, useful for UI display."""

    accepted_value: Optional[int]
    pending_value: Optional[int]
    pending_count: int
    required_count: int
    last_raw_was_unreadable: bool


class TemporalValueTracker:
    """Debounces a stream of candidate integer readings into accepted values."""

    def __init__(self, required_consecutive_frames: int = 2):
        if required_consecutive_frames < 1:
            raise ValueError("required_consecutive_frames must be >= 1")
        self.required_consecutive_frames = required_consecutive_frames
        self._accepted_value: Optional[int] = None
        self._pending_value: Optional[int] = None
        self._pending_count: int = 0
        self._last_raw_was_unreadable: bool = False

    @property
    def accepted_value(self) -> Optional[int]:
        return self._accepted_value

    def status(self) -> TrackerStatus:
        return TrackerStatus(
            accepted_value=self._accepted_value,
            pending_value=self._pending_value,
            pending_count=self._pending_count,
            required_count=self.required_consecutive_frames,
            last_raw_was_unreadable=self._last_raw_was_unreadable,
        )

    def reset(self) -> None:
        self._accepted_value = None
        self._pending_value = None
        self._pending_count = 0
        self._last_raw_was_unreadable = False

    def observe(self, candidate: Optional[int]) -> Optional[Tuple[Optional[int], int]]:
        """Feed one frame's candidate reading into the tracker.

        Returns (previous_value, new_value) if this observation caused a
        newly accepted value, else None.
        """
        if candidate is None:
            # Unreadable this frame: previous accepted value stands as-is.
            # We deliberately do not touch the pending streak of a *different*
            # candidate either way; a single blank/unreadable frame among an
            # otherwise-consistent run of a new value will just be skipped
            # rather than restarting the count from zero.
            self._last_raw_was_unreadable = True
            return None

        self._last_raw_was_unreadable = False

        if candidate == self._accepted_value:
            # Already the accepted value: nothing to do, no event.
            self._pending_value = candidate
            self._pending_count += 1
            return None

        if candidate == self._pending_value:
            self._pending_count += 1
        else:
            self._pending_value = candidate
            self._pending_count = 1

        if self._pending_count >= self.required_consecutive_frames:
            previous = self._accepted_value
            self._accepted_value = candidate
            self._pending_value = None
            self._pending_count = 0
            return previous, candidate

        return None


def parse_digits(raw_text: str, max_digits: Optional[int] = None) -> Optional[int]:
    """Format-check + parse OCR text expected to represent a plain integer.

    Returns None (unreadable / invalid format) if no digits are present, or
    if the digit count exceeds ``max_digits`` (a sanity ceiling used to
    reject OCR text that bled in extra characters from neighboring UI).
    """
    digits = "".join(ch for ch in raw_text if ch.isdigit())
    if not digits:
        return None
    if max_digits is not None and len(digits) > max_digits:
        return None
    return int(digits)


@dataclass
class ArrowTrackerStatus:
    """A snapshot of arrow tracker state, useful for UI display."""

    active_state: Optional[str]        # "hit", "miss", "appeared", or None (idle)
    pending_state: Optional[str]
    pending_count: int
    required_count: int
    last_was_unreadable: bool


class ArrowStateTracker:
    """Edge-triggered detector for a single arrow lane's calibrated states.

    Unlike TemporalValueTracker (which debounces a *stable* value like a
    score), a hit/miss flash at an arrow receptor is often only visible for
    a frame or two. So instead of "accept once a value holds steady", this
    tracker fires an event once when the classified state *changes away
    from idle* (or changes from one active state directly to another), and
    suppresses repeats while that same state persists across frames - i.e.
    a debounced edge/pulse detector, not a stability filter.

    ``observe(state)`` takes "hit", "miss", "appeared", or "idle" (never invented here -
    see app.recognition.classify_arrow_state, which returns "idle" when no
    captured reference matches closely enough). An unreadable frame is
    represented by passing ``None``, which - as with TemporalValueTracker -
    never resets tracking state and never fires an event by itself.
    Judgment boxes also pass "perfect", "great", and "good"; ``require_idle=True`` blocks
    all subsequent outcomes until a confirmed idle, even if the word changes.
    """

    def __init__(self, min_confirm_frames: int = 1, *, require_idle: bool = False):
        if min_confirm_frames < 1:
            raise ValueError("min_confirm_frames must be >= 1")
        self.min_confirm_frames = min_confirm_frames
        self.require_idle = require_idle
        self._active_state: Optional[str] = None
        self._pending_state: Optional[str] = None
        self._pending_count: int = 0
        self._last_was_unreadable = False

    def status(self) -> ArrowTrackerStatus:
        return ArrowTrackerStatus(
            active_state=self._active_state,
            pending_state=self._pending_state,
            pending_count=self._pending_count,
            required_count=self.min_confirm_frames,
            last_was_unreadable=self._last_was_unreadable,
        )

    def reset(self) -> None:
        self._active_state = None
        self._pending_state = None
        self._pending_count = 0
        self._last_was_unreadable = False

    def observe(self, state: Optional[str]) -> Optional[str]:
        """Feed one frame's classified state ("hit"/"miss"/"appeared"/"idle"/None).

        Returns the newly confirmed non-idle state if this
        observation just triggered a new event, else None.
        """
        if state is None:
            self._last_was_unreadable = True
            return None
        self._last_was_unreadable = False

        if state == self._pending_state:
            self._pending_count += 1
        else:
            self._pending_state = state
            self._pending_count = 1

        if self._pending_count < self.min_confirm_frames:
            return None

        confirmed_state = self._pending_state
        if confirmed_state == "idle":
            self._active_state = None
            return None

        if self.require_idle and self._active_state is not None:
            return None

        if confirmed_state != self._active_state:
            self._active_state = confirmed_state
            return confirmed_state

        return None


def classify_arrow_state(
    crop_bgr,
    reference_images: dict,
    threshold: float,
):
    """Classify a live arrow-lane crop against captured hit/miss/appearance
    references. Returns (state, confidence):
      * state is "hit", "miss", or "appeared" if the best-matching reference's
        similarity is >= threshold,
      * otherwise "idle" (no confident match - the honest default, not a
        guess),
      * confidence is the best similarity score found (0.0 if no
        references were captured for this lane at all).

    This never assumes a color or animation meaning - it only compares
    pixels to whatever example(s) the user captured from their own footage.
    """
    from app import image_utils  # local import: keeps recognition.py OCR/image-lib free at import time

    best_state = None
    best_score = 0.0
    for state_name in ("hit", "miss", "appeared"):
        references = reference_images.get(state_name, [])
        if not isinstance(references, (list, tuple)):
            references = [references]
        if not references:
            continue
        for reference in references:
            score = image_utils.similarity(crop_bgr, reference)
            if score > best_score:
                best_score = score
                best_state = state_name

    if best_state is not None and best_score >= threshold:
        return best_state, best_score
    return "idle", best_score


def classify_judgment_state(crop_bgr, reference_images: dict, threshold: float):
    """Return idle for no outcome match, but None for ambiguous strong matches."""
    from app import image_utils

    ranked = sorted(
        (
            (max(image_utils.similarity(crop_bgr, image) for image in references), state)
            for state in (
                ("perfect", "great", "good", "miss")
                if any(reference_images.get(tier) for tier in ("perfect", "great", "good"))
                else ("hit", "miss")
            )
            if (references := reference_images.get(state, []))
        ),
        reverse=True,
    )
    if not ranked:
        return None, 0.0
    score, state = ranked[0]
    if score < threshold:
        return "idle", score
    if len(ranked) > 1 and score - ranked[1][0] < 0.02:
        return None, score
    return state, score
