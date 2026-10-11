"""Tests for app.video_source: sequential reads vs. seeks are told apart
correctly, since that distinction drives recognition-state resets."""
from __future__ import annotations

import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import pytest

from app.video_source import VideoSource


@pytest.fixture()
def small_video(tmp_path):
    path = str(tmp_path / "synthetic.mp4")
    width, height, fps, num_frames = 64, 48, 10.0, 10
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    for i in range(num_frames):
        frame = np.full((height, width, 3), fill_value=i * 20 % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path, num_frames


def test_open_missing_file_raises_ioerror(tmp_path):
    with pytest.raises(IOError):
        VideoSource(str(tmp_path / "does_not_exist.mp4"))


def test_sequential_reads_are_not_flagged_as_seeks(small_video):
    path, _ = small_video
    src = VideoSource(path)
    try:
        frame, idx, is_seek = src.read_next()
        assert frame is not None
        assert idx == 0
        assert is_seek is False  # first frame of a fresh video is not a seek

        frame, idx, is_seek = src.read_next()
        assert idx == 1
        assert is_seek is False
    finally:
        src.close()


def test_seek_to_frame_flags_next_read_as_seek(small_video):
    path, _ = small_video
    src = VideoSource(path)
    try:
        src.read_next()  # frame 0
        src.read_next()  # frame 1
        src.seek_to_frame(5)
        frame, idx, is_seek = src.read_next()
        assert frame is not None
        assert idx == 5
        assert is_seek is True
    finally:
        src.close()


def test_seek_backward_to_frame_zero_is_still_flagged_as_seek(small_video):
    path, _ = small_video
    src = VideoSource(path)
    try:
        src.read_next()
        src.read_next()
        src.read_next()
        src.seek_to_frame(0)
        frame, idx, is_seek = src.read_next()
        assert idx == 0
        assert is_seek is True
    finally:
        src.close()


def test_read_past_end_returns_none(small_video):
    path, num_frames = small_video
    src = VideoSource(path)
    try:
        for _ in range(num_frames):
            frame, idx, _ = src.read_next()
            assert frame is not None
        frame, idx, is_seek = src.read_next()
        assert frame is None
        assert idx is None
    finally:
        src.close()


def test_is_at_end_becomes_true_after_last_frame_and_resets_on_seek(small_video):
    path, num_frames = small_video
    src = VideoSource(path)
    try:
        assert src.is_at_end() is False
        for _ in range(num_frames):
            src.read_next()
        assert src.is_at_end() is True  # read all frames; next read would run past the end
        src.seek_to_frame(0)
        assert src.is_at_end() is False  # seeking back makes playback possible again
    finally:
        src.close()


def test_skip_to_frame_drops_preview_frames_without_reporting_user_seek(small_video):
    path, _ = small_video
    src = VideoSource(path)
    try:
        src.read_next()
        src.skip_to_frame(5)
        frame, idx, is_seek = src.read_next()

        assert frame is not None
        assert idx == 5
        assert is_seek is False
    finally:
        src.close()


def test_preview_skip_preserves_pending_explicit_seek(small_video):
    path, _ = small_video
    src = VideoSource(path)
    try:
        src.read_next()
        src.seek_to_frame(0)
        src.skip_to_frame(2)
        frame, idx, is_seek = src.read_next()
        assert frame is not None
        assert idx == 2
        assert is_seek is True
        _, idx, is_seek = src.read_next()
        assert idx == 3
        assert is_seek is False
    finally:
        src.close()


@pytest.mark.parametrize("gap", [0, 1, 8, 9])
def test_preview_skip_uses_grab_for_small_gaps_and_seek_for_large_gaps(gap):
    src = VideoSource.__new__(VideoSource)
    src._cap = Mock()
    src._cap.grab.return_value = True
    src._cap.set.return_value = True
    src._cap.read.return_value = (True, np.zeros((2, 2, 3), dtype=np.uint8))
    src.frame_count = 100
    src._next_read_index = 1
    src._last_frame_index = 0
    src._pending_seek = True

    src.skip_to_frame(1 + gap)
    assert src.current_frame_index() == 1 + gap
    assert src._cap.grab.call_count == (gap if gap <= 8 else 0)
    if gap > 8:
        src._cap.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 1 + gap)
    else:
        src._cap.set.assert_not_called()
    assert src.read_next()[1:] == (1 + gap, True)
    assert src.read_next()[1:] == (2 + gap, False)


def test_small_skip_delivers_same_pixels_as_sequential_read(small_video):
    path, _ = small_video
    src = VideoSource(path)
    sequential = VideoSource(path)
    try:
        src.read_next()
        src.skip_to_frame(5)
        actual, index, is_seek = src.read_next()
        for _ in range(6):
            expected, _, _ = sequential.read_next()
        np.testing.assert_array_equal(actual, expected)
        assert (index, is_seek) == (5, False)
    finally:
        src.close()
        sequential.close()


@pytest.mark.parametrize("gap", [2, 9])
def test_failed_preview_skip_is_reported_without_claiming_target_frame(gap):
    src = VideoSource.__new__(VideoSource)
    src._cap = Mock()
    src._cap.grab.side_effect = [True, False]
    src._cap.set.return_value = False
    src.frame_count = 100
    src._next_read_index = 1
    src._last_frame_index = 0
    src._pending_seek = True
    with pytest.raises(IOError, match="catching up playback"):
        src.skip_to_frame(1 + gap)
    assert src.current_frame_index() == (2 if gap == 2 else 1)
    assert src._pending_seek
