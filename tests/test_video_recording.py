"""Verify MP4 output and bounded encoding without capture hardware."""
import threading
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from app.video_recording import VideoRecording


def finish(recording):
    recording.stop()
    assert recording._finished.wait(5), "MP4 encoder did not finish."


@pytest.fixture
def recording():
    result = VideoRecording(64, 48, 10)
    try:
        yield result
    finally:
        finish(result)
        result.path.unlink(missing_ok=True)


def test_saved_mp4_preserves_raw_pixels_resolution_fps_and_elapsed_capture_time(recording, tmp_path):
    for timestamp, value in [(0, 30), (100, 60), (300, 180)]:
        recording.write(np.full((48, 64, 3), value, dtype=np.uint8), timestamp)
    finish(recording)
    assert recording.error is None
    assert recording.usable
    saved = tmp_path / "game.mp4"
    recording.save(str(saved))
    assert not recording.path.exists()
    video = cv2.VideoCapture(str(saved))
    try:
        assert video.isOpened()
        assert video.get(cv2.CAP_PROP_FPS) == pytest.approx(10)
        assert video.get(cv2.CAP_PROP_FRAME_COUNT) == 4
        decoded = []
        while True:
            ok, frame = video.read()
            if not ok:
                break
            assert frame.shape == (48, 64, 3)
            decoded.append(frame.mean())
        assert decoded == pytest.approx([30, 60, 60, 180], abs=6)
    finally:
        video.release()


def test_failed_save_preserves_original_recording_and_existing_destination(recording, monkeypatch, tmp_path):
    recording.write(np.zeros((48, 64, 3), dtype=np.uint8), 0)
    finish(recording)
    destination = tmp_path / "existing.mp4"
    destination.write_bytes(b"existing video")
    monkeypatch.setattr("app.video_recording.shutil.copyfile", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        recording.save(str(destination))
    assert recording.path.exists()
    assert destination.read_bytes() == b"existing video"
    assert list(tmp_path.iterdir()) == [destination]


def test_full_encoder_queue_stops_explicitly_without_blocking_capture(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked_write(frame):
        entered.set()
        assert release.wait(5)

    writer = Mock()
    writer.write.side_effect = blocked_write
    monkeypatch.setattr("app.video_recording.cv2.VideoWriter", Mock(return_value=writer))
    decoded = Mock()
    decoded.read.return_value = (True, np.zeros((48, 64, 3), dtype=np.uint8))
    monkeypatch.setattr("app.video_recording.cv2.VideoCapture", Mock(return_value=decoded))
    result = VideoRecording(64, 48, 10)
    try:
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        result.write(frame, 0)
        assert entered.wait(3)
        for timestamp in range(100, 600, 100):
            result.write(frame, timestamp)
        assert not result.active
        assert "could not keep up" in result.error
        release.set()
        finish(result)
        assert result.usable
        writer.release.assert_called_once_with()
    finally:
        release.set()
        finish(result)
        result.path.unlink(missing_ok=True)


@pytest.mark.parametrize("failure", ["open", "write"])
def test_encoder_failures_are_reported_and_writer_is_released(monkeypatch, failure):
    writer = Mock()
    writer.isOpened.return_value = failure != "open"
    if failure == "write":
        writer.write.side_effect = cv2.error("encoder failed")
    monkeypatch.setattr("app.video_recording.cv2.VideoWriter", Mock(return_value=writer))
    result = VideoRecording(64, 48, 10)
    try:
        result.write(np.zeros((48, 64, 3), dtype=np.uint8), 0)
        finish(result)
        assert result.error
        assert not result.usable
        writer.release.assert_called_once_with()
        with pytest.raises(OSError):
            result.save("invalid.mp4")
    finally:
        finish(result)
        result.path.unlink(missing_ok=True)
