"""Tests for numeric OCR detection grouping."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock

import numpy as np
import pytest
import cv2

from app.ocr_engine import OcrEngine, _extract_score_glyphs, _preprocess_for_ocr, _select_numeric_group, score_templates_from_label


def _detection(left, right, text, confidence=0.9, top=0, bottom=20):
    return ([[left, top], [right, top], [right, bottom], [left, bottom]], text, confidence)


def test_yellow_filter_suppresses_glow_and_arrows_without_changing_default():
    crop = np.zeros((200, 6, 3), dtype=np.uint8)
    crop[:] = [
        (0, 255, 255), (255, 255, 255), (255, 0, 255),
        (0, 0, 255), (240, 30, 40), (40, 170, 200),
    ]
    filtered = _preprocess_for_ocr(crop, yellow_filter=True)
    np.testing.assert_array_equal(filtered[0], [255, 0, 0, 0, 0, 130])
    np.testing.assert_array_equal(_preprocess_for_ocr(crop), cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))


@pytest.mark.parametrize("text,confidence", [("323", 0.7365), ("828", 0.99)])
def test_yellow_filter_keeps_actual_ocr_text_and_confidence(text, confidence):
    crop = np.full((180, 303, 3), (0, 255, 255), dtype=np.uint8)
    engine = OcrEngine()
    engine._reader = Mock()
    engine._reader.readtext.return_value = [_detection(0, 303, text, confidence)]
    result = engine.recognize_digits(crop, yellow_filter=True)
    assert result.raw_text == text
    assert result.confidence == confidence
    assert result.error is None
    np.testing.assert_array_equal(
        engine._reader.readtext.call_args.args[0],
        _preprocess_for_ocr(crop, yellow_filter=True),
    )


def test_yellow_filter_does_not_fall_back_to_white_glow():
    engine = OcrEngine()
    engine._reader = Mock()
    result = engine.recognize_digits(np.full((20, 30, 3), 255, dtype=np.uint8), yellow_filter=True)
    assert result.raw_text == ""
    assert result.confidence is None
    assert "No yellow digit signal" in result.error
    engine._reader.readtext.assert_not_called()


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


@pytest.mark.parametrize("text", ["68", "", "x"])
def test_ambiguous_score_glyph_is_rejected_without_fabricating_confidence(monkeypatch, text):
    crop = np.zeros((32, 24, 3), dtype=np.uint8)
    monkeypatch.setattr(
        "app.ocr_engine._extract_score_glyphs",
        lambda _: (crop[:, :, 0], [[0, 24, 0, 32]], [crop[:, :, 0]]),
    )
    engine = OcrEngine()
    engine._reader = Mock()
    engine._reader.recognize.return_value = [_detection(0, 24, text, 0.2)]

    result = engine.recognize_score_digits(crop)

    assert result.raw_text == text
    assert result.confidence == 0.2
    assert "expected exactly one digit" in result.error


def test_template_match_rejects_nearly_tied_digits(monkeypatch):
    glyph = np.full((32, 24), 255, dtype=np.uint8)
    other = glyph.copy()
    other[0, 0] = 0
    monkeypatch.setattr(
        "app.ocr_engine._extract_score_glyphs",
        lambda _: (glyph, [[0, 24, 0, 32]], [glyph]),
    )
    result = OcrEngine().recognize_score_from_templates(
        np.zeros((32, 24, 3), dtype=np.uint8),
        {"0": [glyph] * 3, "6": [other] * 3},
    )

    assert result.confidence == 1.0
    assert result.raw_text == "0"
    assert "Ambiguous score templates" in result.error


def test_template_match_accepts_distinct_digit(monkeypatch):
    glyph = np.zeros((32, 24), dtype=np.uint8)
    monkeypatch.setattr(
        "app.ocr_engine._extract_score_glyphs",
        lambda _: (glyph, [[0, 24, 0, 32]], [glyph]),
    )
    result = OcrEngine().recognize_score_from_templates(
        np.zeros((32, 24, 3), dtype=np.uint8),
        {"0": [glyph] * 3, "6": [np.full_like(glyph, 255)] * 3},
    )

    assert result.raw_text == "0"
    assert result.error is None


@pytest.mark.parametrize("text", ["6", "65", "123", "1234"])
def test_tightly_cropped_short_scores_are_segmented_and_recognized(text):
    image = np.zeros((50, 200, 3), dtype=np.uint8)
    cv2.putText(image, text, (5, 37), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    x, y, w, h = cv2.boundingRect(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    crop = image[y - 4:y + h + 4, x - 4:x + w + 4]
    _, boxes, glyphs = _extract_score_glyphs(crop)
    assert len(boxes) == len(glyphs) == len(text)
    assert "".join(digit for digit, _ in score_templates_from_label(crop, text)) == text
    engine = OcrEngine()
    engine._reader = Mock()
    engine._reader.recognize.return_value = [
        _detection(box[0], box[1], digit, 0.99)
        for box, digit in zip(boxes, text)
    ]
    result = engine.recognize_score_digits(crop)
    assert result.raw_text == text
    assert result.error is None
    assert engine._reader.recognize.call_args.kwargs["horizontal_list"] == boxes
