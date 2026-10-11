"""Tests for app.video_source: sequential reads vs. seeks are told apart
correctly, since that distinction drives recognition-state resets."""
from __future__ import annotations

import os
import sys

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
