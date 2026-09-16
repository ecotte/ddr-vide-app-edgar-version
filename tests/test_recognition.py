"""Tests for app.recognition: temporal consistency tracking and parsing."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.recognition import TemporalValueTracker, parse_digits


# ---------------------------------------------------------------------
# parse_digits format checking
# ---------------------------------------------------------------------

def test_parse_digits_extracts_plain_number():
    assert parse_digits("1234") == 1234


def test_parse_digits_strips_non_digit_characters():
    assert parse_digits("1,234") == 1234
    assert parse_digits(" 42 ") == 42


def test_parse_digits_empty_or_no_digits_is_unknown():
    assert parse_digits("") is None
    assert parse_digits("--") is None
    assert parse_digits("abc") is None


def test_parse_digits_rejects_over_max_digits():
    assert parse_digits("123456789012", max_digits=6) is None
    assert parse_digits("123456", max_digits=6) == 123456


# ---------------------------------------------------------------------
# TemporalValueTracker: invalid readings
# ---------------------------------------------------------------------

def test_unreadable_frame_never_becomes_zero_or_resets_accepted_value():
    tracker = TemporalValueTracker(required_consecutive_frames=2)
    assert tracker.observe(100) is None  # 1st of 2 needed
    change = tracker.observe(100)
    assert change == (None, 100)
    assert tracker.accepted_value == 100

    # Several unreadable frames in a row must not clear/zero the value.
    assert tracker.observe(None) is None
    assert tracker.observe(None) is None
    assert tracker.observe(None) is None
    assert tracker.accepted_value == 100


def test_unreadable_frame_does_not_emit_an_event():
    tracker = TemporalValueTracker(required_consecutive_frames=1)
    tracker.observe(50)
    result = tracker.observe(None)
    assert result is None


# ---------------------------------------------------------------------
# TemporalValueTracker: repeated frames of the same value
# ---------------------------------------------------------------------

def test_repeated_identical_frames_emit_change_only_once():
    tracker = TemporalValueTracker(required_consecutive_frames=2)
    events = [tracker.observe(v) for v in [100, 100, 100, 100, 100]]
    accepted_events = [e for e in events if e is not None]
    assert accepted_events == [(None, 100)]
    assert tracker.accepted_value == 100


def test_repeated_frames_after_acceptance_do_not_refire():
    tracker = TemporalValueTracker(required_consecutive_frames=1)
    first = tracker.observe(10)
    assert first == (None, 10)
    for _ in range(10):
        assert tracker.observe(10) is None


# ---------------------------------------------------------------------
# TemporalValueTracker: genuine value changes
# ---------------------------------------------------------------------

def test_genuine_change_requires_consecutive_confirmation():
    tracker = TemporalValueTracker(required_consecutive_frames=3)
    tracker.observe(10)
    tracker.observe(10)
    change = tracker.observe(10)
    assert change == (None, 10)

    assert tracker.observe(20) is None  # 1/3
    assert tracker.observe(20) is None  # 2/3
    change = tracker.observe(20)  # 3/3 -> accepted
    assert change == (10, 20)
    assert tracker.accepted_value == 20


def test_transient_digit_animation_glitch_is_rejected():
    tracker = TemporalValueTracker(required_consecutive_frames=2)
    tracker.observe(100)
    change = tracker.observe(100)
    assert change == (None, 100)

    # A single-frame glitch (e.g. mid-animation misread) must not flip
    # the accepted value, since it never repeats for 2 consecutive frames.
    assert tracker.observe(109) is None  # glitch, 1/2
    assert tracker.observe(100) is None  # back to normal, matches accepted -> no-op
    assert tracker.accepted_value == 100

    # Confirm a real change still works right after a rejected glitch.
    assert tracker.observe(200) is None  # 1/2
    change = tracker.observe(200)  # 2/2
    assert change == (100, 200)


# ---------------------------------------------------------------------
# TemporalValueTracker: reset behavior
# ---------------------------------------------------------------------

def test_reset_clears_accepted_value_to_unknown():
    tracker = TemporalValueTracker(required_consecutive_frames=1)
    tracker.observe(77)
    assert tracker.accepted_value == 77
    tracker.reset()
    assert tracker.accepted_value is None


def test_reset_causes_next_value_to_be_reported_as_change_from_unknown():
    tracker = TemporalValueTracker(required_consecutive_frames=1)
    tracker.observe(77)
    tracker.reset()
    # Even though 77 was already seen before reset, after a reset the next
    # accepted reading must be reported as a fresh change from "unknown".
    change = tracker.observe(77)
    assert change == (None, 77)


def test_reset_clears_pending_streak():
    tracker = TemporalValueTracker(required_consecutive_frames=3)
    tracker.observe(1)
    tracker.observe(1)  # 2/3 towards value 1, not yet accepted
    tracker.reset()
    status = tracker.status()
    assert status.accepted_value is None
    assert status.pending_value is None
    assert status.pending_count == 0
