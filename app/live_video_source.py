"""Named HDMI/USB capture with a single latest-frame slot and no GUI I/O."""
from __future__ import annotations

import math
from collections import deque
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from cv2_enumerate_cameras import enumerate_cameras

from app import config
from app.video_recording import VideoRecording

CAPTURE_STALL_SECONDS = 5.0


@dataclass(frozen=True)
class CaptureDevice:
    index: int
    name: str
    backend: int
    path: str

    @property
    def label(self) -> str:
        return f"{self.name} (device {self.index})"


def list_capture_devices() -> list[CaptureDevice]:
    """Enumerate DirectShow names and indices together without opening cameras."""
    return [
        CaptureDevice(device.index, device.name, device.backend, device.path)
        for device in enumerate_cameras(cv2.CAP_DSHOW)
    ]


class LiveVideoSource:
    source_mode = config.SOURCE_MODE_LIVE

    def __init__(self, device: CaptureDevice):
        self.device = device
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.frame_count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest: Optional[tuple[np.ndarray, int, int]] = None
        self._delivered_index = -1
        self._delivered_time_ms = 0
        self._last_received_at: Optional[float] = None
        self._error: Optional[str] = None
        self._capture_times: deque[int] = deque(maxlen=31)
        self._recording_lock = threading.Lock()
        self._recording: Optional[VideoRecording] = None
        self._thread = threading.Thread(target=self._capture, name="hdmi-capture", daemon=True)
        self._thread.start()

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def _capture(self) -> None:
        capture = None
        try:
            capture = cv2.VideoCapture(self.device.index, self.device.backend)
            if not capture.isOpened():
                raise OSError(
                    f"Could not open {self.device.name}. Close other capture apps and check "
                    "Windows camera permissions and the USB connection."
                )
            cam_link = "cam link" in self.device.name.lower()
            if cam_link:
                # The driver's default 640x480 mode can return only black frames.
                for prop, value in (
                    (cv2.CAP_PROP_FRAME_WIDTH, 1920),
                    (cv2.CAP_PROP_FRAME_HEIGHT, 1080),
                    (cv2.CAP_PROP_FPS, 30),
                ):
                    if not capture.set(prop, value):
                        raise OSError(
                            f"Could not select 1920x1080 at 30 fps on {self.device.name}. "
                            "Check its USB 3.x connection and reconnect to retry."
                        )
            fps = capture.get(cv2.CAP_PROP_FPS)
            self.fps = fps if math.isfinite(fps) and fps > 0 else 0.0
            if cam_link and self.fps and abs(self.fps - 30) > 0.5:
                raise OSError(
                    f"{self.device.name} reported {self.fps:g} fps instead of the requested 30 fps. "
                    "Reconnect the capture device and retry."
                )
            started = time.monotonic()
            index = 0
            while not self._stop.is_set():
                ok, frame = capture.read()
                if self._stop.is_set():
                    break
                if not ok or frame is None or frame.size == 0:
                    raise OSError(
                        f"No video frames from {self.device.name}. Check the HDMI signal, "
                        "USB connection, and whether another app is using the device. Reconnect to retry."
                    )
                height, width = frame.shape[:2]
                if cam_link and not self.width and (width, height) != (1920, 1080):
                    raise OSError(
                        f"{self.device.name} returned {width}x{height} instead of the requested "
                        "1920x1080. Reconnect and check the capture device's USB connection."
                    )
                if self.width and (width, height) != (self.width, self.height):
                    raise OSError("HDMI capture resolution changed. Reconnect and check your layout resolution.")
                self.width, self.height = width, height
                captured_at = time.monotonic()
                timestamp = round((captured_at - started) * 1000)
                with self._lock:
                    self._latest = (frame, index, timestamp)
                    self._last_received_at = captured_at
                    self._capture_times.append(timestamp)
                with self._recording_lock:
                    if self._recording is not None:
                        self._recording.write(frame, timestamp)
                index += 1
        except (OSError, cv2.error) as exc:
            with self._lock:
                self._error = str(exc)
        finally:
            self.stop_recording()
            if capture is not None:
                capture.release()

    def start_recording(self) -> VideoRecording:
        with self._recording_lock:
            if self._recording is not None:
                raise ValueError("Stop and save the current HDMI recording first.")
            with self._lock:
                if self._stop.is_set() or self._error is not None or not self.width:
                    raise ValueError("Connect a working HDMI input before recording.")
                if (
                    self._last_received_at is None
                    or time.monotonic() - self._last_received_at >= CAPTURE_STALL_SECONDS
                ):
                    raise ValueError("No current HDMI frames are available. Reconnect before recording.")
                fps = self.fps
                if not fps and len(self._capture_times) > 1:
                    elapsed = self._capture_times[-1] - self._capture_times[0]
                    fps = (len(self._capture_times) - 1) * 1000 / elapsed if elapsed > 0 else 0
                recording = VideoRecording(self.width, self.height, fps)
            self._recording = recording
            return recording

    def stop_recording(self) -> None:
        with self._recording_lock:
            if self._recording is not None:
                self._recording.stop()
                self._recording = None

    def read_next(self) -> tuple[Optional[np.ndarray], Optional[int], bool]:
        with self._lock:
            if self._stop.is_set():
                raise OSError("HDMI capture is stopping or disconnected. Reconnect before resuming.")
            if self._error is not None:
                raise OSError(self._error)
            if (
                self._last_received_at is not None
                and time.monotonic() - self._last_received_at >= CAPTURE_STALL_SECONDS
            ):
                raise OSError("No HDMI frames received for 5 seconds. Check the signal and reconnect to retry.")
            if self._latest is None or self._latest[1] == self._delivered_index:
                return None, None, False
            frame, index, timestamp = self._latest
            self._delivered_index = index
            self._delivered_time_ms = timestamp
            return frame, index, False

    def video_time_ms_for_frame(self, frame_index: int) -> int:
        with self._lock:
            if frame_index != self._delivered_index:
                raise ValueError("Only the most recently delivered HDMI frame has a retained timestamp.")
            return self._delivered_time_ms

    def current_frame_index(self) -> int:
        with self._lock:
            return self._delivered_index + 1

    def is_at_end(self) -> bool:
        return False

    def seek_to_frame(self, frame_index: int) -> None:
        raise ValueError("Live HDMI input cannot seek or rewind.")

    def skip_to_frame(self, frame_index: int) -> None:
        raise ValueError("Live HDMI input already delivers the latest frame.")

    def close(self) -> None:
        self._stop.set()
        self.stop_recording()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise OSError(
                f"{self.device.name} is still shutting down in its driver. "
                "Wait a moment and retry disconnecting or closing the app."
            )
