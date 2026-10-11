"""Thin wrapper around OpenCV video capture with explicit seek detection.

The rest of the app should never touch cv2.VideoCapture directly - going
through this class means seeks are always detected in one place, which is
what lets recognition state be reset correctly and consistently.
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np


class VideoSource:
    def __init__(self, path: str):
        self.path = path
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise IOError(f"Could not open video file: {path}")

        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self._cap.get(cv2.CAP_PROP_FPS) or 0.0
        self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

        self._last_frame_index: Optional[int] = None  # last index actually delivered
        self._next_read_index = 0  # index that .read() will return next
        self._pending_seek = False  # set by seek_to_frame(), consumed by read_next()

    def close(self) -> None:
        self._cap.release()

    # -- reading -----------------------------------------------------
    def read_next(self) -> Tuple[Optional[np.ndarray], Optional[int], bool]:
        """Read the next sequential frame.

        Returns (frame, frame_index, is_seek). ``is_seek`` is True if this
        frame is not the immediate successor of the last one delivered
        (e.g. right after ``seek_to_frame``), so callers know to reset
        recognition tracking.
        """
        ok, frame = self._cap.read()
        if not ok:
            return None, None, False
        frame_index = self._next_read_index
        self._next_read_index += 1
        is_seek = self._pending_seek or (
            self._last_frame_index is not None and frame_index != self._last_frame_index + 1
        )
        self._pending_seek = False
        self._last_frame_index = frame_index
        return frame, frame_index, is_seek

    def seek_to_frame(self, frame_index: int) -> None:
        frame_index = max(0, min(frame_index, max(self.frame_count - 1, 0)))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        self._next_read_index = frame_index
        # Force the next read_next() call to be flagged as a seek, regardless
        # of whether the destination happens to be last_frame_index + 1.
        self._pending_seek = True

    def skip_to_frame(self, frame_index: int) -> None:
        """Move playback forward without treating dropped preview frames as a user seek."""
        frame_index = max(
            self._next_read_index,
            min(frame_index, max(self.frame_count - 1, 0)),
        )
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        self._next_read_index = frame_index
        self._pending_seek = False
        self._last_frame_index = frame_index - 1

    def current_frame_index(self) -> int:
        return self._next_read_index

    def is_at_end(self) -> bool:
        """True when the next read_next() would run past the last frame.

        Frame count can be unknown/zero for some containers, in which case
        this returns False so playback is never blocked by a bad count.
        """
        return self.frame_count > 0 and self._next_read_index >= self.frame_count

    def video_time_ms_for_frame(self, frame_index: int) -> int:
        if self.fps and self.fps > 0:
            return int(round((frame_index / self.fps) * 1000))
        return int(self._cap.get(cv2.CAP_PROP_POS_MSEC))
