"""Retain a neighboring source frame, not an unbounded OCR backlog."""
from concurrent.futures import Future
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import numpy as np

from app.ui.app_window import App


def _controller():
    jobs = []

    def submit(function, *args):
        future = Future()
        jobs.append((future, args))
        return future

    controller = SimpleNamespace(
        analysis=SimpleNamespace(
            is_running=True, process_background_numeric_frame=Mock(),
        ),
        regions=[SimpleNamespace(kind="combo")],
        _analysis_executor=SimpleNamespace(submit=submit),
        _analysis_future=None,
        _analysis_future_generation=0,
        _analysis_generation=0,
        _analysis_frame_index=-1,
        _analysis_is_confirmation=False,
        _confirmation_frame=None,
        _dropped_analysis_frames=0,
        _analysis_poll_after_id=None,
        after=Mock(return_value="poll"),
        _apply_analysis_result=Mock(),
        video_tab=SimpleNamespace(set_error_status=Mock()),
    )
    controller._submit_numeric_analysis = MethodType(App._submit_numeric_analysis, controller)
    controller._poll_analysis_result = MethodType(App._poll_analysis_result, controller)
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
