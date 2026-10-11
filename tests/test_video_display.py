"""Tests for aspect-ratio-preserving video display fitting."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ui.video_tab import fit_display_size, next_playback_delay_ms


@pytest.mark.parametrize(
    ("source_width", "source_height", "max_width", "max_height"),
    [
        (1920, 1080, 700, 540),
        (1080, 1920, 700, 540),
        (1024, 768, 700, 540),
        (640, 480, 700, 540),
    ],
)
def test_fit_display_size_never_crops_or_upscales(
    source_width, source_height, max_width, max_height
):
    width, height, scale = fit_display_size(
        source_width, source_height, max_width, max_height
    )

    assert width <= max_width
    assert height <= max_height
    assert scale <= 1.0
    assert width == round(source_width * scale)
    assert height == round(source_height * scale)


def test_fit_display_size_uses_uniform_scale_for_wide_video():
    width, height, scale = fit_display_size(1920, 1080, 700, 540)

    assert (width, height) == (700, 394)
    assert scale == pytest.approx(700 / 1920)


def test_playback_delay_subtracts_time_already_spent_processing():
    assert next_playback_delay_ms(60.0, processing_seconds=0.010) == 7


def test_playback_delay_never_becomes_zero_when_processing_exceeds_interval():
    assert next_playback_delay_ms(60.0, processing_seconds=0.030) == 1
