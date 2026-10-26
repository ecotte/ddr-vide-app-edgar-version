"""Deterministic live-capture tests; never open a real camera."""
from queue import Queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from app.live_video_source import CaptureDevice, LiveVideoSource, list_capture_devices


DEVICE = CaptureDevice(1, "HDMI capture", cv2.CAP_DSHOW, "capture-device")


class FakeCapture:
    def __init__(self):
        self.frames = Queue()
        self.released = threading.Event()
        self.fps = 30.0
        self.opened = True

    def isOpened(self):
        return self.opened

    def get(self, prop):
        assert prop == cv2.CAP_PROP_FPS
        return self.fps

    def read(self):
        frame = self.frames.get(timeout=5)
        if isinstance(frame, cv2.error):
            raise frame
        return frame is not None, frame

    def release(self):
        self.released.set()


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Timed out waiting for simulated capture.")
        time.sleep(0.005)


@pytest.fixture
def capture(monkeypatch):
    driver = FakeCapture()
    factory = Mock(return_value=driver)
    monkeypatch.setattr("app.live_video_source.cv2.VideoCapture", factory)
    source = LiveVideoSource(DEVICE)
    try:
        yield source, driver, factory
    finally:
        source._stop.set()
        driver.frames.put(None)
        source.close()


def test_enumeration_preserves_named_backend_indices_without_opening_devices(monkeypatch):
    enumerate_devices = Mock(return_value=[
        SimpleNamespace(index=0, name="Built-in camera", backend=cv2.CAP_DSHOW, path="internal"),
        SimpleNamespace(index=1, name="Hagibis", backend=cv2.CAP_DSHOW, path="external"),
    ])
    factory = Mock()
    monkeypatch.setattr("app.live_video_source.enumerate_cameras", enumerate_devices)
    monkeypatch.setattr("app.live_video_source.cv2.VideoCapture", factory)
    devices = list_capture_devices()
    enumerate_devices.assert_called_once_with(cv2.CAP_DSHOW)
    factory.assert_not_called()
    assert devices[1] == CaptureDevice(1, "Hagibis", cv2.CAP_DSHOW, "external")
    assert devices[1].label == "Hagibis (device 1)"


def test_capture_delivers_latest_frame_only_and_keeps_capture_gaps_out_of_seek_logic(capture):
    source, driver, factory = capture
    for index in range(20):
        driver.frames.put(np.full((24, 32, 3), index, dtype=np.uint8))
    wait_for(lambda: source._latest is not None and source._latest[1] == 19)
    frame, index, seek = source.read_next()
    factory.assert_called_once_with(DEVICE.index, DEVICE.backend)
    assert (index, seek) == (19, False)
    assert frame.mean() == 19
    assert (source.width, source.height, source.frame_count) == (32, 24, 0)
    assert source.source_mode == "live"
    assert source.current_frame_index() == 20
    assert source.read_next() == (None, None, False)
    assert not source.is_at_end()


def test_timestamps_follow_elapsed_capture_time_not_reported_fps(capture):
    source, driver, _ = capture
    driver.frames.put(np.zeros((24, 32, 3), dtype=np.uint8))
    wait_for(lambda: source._latest is not None)
    _, index, _ = source.read_next()
    first_ms = source.video_time_ms_for_frame(index)
    time.sleep(0.04)
    driver.frames.put(np.zeros((24, 32, 3), dtype=np.uint8))
    wait_for(lambda: source._latest[1] == 1)
    _, index, _ = source.read_next()
    assert source.video_time_ms_for_frame(index) - first_ms >= 30
    with pytest.raises(ValueError, match="retained timestamp"):
        source.video_time_ms_for_frame(0)


def test_open_and_read_happen_on_worker_thread(monkeypatch):
    driver = FakeCapture()
    worker_ids = []

    def open_capture(*_):
        worker_ids.append(threading.get_ident())
        return driver

    monkeypatch.setattr("app.live_video_source.cv2.VideoCapture", open_capture)
    source = LiveVideoSource(DEVICE)
    try:
        wait_for(lambda: bool(worker_ids))
        assert worker_ids[0] != threading.get_ident()
        assert source.read_next() == (None, None, False)
    finally:
        source._stop.set()
        driver.frames.put(None)
        source.close()
    assert driver.released.is_set()


def test_open_failure_is_reported_and_releases_driver(monkeypatch):
    driver = FakeCapture()
    driver.opened = False
    monkeypatch.setattr("app.live_video_source.cv2.VideoCapture", lambda *_: driver)
    source = LiveVideoSource(DEVICE)
    wait_for(lambda: source.error is not None)
    with pytest.raises(OSError, match="Could not open"):
        source.read_next()
    source.close()
    assert driver.released.is_set()


@pytest.mark.parametrize("failure", [None, cv2.error("capture driver error")])
def test_signal_loss_or_driver_error_is_not_reported_as_eof(capture, failure):
    source, driver, _ = capture
    driver.frames.put(failure)
    wait_for(lambda: source.error is not None)
    with pytest.raises(OSError):
        source.read_next()
    wait_for(driver.released.is_set)
    assert not source.is_at_end()


def test_resolution_changes_fail_explicitly_instead_of_using_stale_calibration(capture):
    source, driver, _ = capture
    driver.frames.put(np.zeros((24, 32, 3), dtype=np.uint8))
    wait_for(lambda: source._latest is not None)
    driver.frames.put(np.zeros((48, 64, 3), dtype=np.uint8))
    wait_for(lambda: source.error is not None)
    with pytest.raises(OSError, match="resolution changed"):
        source.read_next()


def test_stalled_driver_is_reported_instead_of_reusing_a_frozen_frame(capture):
    source, driver, _ = capture
    driver.frames.put(np.zeros((24, 32, 3), dtype=np.uint8))
    wait_for(lambda: source._latest is not None)
    source._last_received_at = time.monotonic() - 6
    with pytest.raises(OSError, match="No HDMI frames received for 5 seconds"):
        source.read_next()


def test_close_releases_after_a_blocked_read_and_prevents_stale_delivery(capture):
    source, driver, _ = capture
    timer = threading.Timer(0.05, lambda: driver.frames.put(None))
    timer.start()
    try:
        source.close()
    finally:
        timer.join()
    assert driver.released.is_set()
    with pytest.raises(OSError, match="disconnected"):
        source.read_next()


def test_stuck_capture_driver_shutdown_is_bounded_and_explicit():
    source = LiveVideoSource.__new__(LiveVideoSource)
    source.device = DEVICE
    source._stop = threading.Event()
    source._thread = Mock()
    source._thread.is_alive.return_value = True
    with pytest.raises(OSError, match="still shutting down"):
        source.close()
    source._thread.join.assert_called_once_with(timeout=2)
    assert source._stop.is_set()


def test_live_input_does_not_support_replay_operations(capture):
    source, _, _ = capture
    with pytest.raises(ValueError, match="cannot seek"):
        source.seek_to_frame(0)
    with pytest.raises(ValueError, match="latest frame"):
        source.skip_to_frame(100)
