"""Retain a neighboring source frame, not an unbounded OCR backlog."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, get_ident
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.analysis import AnalysisSession, FrameAnalysisResult, RegionFrameResult
from app.event_bus import EventBus
from app.models import Region
from app.ocr_engine import OcrEngine, OcrResult
from app.ui.app_window import App


def _controller():
    jobs = []

    def submit(function, *args, **kwargs):
        future = Future()
        jobs.append((future, args))
        return future

    controller = SimpleNamespace(
        analysis=SimpleNamespace(
            is_running=True, generation=0, process_background_numeric_frame=Mock(),
        ),
        event_bus=EventBus(),
        regions=[SimpleNamespace(kind="combo")],
        _analysis_executor=SimpleNamespace(submit=submit),
        _analysis_future=None,
        _analysis_future_generation=0,
        _analysis_generation=0,
        _analysis_frame_index=-1,
        _analysis_is_confirmation=False,
        _confirmation_frame=None,
        _latest_numeric_frame=None,
        _final_numeric_frame=None,
        _last_frame_bgr=None,
        _last_frame_position=None,
        _dropped_analysis_frames=0,
        _analysis_poll_after_id=None,
        after=Mock(return_value="poll"),
        _apply_analysis_result=Mock(),
        video_tab=SimpleNamespace(set_error_status=Mock(), set_processing_status=Mock()),
        events_tab=SimpleNamespace(clear=Mock(), append_event=Mock()),
    )
    controller._submit_numeric_analysis = MethodType(App._submit_numeric_analysis, controller)
    controller._poll_analysis_result = MethodType(App._poll_analysis_result, controller)
    controller._flush_final_numeric_frame = MethodType(App._flush_final_numeric_frame, controller)
    controller._submit_final_numeric_analysis = MethodType(App._submit_final_numeric_analysis, controller)
    return controller, jobs


def _display_controller():
    controller = SimpleNamespace(
        analysis=SimpleNamespace(
            generation=0,
            tracker_for=Mock(return_value=SimpleNamespace(status=lambda: None)),
        ),
        _arrow_display_state={},
        _recognition_photos={},
        _make_thumbnail_photo=Mock(return_value="photo"),
        _format_arrow_status=Mock(return_value="idle"),
        _format_numeric_status=Mock(return_value="stable"),
        video_tab=Mock(),
        events_tab=Mock(),
    )
    controller._update_recognition_row = MethodType(App._update_recognition_row, controller)
    return controller


def test_unchanged_arrow_previews_are_limited_to_ten_updates_per_second(monkeypatch):
    controller = _display_controller()
    rr = RegionFrameResult(
        Region("Up", "P1", "arrow", 0, 0, 20, 20, direction="Up"),
        arrow_state="idle", crop=np.zeros((20, 20, 3), dtype=np.uint8),
    )
    clock = [0.0]
    monkeypatch.setattr("app.ui.app_window.time.perf_counter", lambda: clock[0])
    for timestamp in (0.0, 0.016, 0.032, 0.099, 0.1):
        clock[0] = timestamp
        controller._update_recognition_row(rr)
    assert controller.video_tab.update_arrow_recognition_row.call_count == 2
    assert controller._make_thumbnail_photo.call_count == 2


@pytest.mark.parametrize("change", ["state", "error", "unreadable", "generation", "player"])
def test_arrow_state_errors_and_tracking_resets_update_without_waiting(monkeypatch, change):
    controller = _display_controller()
    rr = RegionFrameResult(
        Region("Up", "P1", "arrow", 0, 0, 20, 20, direction="Up"), arrow_state="idle",
    )
    monkeypatch.setattr("app.ui.app_window.time.perf_counter", lambda: 0.0)
    controller._update_recognition_row(rr)
    if change == "state":
        rr.arrow_state = "hit"
    elif change == "error":
        rr.error = "Missing arrow reference"
    elif change == "unreadable":
        rr.is_unreadable = True
    elif change == "generation":
        controller.analysis.generation += 1
    else:
        rr.region = Region("Up", "P2", "arrow", 0, 0, 20, 20, direction="Up")
    controller._update_recognition_row(rr)
    assert controller.video_tab.update_arrow_recognition_row.call_count == 2


def test_preview_pacing_never_drops_events_or_delays_numeric_results(monkeypatch):
    controller = _display_controller()
    monkeypatch.setattr("app.ui.app_window.time.perf_counter", lambda: 0.0)
    arrow = RegionFrameResult(
        Region("Up", "P1", "arrow", 0, 0, 20, 20, direction="Up"), arrow_state="idle",
    )
    events = [object(), object()]
    for event in events:
        App._apply_analysis_result(controller, FrameAnalysisResult([arrow], [event], 1), 0)
    assert controller.video_tab.update_arrow_recognition_row.call_count == 1
    assert [call.args[0] for call in controller.events_tab.append_event.call_args_list] == events
    for kind in ("combo", "score"):
        rr = RegionFrameResult(Region(kind, "P1", kind, 0, 0, 20, 20))
        for _ in range(2):
            App._apply_analysis_result(controller, FrameAnalysisResult([rr], [], 1), 0)
    assert controller.video_tab.update_recognition_row.call_count == 4


def _numeric_result(needs_confirmation=False):
    return FrameAnalysisResult([
        RegionFrameResult(
            Region("Combo", "P1", "combo", 0, 0, 2, 2),
            needs_confirmation=needs_confirmation,
        ),
    ], [], 1)


@pytest.mark.parametrize("result", [_numeric_result(), None])
def test_finished_job_goes_directly_to_latest_frame_without_unneeded_confirmation(result):
    app, jobs = _controller()
    frames = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(40)]
    for i, frame in enumerate(frames):
        app._submit_numeric_analysis(frame, i, i * 17)
    assert len(jobs) == 1
    assert app._confirmation_frame[1] == 1
    assert app._latest_numeric_frame[1] == 39
    jobs[0][0].set_result(result)
    app._poll_analysis_result()
    assert len(jobs) == 2
    assert jobs[1][1][0] is frames[39]
    assert jobs[1][1][1:] == (39, 663)
    assert not app._analysis_is_confirmation
    jobs[1][0].set_result(_numeric_result())
    app._poll_analysis_result()
    assert len(jobs) == 2


def test_confirmation_preserves_latest_paused_frame_and_cannot_chain_old_neighbors():
    app, jobs = _controller()
    for i in range(40):
        app._submit_numeric_analysis(np.full((2, 2, 3), i, dtype=np.uint8), i, i * 17)
    jobs[0][0].set_result(_numeric_result(needs_confirmation=True))
    app._poll_analysis_result()
    assert jobs[1][1][1] == 1
    assert app._latest_numeric_frame[1] == 39
    jobs[1][0].set_result(_numeric_result(needs_confirmation=True))
    app._poll_analysis_result()
    assert jobs[2][1][1] == 39
    assert not app._analysis_is_confirmation
    jobs[2][0].set_result(_numeric_result(needs_confirmation=True))
    app._poll_analysis_result()
    assert len(jobs) == 3


@pytest.mark.parametrize("kind,text,confidence,needs_confirmation", [
    ("combo", "179", 0.99, False), ("combo", "179", 0.4, False),
    ("combo", "", 0.99, False), ("score", "363009", 0.5, True),
    ("score", "363009", 0.99, False),
])
def test_analysis_requests_confirmation_only_for_pending_evidence(
    monkeypatch, kind, text, confidence, needs_confirmation,
):
    engine = OcrEngine()
    monkeypatch.setattr(engine, "recognize_digits", lambda _: OcrResult(text, confidence))
    monkeypatch.setattr(engine, "recognize_score_digits", lambda _: OcrResult(text, confidence))
    analysis = AnalysisSession(EventBus(), engine)
    analysis.set_regions([Region(kind, "P1", kind, 0, 0, 2, 2)])
    analysis.start()
    result = analysis.process_background_numeric_frame(np.zeros((2, 2, 3), dtype=np.uint8), 123, 2050)
    assert result.needs_confirmation is needs_confirmation
    assert result.region_results[0].frame_index == 123
    assert result.region_results[0].video_time_ms == 2050


def test_confirmation_only_runs_pending_fields_and_does_not_reread_other_player(monkeypatch):
    engine = OcrEngine()
    score_ocr = Mock(return_value=OcrResult("100", 0.5))
    combo_ocr = Mock(return_value=OcrResult("65", 0.99))
    monkeypatch.setattr(engine, "recognize_score_digits", score_ocr)
    monkeypatch.setattr(engine, "recognize_digits", combo_ocr)
    bus = EventBus()
    analysis = AnalysisSession(bus, engine)
    analysis.set_regions([
        Region("Score", "P1", "score", 0, 0, 2, 2),
        Region("Combo", "P1", "combo", 0, 0, 2, 2),
        Region("Combo", "P2", "combo", 0, 0, 2, 2),
    ])
    analysis.start()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    first = analysis.process_background_numeric_frame(frame, 0, 0)
    keys = frozenset(rr.region.key() for rr in first.region_results if rr.needs_confirmation)
    assert keys == frozenset({"P1:score"})
    confirmed = analysis.process_background_numeric_frame(frame, 1, 17, region_keys=keys)
    assert [rr.region.key() for rr in confirmed.region_results] == ["P1:score"]
    assert confirmed.region_results[0].accepted_value == 100
    assert score_ocr.call_count == 2
    assert combo_ocr.call_count == 2
    latest = analysis.process_background_numeric_frame(frame, 40, 680)
    assert len(latest.region_results) == 3
    assert combo_ocr.call_count == 4


def test_scheduler_passes_only_pending_region_keys_to_confirmation_worker():
    app, _ = _controller()
    future = Future()
    app._analysis_executor.submit = Mock(return_value=future)
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 0, 0)
    app._submit_numeric_analysis(frame, 1, 17)
    result = _numeric_result(needs_confirmation=True)
    result.region_results.append(RegionFrameResult(Region("Score", "P1", "score", 0, 0, 2, 2)))
    future.set_result(result)
    app._poll_analysis_result()
    assert app._analysis_executor.submit.call_args.kwargs["region_keys"] == frozenset({"P1:combo"})


def test_busy_ocr_keeps_first_neighbor_when_needed_then_skips_to_current_playback():
    app, jobs = _controller()
    frames = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(5)]
    for i in range(3):
        app._submit_numeric_analysis(frames[i], i, i * 17)
    assert len(jobs) == 1
    assert app._confirmation_frame[0] is frames[1]
    assert app._dropped_analysis_frames == 1

    jobs[0][0].set_result(_numeric_result(needs_confirmation=True))
    app._poll_analysis_result()
    assert len(jobs) == 2
    assert jobs[1][1][0] is frames[1]
    assert jobs[1][1][1:] == (1, 17)
    app._submit_numeric_analysis(frames[3], 3, 51)
    assert app._confirmation_frame is None
    jobs[1][0].set_result(_numeric_result())
    app._poll_analysis_result()
    assert len(jobs) == 3
    assert jobs[2][1][0] is frames[3]
    app._submit_numeric_analysis(frames[4], 4, 68)
    jobs[2][0].set_result(_numeric_result())
    app._poll_analysis_result()
    assert len(jobs) == 4
    assert jobs[3][1][0] is frames[4]


def test_combo_filter_checkbox_updates_controller_and_retains_loaded_setting():
    import tkinter as tk
    from tkinter import ttk
    from app.ui.video_tab import VideoTab

    app, _ = _controller()
    combo = Region("Combo", "P1", "combo", 0, 0, 50, 20)
    score = Region("Score", "P1", "score", 0, 0, 50, 20)
    app.analysis = AnalysisSession(app.event_bus)
    app.analysis.set_regions([combo, score])
    app.set_combo_yellow_filter = MethodType(App.set_combo_yellow_filter, app)
    app._last_frame_position = (10, 330)
    app._last_frame_bgr = np.zeros((20, 50, 3), dtype=np.uint8)
    app._submit_numeric_analysis = Mock()
    root = tk.Tk()
    root.withdraw()
    try:
        view = SimpleNamespace(
            recognition_container=ttk.Frame(root), controller=app, _region_rows={},
        )
        VideoTab._build_numeric_row(view, combo)
        row = view.recognition_container.winfo_children()[0]
        checkbox, = [
            w for w in row.winfo_children()
            if isinstance(w, ttk.Checkbutton) and w["text"] == "Yellow-digit filter"
        ]
        assert not view._region_rows[combo.key()]["yellow_filter_var"].get()
        checkbox.invoke()
        assert combo.combo_yellow_filter
        assert app._analysis_generation == 1
        app._submit_numeric_analysis.assert_called_once_with(app._last_frame_bgr, 10, 330)
        assert "Save Layout" in app.video_tab.set_processing_status.call_args.args[0]
        loaded_combo = Region.from_dict(combo.to_dict())
        VideoTab._build_numeric_row(view, loaded_combo)
        assert view._region_rows[combo.key()]["yellow_filter_var"].get()
        VideoTab._build_numeric_row(view, score)
        assert view._region_rows[score.key()]["yellow_filter_var"] is None
        checkbox.invoke()
        assert not combo.combo_yellow_filter
        assert app._analysis_generation == 2
    finally:
        root.destroy()


def test_repeated_paused_frame_is_not_buffered_as_confirmation():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 10, 170)
    app._submit_numeric_analysis(frame, 10, 170)
    assert app._confirmation_frame is None
    jobs[0][0].set_result(_numeric_result())
    app._poll_analysis_result()
    assert len(jobs) == 1


def test_preview_skip_failure_stops_playback_and_surfaces_error(monkeypatch):
    app, _ = _controller()
    app.is_playing = True
    app._playback_started_at = 0
    app._playback_start_frame = 0
    app._stop_playing = Mock()
    app.video_source = SimpleNamespace(
        fps=60, current_frame_index=lambda: 0,
        skip_to_frame=Mock(side_effect=IOError("Could not decode while catching up playback.")),
        read_next=Mock(),
    )
    monkeypatch.setattr("app.ui.app_window.time.perf_counter", lambda: 1)
    App._play_tick(app)
    app._stop_playing.assert_called_once()
    app.video_tab.set_error_status.assert_called_once_with(
        "Could not decode while catching up playback."
    )
    app.video_source.read_next.assert_not_called()


def test_seek_discards_old_pair_and_starts_new_generation():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 10, 170)
    app._submit_numeric_analysis(frame, 11, 187)
    app._analysis_generation += 1
    app._submit_numeric_analysis(frame, 0, 0)
    jobs[0][0].set_result(_numeric_result())
    app._poll_analysis_result()
    app._apply_analysis_result.assert_not_called()
    assert jobs[1][1][1:] == (0, 0)
    assert not app._analysis_is_confirmation


def test_stop_does_not_submit_buffered_frame():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 0, 0)
    app._submit_numeric_analysis(frame, 1, 17)
    app.analysis.is_running = False
    jobs[0][0].set_result(None)
    app._poll_analysis_result()
    assert len(jobs) == 1
    assert app._confirmation_frame is None


@pytest.mark.parametrize("during_confirmation", [False, True])
def test_eof_flushes_final_frame_after_preserving_confirmation_pair(during_confirmation):
    app, jobs = _controller()
    frames = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(3)]
    app._submit_numeric_analysis(frames[0], 0, 0)
    app._submit_numeric_analysis(frames[1], 1, 17)
    if during_confirmation:
        jobs[0][0].set_result(_numeric_result(needs_confirmation=True))
        app._poll_analysis_result()
    app._submit_numeric_analysis(frames[2], 2, 34)
    app._last_frame_bgr = frames[2]
    app._last_frame_position = (2, 34)
    app.is_playing = True
    app.video_source = SimpleNamespace(fps=0, read_next=lambda: (None, None, False))
    app._stop_playing = lambda: setattr(app, "is_playing", False)
    App._play_tick(app)
    assert not app.is_playing
    while app._analysis_future is not None:
        app._analysis_future.set_result(_numeric_result(needs_confirmation=True))
        app._poll_analysis_result()
    assert [args[1:] for _, args in jobs] == [(0, 0), (1, 17), (2, 34)]
    assert jobs[-1][1][0] is frames[2]
    assert app._final_numeric_frame is None


def test_eof_does_not_process_final_frame_twice():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 0, 0)
    app._submit_numeric_analysis(frame, 1, 17)
    app._last_frame_bgr = frame
    app._last_frame_position = (1, 17)
    app._flush_final_numeric_frame()
    for i in range(2):
        jobs[i][0].set_result(None)
        app._poll_analysis_result()
    app._flush_final_numeric_frame()
    assert len(jobs) == 2
    assert app._final_numeric_frame is None


def test_seek_invalidates_buffered_final_frame():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 10, 170)
    app._last_frame_bgr = frame
    app._last_frame_position = (11, 187)
    app._flush_final_numeric_frame()
    app._analysis_generation += 1
    app._submit_numeric_analysis(frame, 0, 0)
    jobs[0][0].set_result(None)
    app._poll_analysis_result()
    jobs[1][0].set_result(None)
    app._poll_analysis_result()
    assert [args[1] for _, args in jobs] == [10, 0]
    assert app._final_numeric_frame is None


def test_new_session_drains_whole_numeric_frame_before_reset(monkeypatch, tmp_path):
    app, _ = _controller()
    entered_ocr, release_ocr, reset_waiting = Event(), Event(), Event()
    engine = OcrEngine()

    def blocking_score(_):
        entered_ocr.set()
        assert release_ocr.wait(5)
        return OcrResult("100", 0.99)

    monkeypatch.setattr(engine, "recognize_score_digits", blocking_score)
    monkeypatch.setattr(engine, "recognize_digits", lambda _: OcrResult("65", 0.99))
    analysis = AnalysisSession(app.event_bus, engine)
    analysis.set_regions([
        Region("Score", "P1", "score", 0, 0, 80, 30),
        Region("Combo", "P1", "combo", 0, 0, 80, 30),
    ])
    analysis.start()
    app.analysis = analysis
    old_session = app.event_bus.session_id
    lock = analysis._score_lock
    reset_thread = []

    class ObservedLock:
        def __enter__(self):
            if reset_thread and get_ident() == reset_thread[0]:
                reset_waiting.set()
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

    analysis._score_lock = ObservedLock()

    def reset():
        reset_thread.append(get_ident())
        App.new_session(app)

    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(
            analysis.process_background_numeric_frame,
            np.zeros((30, 80, 3), dtype=np.uint8), 120, 2000,
            expected_session_id=old_session,
        )
        try:
            assert entered_ocr.wait(5)
            resetting = pool.submit(reset)
            assert reset_waiting.wait(5)
            assert app.event_bus.session_id == old_session
        finally:
            release_ocr.set()
        old_result = work.result(timeout=5)
        resetting.result(timeout=5)
    assert len(old_result.new_events) == 4
    assert all(event.session_id == old_session for event in old_result.new_events)
    assert app.event_bus.session_id != old_session
    assert [event.event_type for event in app.event_bus.events] == ["new_session"]
    assert all(analysis.tracker_for(region).accepted_value is None for region in analysis.regions)
    assert analysis.is_running
    app.events_tab.clear.assert_called_once()
    app.events_tab.append_event.assert_called_once_with(app.event_bus.events[0])
    export_path = tmp_path / "new-session.jsonl"
    assert app.event_bus.export_jsonl(str(export_path)) == 1


def test_queued_job_from_previous_session_is_ignored(monkeypatch):
    engine = OcrEngine()
    recognize = Mock(return_value=OcrResult("100", 0.99))
    monkeypatch.setattr(engine, "recognize_score_digits", recognize)
    bus = EventBus()
    analysis = AnalysisSession(bus, engine)
    analysis.set_regions([Region("Score", "P1", "score", 0, 0, 80, 30)])
    analysis.start()
    old_session = bus.session_id
    analysis.new_session()
    result = analysis.process_background_numeric_frame(
        np.zeros((30, 80, 3), dtype=np.uint8), 120, 2000,
        expected_session_id=old_session,
    )
    assert result is None
    recognize.assert_not_called()
    assert [event.event_type for event in bus.events] == ["new_session"]


def test_automatic_boundary_displays_completed_old_work_without_resubmitting_old_frames():
    from app import image_utils

    app, jobs = _controller()
    engine = Mock(spec=OcrEngine)
    engine.recognize_digits.return_value = OcrResult("10", 0.99)
    frame = np.zeros((30, 80, 3), dtype=np.uint8)
    end_frame = frame.copy()
    end_frame[:, 40:] = 255
    end_region = Region(
        "Results", "Unknown", "game_end", 40, 0, 40, 30,
        reference_images={"ended": [image_utils.encode_crop_to_base64_png(end_frame[:, 40:])]},
    )
    app.regions = [Region("Combo", "P1", "combo", 0, 0, 40, 30), end_region]
    app.analysis = AnalysisSession(app.event_bus, engine)
    app.analysis.set_regions(app.regions)
    app.analysis.start()
    old_result = app.analysis.process_background_numeric_frame(frame, 0, 0)
    old_id = app.event_bus.session_id
    completed = Future()
    completed.set_result(old_result)
    app._analysis_future = completed
    app._analysis_frame_index = 0
    app._confirmation_frame = (frame, 1, 33, 0)
    app._latest_numeric_frame = (frame, 1, 33, 0)
    app._final_numeric_frame = (frame, 1, 33, 0)
    app.live_scoreboard = Mock()
    app.video_tab = Mock()
    app.video_source = SimpleNamespace(frame_count=100)
    app._display_current_frame = Mock()
    app.analysis.process_game_end_frame(end_frame, 1, 33)

    App._process_and_update(app, end_frame, 2, 66, False)

    app._apply_analysis_result.assert_any_call(old_result, 0)
    assert app.event_bus.session_id != old_id
    assert app._analysis_generation == 1
    assert app._analysis_future_generation == 1
    assert len(jobs) == 1
    assert jobs[0][1][1:] == (2, 66)
    assert app._confirmation_frame is None
    assert app._latest_numeric_frame is None
    assert app._final_numeric_frame is None
    assert app.event_bus.events[-1].payload["previous_session_id"] == old_id


def test_queued_job_from_before_seek_is_ignored_without_changing_session(monkeypatch):
    engine = OcrEngine()
    recognize = Mock(return_value=OcrResult("100", 0.99))
    monkeypatch.setattr(engine, "recognize_score_digits", recognize)
    bus = EventBus()
    analysis = AnalysisSession(bus, engine)
    analysis.set_regions([Region("Score", "P1", "score", 0, 0, 80, 30)])
    analysis.start()
    generation = analysis.generation
    session_id = bus.session_id
    analysis.reset_tracking()
    frame = np.zeros((30, 80, 3), dtype=np.uint8)
    result = analysis.process_background_numeric_frame(
        frame, 120, 2000, expected_session_id=session_id, expected_generation=generation,
    )
    assert result is None
    assert bus.session_id == session_id
    recognize.assert_not_called()
    assert bus.events == []
    result = analysis.process_background_numeric_frame(
        frame, 0, 0, expected_session_id=session_id, expected_generation=analysis.generation,
    )
    assert result.region_results[0].accepted_value == 100


def test_eof_flush_records_final_score_event(monkeypatch):
    app, jobs = _controller()
    engine = OcrEngine()
    monkeypatch.setattr(
        engine, "recognize_score_digits",
        lambda crop: OcrResult(str(int(crop[0, 0, 0])), 0.99),
    )
    app.analysis = AnalysisSession(app.event_bus, engine)
    app.regions = [Region("Score", "P1", "score", 0, 0, 2, 2)]
    app.analysis.set_regions(app.regions)
    app.analysis.start()
    frames = [np.full((2, 2, 3), value, dtype=np.uint8) for value in (100, 110, 120)]

    def finish(index):
        future, args = jobs[index]
        future.set_result(app.analysis.process_background_numeric_frame(*args))
        app._poll_analysis_result()

    app._submit_numeric_analysis(frames[0], 0, 0)
    app._submit_numeric_analysis(frames[1], 1, 17)
    finish(0)
    app._submit_numeric_analysis(frames[2], 2, 34)
    app._last_frame_bgr = frames[2]
    app._last_frame_position = (2, 34)
    app._flush_final_numeric_frame()
    finish(1)
    finish(2)
    changes = [e for e in app.event_bus.events if e.event_type == "score_changed"]
    assert [e.payload["new_value"] for e in changes] == [100, 110, 120]
    assert changes[-1].frame_index == 2
    assert changes[-1].video_time_ms == 34
    assert app.analysis.tracker_for(app.regions[0]).accepted_value == 120
