"""Source lifecycle/controller checks without hardware, Tk, or network access."""
from concurrent.futures import Future
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.live_video_source import CaptureDevice, LiveVideoSource
from app.models import Region
from app.ocr_engine import OcrEngine, OcrResult
from app.ui.app_window import App
from app.video_source import VideoSource


DEVICE = CaptureDevice(1, "Hagibis", cv2.CAP_DSHOW, "hdmi-device")
FRAME = np.zeros((360, 640, 3), dtype=np.uint8)


class FakeLiveSource(LiveVideoSource):
    def __init__(self, device):
        self.device = device
        self.width, self.height = 640, 360
        self.fps = 60.0
        self.frame_count = 0
        self.read_next = Mock(return_value=(None, None, False))
        self.close = Mock()
        self.is_at_end = Mock(return_value=False)
        self.current_frame_index = Mock(return_value=8)
        self.video_time_ms_for_frame = Mock(return_value=123)
        self.seek_to_frame = Mock()
        self.skip_to_frame = Mock()
        self.start_recording = Mock()
        self.stop_recording = Mock()


def _file_source():
    source = Mock(spec=VideoSource)
    source.path = "game.mp4"
    source.source_mode = "replay"
    source.width, source.height = 640, 360
    source.fps, source.frame_count = 60.0, 100
    source.read_next.return_value = (FRAME, 0, False)
    source.video_time_ms_for_frame.return_value = 0
    return source


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr("app.ui.app_window.LiveVideoSource", FakeLiveSource)
    bus = EventBus()
    app = SimpleNamespace(
        video_source=None, _pending_live_source=None, _capture_poll_after_id=None,
        _capture_started_at=0, _capture_devices=[DEVICE], _calibration_resolution=None,
        event_bus=bus, analysis=AnalysisSession(bus, Mock(spec=OcrEngine)), regions=[],
        video_tab=Mock(), events_tab=Mock(),
        is_playing=False, _play_after_id=None, _analysis_generation=0,
        _confirmation_frame=None, _latest_numeric_frame=None, _final_numeric_frame=None,
        _last_frame_position=None, _last_frame_bgr=None,
        _video_recording=None, _recording_poll_after_id=None, _recording_after_save=None,
        _recording_save_requested=False, _recording_error_reported=False,
        _recording_save_future=None, _recording_executor=Mock(),
        after=Mock(return_value="capture-poll"), after_cancel=Mock(),
        _schedule_play_tick=Mock(), _process_and_update=Mock(),
        _refresh_score_calibration_status=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    for name in (
        "select_source", "refresh_capture_devices", "toggle_hdmi", "_cancel_hdmi_connection",
        "_poll_live_connection", "_disconnect_source", "_activate_source", "_stop_playing",
        "_start_playing", "_play_live_tick",
        "toggle_video_recording", "_stop_video_recording", "_prepare_recording_change",
        "_poll_video_recording", "_save_video_recording", "_on_close", "load_video",
    ):
        setattr(app, name, MethodType(getattr(App, name), app))
    app.video_tab.capture_device_var.get.return_value = DEVICE.label
    return app


def test_selecting_hdmi_lists_names_without_opening_any_camera(controller, monkeypatch):
    discover = Mock(return_value=[DEVICE])
    monkeypatch.setattr("app.ui.app_window.list_capture_devices", discover)
    controller.select_source("HDMI input")
    discover.assert_called_once_with()
    controller.video_tab.set_capture_devices.assert_called_once_with([DEVICE.label])
    controller.video_tab.set_live_mode.assert_called_with(True)
    assert controller.video_source is None
    assert controller._pending_live_source is None


def test_device_discovery_failure_clears_stale_selection_and_is_visible(controller, monkeypatch):
    monkeypatch.setattr(
        "app.ui.app_window.list_capture_devices", Mock(side_effect=OSError("device enumeration failed")),
    )
    controller.refresh_capture_devices()
    assert controller._capture_devices == []
    controller.video_tab.set_capture_devices.assert_called_once_with([])
    assert "device enumeration failed" in controller.video_tab.set_error_status.call_args.args[0]


def test_hdmi_connect_requires_an_explicit_valid_device(controller):
    controller.video_tab.capture_device_var.get.return_value = ""
    controller.toggle_hdmi()
    assert controller._pending_live_source is None
    controller.video_tab.set_error_status.assert_called_once_with("Select your HDMI capture device first.")


def test_hdmi_connection_is_pending_until_a_frame_arrives(controller):
    original_id = controller.event_bus.session_id
    controller.toggle_hdmi()
    source = controller._pending_live_source
    assert source.device == DEVICE
    controller._poll_live_connection()
    assert controller.video_source is None
    assert controller._pending_live_source is source
    assert controller.event_bus.session_id == original_id
    assert not controller.is_playing
    controller.after.assert_called_with(30, controller._poll_live_connection)
    source.read_next.return_value = (FRAME, 7, False)
    controller._poll_live_connection()
    assert controller.video_source is source
    assert controller._pending_live_source is None
    assert controller.is_playing
    event, = controller.event_bus.events
    assert event.source_mode == "live"
    assert event.event_type == "new_session"
    assert event.payload["reason"] == "hdmi_connected"
    assert (event.frame_index, event.video_time_ms) == (7, 123)
    assert event.session_id != original_id
    controller.video_tab.set_hdmi_connected.assert_called_with(True)
    controller.video_tab.set_live_mode.assert_called_with(True)


def test_connection_error_is_visible_and_candidate_is_released(controller):
    controller.toggle_hdmi()
    source = controller._pending_live_source
    source.read_next.side_effect = OSError("device is busy")
    controller._poll_live_connection()
    source.close.assert_called_once_with()
    assert controller._pending_live_source is None
    assert controller.video_source is None
    controller.video_tab.set_error_status.assert_called_with("device is busy")
    controller.video_tab.set_capture_pending.assert_called_with(False)


def test_capture_start_timeout_is_explicit(controller):
    controller.toggle_hdmi()
    controller._capture_started_at -= 16
    controller._poll_live_connection()
    assert "15 seconds" in controller.video_tab.set_error_status.call_args.args[0]
    assert controller._pending_live_source is None


def test_cancel_releases_candidate_and_cancels_connection_poll(controller):
    controller.toggle_hdmi()
    source = controller._pending_live_source
    controller.toggle_hdmi()
    source.close.assert_called_once_with()
    controller.after_cancel.assert_called_once_with("capture-poll")
    assert controller._pending_live_source is None


def test_live_poll_with_no_new_frame_is_not_eof_or_an_analysis_reset(controller):
    source = FakeLiveSource(DEVICE)
    controller.video_source = source
    controller.is_playing = True
    controller.analysis.start()
    generation = controller.analysis.generation
    controller._play_live_tick()
    controller._schedule_play_tick.assert_called_once_with(15)
    assert controller.is_playing
    assert controller.analysis.is_running
    assert controller.analysis.generation == generation
    controller._process_and_update.assert_not_called()


def test_live_poll_uses_capture_timestamp_and_never_seeks_to_catch_up(controller):
    source = FakeLiveSource(DEVICE)
    source.read_next.return_value = (FRAME, 250, False)
    controller.video_source = source
    controller.is_playing = True
    App._play_tick(controller)
    controller._process_and_update.assert_called_once_with(FRAME, 250, 123, is_seek=False)
    source.seek_to_frame.assert_not_called()
    source.skip_to_frame.assert_not_called()


def test_signal_loss_stops_preview_analysis_and_pending_numeric_work(controller):
    source = FakeLiveSource(DEVICE)
    source.read_next.side_effect = OSError("HDMI signal lost")
    controller.video_source = source
    controller.is_playing = True
    controller.analysis.start()
    controller._confirmation_frame = object()
    controller._latest_numeric_frame = object()
    controller._final_numeric_frame = object()
    controller._play_live_tick()
    assert not controller.is_playing
    assert not controller.analysis.is_running
    assert controller._confirmation_frame is None
    assert controller._latest_numeric_frame is None
    assert controller._final_numeric_frame is None
    controller.video_tab.set_error_status.assert_called_with("HDMI signal lost")
    controller._schedule_play_tick.assert_not_called()


@pytest.mark.parametrize("method,args", [("step_forward", ()), ("step_backward", ()), ("seek_to", (5,))])
def test_live_replay_controls_are_guarded_in_controller(controller, method, args):
    controller.video_source = FakeLiveSource(DEVICE)
    getattr(App, method)(controller, *args)
    controller.video_source.seek_to_frame.assert_not_called()
    controller.video_tab.set_error_status.assert_called_once()


def test_switching_back_to_file_releases_live_source_and_restores_replay_mode(controller, monkeypatch):
    live = FakeLiveSource(DEVICE)
    controller.video_source = live
    controller.analysis.start()
    file = _file_source()
    monkeypatch.setattr("app.ui.app_window.VideoSource", Mock(return_value=file))
    controller.event_bus.source_mode = "live"
    old_id = controller.event_bus.session_id
    App.load_video(controller, "game.mp4")
    live.close.assert_called_once_with()
    assert controller.video_source is file
    assert not controller.analysis.is_running
    assert controller.event_bus.source_mode == "replay"
    event, = controller.event_bus.events
    assert event.source_mode == "replay"
    assert event.payload["reason"] == "video_loaded"
    assert event.session_id != old_id
    controller.video_tab.set_source_selection.assert_called_with("Video file")
    controller.video_tab.set_live_mode.assert_called_with(False)


def test_empty_file_is_closed_without_discarding_existing_source(controller, monkeypatch):
    current = _file_source()
    controller.video_source = current
    empty = _file_source()
    empty.read_next.return_value = (None, None, False)
    monkeypatch.setattr("app.ui.app_window.VideoSource", Mock(return_value=empty))
    error = Mock()
    monkeypatch.setattr("app.ui.app_window.messagebox.showerror", error)
    App.load_video(controller, "empty.mp4")
    empty.close.assert_called_once_with()
    current.close.assert_not_called()
    assert controller.video_source is current
    error.assert_called_once()


def test_disconnect_failure_is_visible_and_preserves_handle_for_retry(controller):
    source = FakeLiveSource(DEVICE)
    source.close.side_effect = OSError("driver still shutting down")
    controller.video_source = source
    assert not controller._disconnect_source()
    assert controller.video_source is source
    controller.video_tab.set_error_status.assert_called_with("driver still shutting down")


@pytest.mark.parametrize("fit", [True, False])
def test_changed_source_resolution_requires_explicit_calibration_choice(controller, monkeypatch, fit):
    controller.regions = [Region("Combo", "P1", "combo", 10, 10, 40, 20)]
    controller._calibration_resolution = (320, 180)
    controller.analysis.set_regions(controller.regions)
    prompt = Mock(return_value=fit)
    monkeypatch.setattr("app.ui.app_window.messagebox.askyesno", prompt)
    source = FakeLiveSource(DEVICE)
    controller._activate_source(source, FRAME, 7, reason="hdmi_connected")
    prompt.assert_called_once()
    if fit:
        assert (controller.regions[0].x, controller.regions[0].w) == (20, 80)
    else:
        assert controller.regions == []
    assert controller._calibration_resolution == (640, 360)


def test_live_gameplay_and_following_session_announcements_keep_live_source_mode(controller):
    source = FakeLiveSource(DEVICE)
    controller.regions = [Region("Combo", "P1", "combo", 0, 0, 40, 20)]
    controller.analysis.set_regions(controller.regions)
    controller.analysis.ocr_engine.recognize_digits.return_value = OcrResult("10", 0.99)
    controller._activate_source(source, FRAME, 7, reason="hdmi_connected")
    controller.analysis.start()
    controller.analysis.process_numeric_frame(FRAME, 8, 140)
    original_id = controller.event_bus.session_id
    controller.analysis.new_session(9, 160, reason="game_end", clear_events=False)
    assert all(event.source_mode == "live" for event in controller.event_bus.events)
    assert controller.event_bus.events[-1].payload["previous_session_id"] == original_id
    assert controller.event_bus.events[1].event_type == "combo"


@pytest.fixture
def recording_controller(controller, monkeypatch, tmp_path):
    source = FakeLiveSource(DEVICE)
    recording = Mock(finished=False, active=True, usable=True, error=None, path=tmp_path / "temporary.mp4")
    source.start_recording.return_value = recording
    controller.video_source = source
    controller._recording_executor.submit.return_value = Future()
    monkeypatch.setattr("app.ui.app_window.messagebox.showerror", Mock())
    monkeypatch.setattr("app.ui.app_window.filedialog.asksaveasfilename", Mock(return_value=""))
    return controller, recording


def test_record_stop_save_encodes_without_affecting_preview_or_analysis(recording_controller, monkeypatch, tmp_path):
    controller, recording = recording_controller
    controller.is_playing = True
    controller.analysis.start()
    controller.toggle_video_recording()
    assert controller._video_recording is recording
    controller.video_source.start_recording.assert_called_once_with()
    controller.toggle_video_recording()
    recording.stop.assert_called_once_with()
    assert controller.is_playing
    assert controller.analysis.is_running
    destination = str(tmp_path / "game.mp4")
    dialog = Mock(return_value=destination)
    monkeypatch.setattr("app.ui.app_window.filedialog.asksaveasfilename", dialog)
    controller._poll_video_recording()
    dialog.assert_not_called()
    recording.finished = True
    controller._poll_video_recording()
    assert dialog.call_args.kwargs["defaultextension"] == ".mp4"
    controller._recording_executor.submit.assert_called_once_with(recording.save, destination)
    controller._recording_save_future.set_result(None)
    controller._poll_video_recording()
    assert controller._video_recording is None
    controller.video_tab.set_recording_state.assert_called_with("idle", "MP4 recording saved.")


def test_cancel_save_keeps_recording_and_allows_retry(recording_controller, monkeypatch):
    controller, recording = recording_controller
    controller.toggle_video_recording()
    controller.toggle_video_recording()
    recording.finished = True
    controller._poll_video_recording()
    assert controller._video_recording is recording
    assert controller._recording_after_save is None
    assert controller.video_tab.set_recording_state.call_args.args[0] == "unsaved"
    controller._recording_executor.submit.assert_not_called()
    dialog = Mock(return_value="retry.mp4")
    monkeypatch.setattr("app.ui.app_window.filedialog.asksaveasfilename", dialog)
    controller.toggle_video_recording()
    controller._recording_executor.submit.assert_called_once_with(recording.save, "retry.mp4")


def test_failed_save_keeps_recording_and_cancels_deferred_source_change(recording_controller, monkeypatch):
    controller, recording = recording_controller
    controller.toggle_video_recording()
    recording.finished = True
    monkeypatch.setattr("app.ui.app_window.filedialog.asksaveasfilename", Mock(return_value="game.mp4"))
    action = Mock()
    assert not controller._prepare_recording_change(action)
    controller._recording_save_future.set_exception(OSError("destination is full"))
    controller._poll_video_recording()
    assert controller._video_recording is recording
    assert controller._recording_after_save is None
    action.assert_not_called()
    from app.ui.app_window import messagebox
    assert "destination is full" in messagebox.showerror.call_args.args[1]


@pytest.mark.parametrize("action", ["disconnect", "switch", "close"])
def test_source_change_and_close_wait_for_recording_save(recording_controller, monkeypatch, action):
    controller, recording = recording_controller
    controller.toggle_video_recording()
    original_source = controller.video_source
    if action == "disconnect":
        controller.toggle_hdmi()
    elif action == "switch":
        controller.select_source("Video file")
    else:
        controller._on_close()
    original_source.close.assert_not_called()
    assert controller._recording_after_save is not None
    recording.finished = True
    controller._poll_video_recording()
    assert controller._video_recording is recording
    assert controller.video_source is original_source
    assert controller._recording_after_save is None


def test_successful_save_resumes_deferred_action(recording_controller, monkeypatch):
    controller, recording = recording_controller
    controller.toggle_video_recording()
    action = Mock()
    assert not controller._prepare_recording_change(action)
    action.assert_not_called()
    recording.finished = True
    monkeypatch.setattr("app.ui.app_window.filedialog.asksaveasfilename", Mock(return_value="game.mp4"))
    controller._poll_video_recording()
    action.assert_not_called()
    controller._recording_save_future.set_result(None)
    controller._poll_video_recording()
    action.assert_called_once_with()


def test_signal_loss_offers_to_save_partial_recording(recording_controller):
    controller, recording = recording_controller
    controller.toggle_video_recording()
    recording.finished = True
    recording.error = "No HDMI recording frames received for 5 seconds."
    controller._poll_video_recording()
    from app.ui.app_window import filedialog, messagebox
    messagebox.showerror.assert_called_once()
    filedialog.asksaveasfilename.assert_called_once()
    assert controller._video_recording is recording
