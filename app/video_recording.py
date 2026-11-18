"""Local MP4 encoding with bounded buffering, independent of preview delivery."""
from __future__ import annotations

import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import tempfile
import threading
import time

import cv2
import numpy as np


class VideoRecording:
    def __init__(self, width: int, height: int, fps: float):
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise ValueError("MP4 recording requires positive, even video dimensions.")
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Cannot determine recording FPS yet. Wait for more HDMI frames and retry.")
        descriptor, name = tempfile.mkstemp(prefix="ddr-hdmi-", suffix=".mp4")
        os.close(descriptor)
        self.path = Path(name)
        self.width, self.height, self.fps = width, height, fps
        self.error: str | None = None
        self.usable = False
        self._frames: Queue[tuple[np.ndarray, int]] = Queue(maxsize=4)
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._last_frame_at = time.monotonic()
        self._thread = threading.Thread(target=self._encode, name="hdmi-recording", daemon=True)
        self._thread.start()

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    @property
    def active(self) -> bool:
        return not self._stop.is_set() and not self.finished

    def write(self, frame: np.ndarray, timestamp_ms: int) -> None:
        if not self.active:
            return
        try:
            self._frames.put_nowait((frame.copy(), timestamp_ms))
            self._last_frame_at = time.monotonic()
        except Full:
            self.error = "Recording could not keep up with HDMI input. Recording stopped; save the partial MP4."
            self.stop()

    def stop(self) -> None:
        self._stop.set()

    def _encode(self) -> None:
        writer = None
        count = 0
        try:
            writer = cv2.VideoWriter(
                str(self.path), cv2.VideoWriter_fourcc(*"mp4v"),
                self.fps, (self.width, self.height),
            )
            if not writer.isOpened():
                raise OSError("Could not open the MP4 encoder. Check available disk space and MP4 codec support.")
            started_ms = None
            previous = None
            while True:
                try:
                    frame, timestamp_ms = self._frames.get(timeout=0.05)
                except Empty:
                    if self._stop.is_set():
                        break
                    if time.monotonic() - self._last_frame_at >= 5:
                        raise OSError("No HDMI recording frames received for 5 seconds. Save the partial MP4.")
                    continue
                if frame.shape != (self.height, self.width, 3) or frame.dtype != np.uint8:
                    raise OSError("HDMI recording received an unexpected frame format.")
                if started_ms is None:
                    started_ms = timestamp_ms
                slot = round((timestamp_ms - started_ms) * self.fps / 1000)
                if slot < count:
                    continue
                # MP4 has a fixed frame rate; retain elapsed time across capture gaps.
                while previous is not None and count < slot:
                    writer.write(previous)
                    count += 1
                writer.write(frame)
                previous = frame
                count += 1
        except (OSError, cv2.error) as exc:
            self.error = str(exc)
        finally:
            try:
                if writer is not None:
                    writer.release()
                if count:
                    video = cv2.VideoCapture(str(self.path))
                    try:
                        ok, frame = video.read()
                        self.usable = ok and frame is not None and frame.size > 0
                    finally:
                        video.release()
                if not self.usable:
                    self.error = self.error or "No playable HDMI video was recorded. Check the signal and disk space."
            except (OSError, cv2.error) as exc:
                self.error = str(exc)
            finally:
                self._finished.set()

    def save(self, destination: str) -> None:
        if not self.finished or not self.usable:
            raise OSError(self.error or "The HDMI recording has not finished encoding yet.")
        target = Path(destination)
        if target.suffix.lower() != ".mp4":
            raise OSError("Choose a filename ending in .mp4. The recording is still available to save.")
        if target.resolve() == self.path.resolve():
            return
        descriptor, name = tempfile.mkstemp(prefix=".ddr-save-", suffix=".mp4", dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            shutil.copyfile(self.path, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self.path.unlink()
