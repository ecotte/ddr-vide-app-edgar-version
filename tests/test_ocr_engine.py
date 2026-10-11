"""Tests for numeric OCR detection grouping."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ocr_engine import _first_digit, _select_numeric_group


def _detection(left, right, text, confidence=0.9, top=0, bottom=20):
    return ([[left, top], [right, top], [right, bottom], [left, bottom]], text, confidence)


def test_numeric_group_ignores_isolated_digit_shapes_beyond_spaces():
    detections = [
        _detection(0, 10, "7"),
        _detection(40, 90, "123"),
        _detection(120, 130, "8"),
    ]

    selected = _select_numeric_group(detections, image_width=130)

    assert [detection[1] for detection in selected] == ["123"]


def test_numeric_group_keeps_adjacent_split_digits_together():
    detections = [
        _detection(30, 40, "1"),
        _detection(45, 55, "2"),
        _detection(60, 70, "3"),
        _detection(110, 120, "7"),
    ]

    selected = _select_numeric_group(detections, image_width=140)

    assert [detection[1] for detection in selected] == ["1", "2", "3"]


def test_numeric_group_prefers_higher_confidence_when_lengths_tie():
    detections = [
        _detection(10, 30, "12", confidence=0.4),
        _detection(70, 90, "34", confidence=0.95),
    ]

    selected = _select_numeric_group(detections, image_width=100)

    assert [detection[1] for detection in selected] == ["34"]


def test_single_score_glyph_uses_only_first_digit_from_duplicated_reading():
    assert _first_digit("68") == "6"
