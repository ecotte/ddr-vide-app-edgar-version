"""Retain a neighboring source frame, not an unbounded OCR backlog."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, get_ident
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.analysis import AnalysisSession
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
        _final_numeric_frame=None,
        _last_frame_bgr=None,
        _last_frame_position=None,
        _dropped_analysis_frames=0,
        _analysis_poll_after_id=None,
        after=Mock(return_value="poll"),
        _apply_analysis_result=Mock(),
        video_tab=SimpleNamespace(set_error_status=Mock(), set_processing_status=Mock()),
        events_tab=SimpleNamespace(clear=Mock()),
    )
    controller._submit_numeric_analysis = MethodType(App._submit_numeric_analysis, controller)
    controller._poll_analysis_result = MethodType(App._poll_analysis_result, controller)
    controller._flush_final_numeric_frame = MethodType(App._flush_final_numeric_frame, controller)
    controller._submit_final_numeric_analysis = MethodType(App._submit_final_numeric_analysis, controller)
    return controller, jobs


def test_busy_ocr_keeps_first_neighbor_then_skips_to_current_playback():
    app, jobs = _controller()
    frames = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(5)]
    for i in range(3):
        app._submit_numeric_analysis(frames[i], i, i * 17)
    assert len(jobs) == 1
    assert app._confirmation_frame[0] is frames[1]
    assert app._dropped_analysis_frames == 1

    jobs[0][0].set_result("first")
    app._poll_analysis_result()
    assert len(jobs) == 2
    assert jobs[1][1][0] is frames[1]
    assert jobs[1][1][1:] == (1, 17)
    app._submit_numeric_analysis(frames[3], 3, 51)
    assert app._confirmation_frame is None
    jobs[1][0].set_result("confirmation")
    app._poll_analysis_result()
    assert len(jobs) == 2
    app._submit_numeric_analysis(frames[4], 4, 68)
    assert len(jobs) == 3
    assert jobs[2][1][0] is frames[4]


def test_repeated_paused_frame_is_not_buffered_as_confirmation():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 10, 170)
    app._submit_numeric_analysis(frame, 10, 170)
    assert app._confirmation_frame is None
    jobs[0][0].set_result("first")
    app._poll_analysis_result()
    assert len(jobs) == 1


def test_seek_discards_old_pair_and_starts_new_generation():
    app, jobs = _controller()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    app._submit_numeric_analysis(frame, 10, 170)
    app._submit_numeric_analysis(frame, 11, 187)
    app._analysis_generation += 1
    app._submit_numeric_analysis(frame, 0, 0)
    jobs[0][0].set_result("old")
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
        jobs[0][0].set_result(None)
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
        app._analysis_future.set_result(None)
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
    assert len(old_result.new_events) == 3
    assert all(event.session_id == old_session for event in old_result.new_events)
    assert app.event_bus.session_id != old_session
    assert app.event_bus.events == []
    assert all(analysis.tracker_for(region).accepted_value is None for region in analysis.regions)
    assert analysis.is_running
    app.events_tab.clear.assert_called_once()
    export_path = tmp_path / "new-session.jsonl"
    assert app.event_bus.export_jsonl(str(export_path)) == 0
    assert export_path.read_text() == ""


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
    assert bus.events == []


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
