"""Main application window: wires video, regions, recognition, and events."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from tkinter import ttk, filedialog, messagebox, simpledialog
from typing import Optional

import cv2
from PIL import Image, ImageTk

from app import config, credential_store, image_utils, layout_store
from app.credential_store import CredentialStorageError
from app.analysis import AnalysisSession, RegionFrameResult
from app.event_bus import EventBus
from app.live_scoreboard import LiveScoreboard
from app.live_video_source import CaptureDevice, LiveVideoSource, list_capture_devices
from app.eventstream import EventstreamPublisher
from app.layout_store import ResolutionMismatchError
from app.models import LayoutProfile, Region
from app.ocr_engine import OcrEngine
from app.recognition import ArrowTrackerStatus, TrackerStatus
from app.rayfin_sender import RayfinSender
from app.ui.events_tab import EventsTab
from app.ui.region_dialog import RegionDialog
from app.ui.score_calibration_dialog import ScoreCalibrationDialog
from app.ui.theme import apply_dark_theme
from app.ui.video_tab import VideoTab, fit_display_size, next_playback_delay_ms
from app.video_source import VideoSource


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DDR Video Event Extractor")
        self.geometry("1500x900")
        apply_dark_theme(self)

        self.ocr_engine = OcrEngine()
        self.event_bus = EventBus(source_mode=config.SOURCE_MODE_REPLAY)
        self.publisher = EventstreamPublisher(os.path.join(config.DATA_DIR, "eventstream-outbox.sqlite3"))
        self.live_scoreboard = LiveScoreboard(
            sender_factory=lambda: RayfinSender(registration_sink=self.publisher.enqueue_registration),
        )
        self.event_bus.set_live_sink(self.live_scoreboard.record)
        self._publishing_enabled = False
        self._publisher_poll_after_id = None
        self.analysis = AnalysisSession(self.event_bus, self.ocr_engine)

        self.video_source: Optional[VideoSource | LiveVideoSource] = None
        self._pending_live_source: Optional[LiveVideoSource] = None
        self._capture_poll_after_id: Optional[str] = None
        self._capture_started_at = 0.0
        self._capture_devices: list[CaptureDevice] = []
        self._calibration_resolution: Optional[tuple[int, int]] = None
        self.regions = []  # list[Region]
        self.is_playing = False
        self._play_after_id: Optional[str] = None
        self._playback_started_at = 0.0
        self._playback_start_frame = 0
        self._last_frame_bgr = None  # last displayed frame; used for arrow reference capture
        self._last_frame_position: Optional[tuple[int, int]] = None
        self._analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._analysis_future: Optional[Future] = None
        self._analysis_future_generation = 0
        self._analysis_frame_index = -1
        self._analysis_is_confirmation = False
        self._confirmation_frame = None
        self._latest_numeric_frame = None
        self._final_numeric_frame = None
        self._analysis_generation = 0
        self._analysis_poll_after_id: Optional[str] = None
        self._dropped_analysis_frames = 0
        self._recognition_photos = {}
        self._arrow_display_state = {}

        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 16))
        header.pack(fill="x")
        ttk.Label(header, text="DDR Video Event Extractor", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header, text="VIDEO + HDMI  /  Calibrate regions. Review recognition. Capture events.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.video_tab = VideoTab(notebook, self)
        self.events_tab = EventsTab(notebook, self)
        notebook.add(self.video_tab, text="Video & Regions")
        notebook.add(self.events_tab, text="Events")

        self.video_tab.refresh_layout_names()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._start_ocr_loading()
        self._poll_publisher_status()

    # ------------------------------------------------------------------
    # OCR engine background loading
    def _start_ocr_loading(self) -> None:
        self.video_tab.set_ocr_status(
            "OCR engine: loading EasyOCR model (first run may download ~100MB and needs internet once)..."
        )
        threading.Thread(target=self.ocr_engine.ensure_ready, daemon=True).start()
        self.after(300, self._poll_ocr_status)

    def _poll_ocr_status(self) -> None:
        if self.ocr_engine.is_ready:
            self.video_tab.set_ocr_status("OCR engine: ready (EasyOCR, CPU)")
            return
        if self.ocr_engine.init_error:
            self.video_tab.set_ocr_status("OCR engine: FAILED to load - see error below")
            self.video_tab.set_error_status(self.ocr_engine.init_error)
            return
        self.after(300, self._poll_ocr_status)

    # ------------------------------------------------------------------
    # Source selection / playback
    def select_source(self, selection: str) -> None:
        if not self._cancel_hdmi_connection() or not self._disconnect_source():
            active = "HDMI input" if isinstance(self.video_source, LiveVideoSource) else "Video file"
            self.video_tab.set_source_selection(active)
            return
        self.video_tab.set_source_selection(selection)
        self.video_tab.set_live_mode(selection == "HDMI input")
        if selection == "HDMI input":
            self.refresh_capture_devices()

    def refresh_capture_devices(self) -> None:
        try:
            self._capture_devices = list_capture_devices()
        except (OSError, RuntimeError) as exc:
            self._capture_devices = []
            self.video_tab.set_capture_devices([])
            self.video_tab.set_error_status(f"Could not enumerate capture devices: {exc}")
            return
        self.video_tab.set_capture_devices([device.label for device in self._capture_devices])
        self.video_tab.set_error_status("")
        self.video_tab.set_video_info(
            "Select the HDMI capture device and click Connect HDMI."
            if self._capture_devices else
            "No capture devices found. Connect your HDMI-to-USB capture card, then Refresh devices."
        )

    def toggle_hdmi(self) -> None:
        if self._pending_live_source is not None:
            self._cancel_hdmi_connection()
            return
        if isinstance(self.video_source, LiveVideoSource):
            self._disconnect_source()
            return
        selected = self.video_tab.capture_device_var.get()
        device = next((item for item in self._capture_devices if item.label == selected), None)
        if device is None:
            self.video_tab.set_error_status("Select your HDMI capture device first.")
            return
        if not self._disconnect_source():
            return
        self._pending_live_source = LiveVideoSource(device)
        self._capture_started_at = time.monotonic()
        self.video_tab.set_capture_pending(True)
        self.video_tab.set_video_info(f"Connecting to {device.name}...")
        self._capture_poll_after_id = self.after(30, self._poll_live_connection)

    def _cancel_hdmi_connection(self) -> bool:
        if self._capture_poll_after_id is not None:
            self.after_cancel(self._capture_poll_after_id)
            self._capture_poll_after_id = None
        if self._pending_live_source is not None:
            try:
                self._pending_live_source.close()
            except OSError as exc:
                self.video_tab.set_error_status(str(exc))
                return False
            self._pending_live_source = None
            self.video_tab.set_video_info("HDMI disconnected.")
        self.video_tab.set_capture_pending(False)
        return True

    def _poll_live_connection(self) -> None:
        self._capture_poll_after_id = None
        source = self._pending_live_source
        if source is None:
            return
        try:
            frame, index, _ = source.read_next()
            if frame is None:
                if time.monotonic() - self._capture_started_at >= 15:
                    raise OSError("No HDMI frame received within 15 seconds. Check the device and HDMI signal.")
                self._capture_poll_after_id = self.after(30, self._poll_live_connection)
                return
        except OSError as exc:
            if self._cancel_hdmi_connection():
                self.video_tab.set_error_status(str(exc))
            return
        self._pending_live_source = None
        self.video_tab.set_capture_pending(False)
        assert index is not None
        self._activate_source(source, frame, index, reason="hdmi_connected")
        self._start_playing()

    def _disconnect_source(self) -> bool:
        self._stop_playing()
        self.analysis.stop()
        self._analysis_generation += 1
        self._confirmation_frame = None
        self._latest_numeric_frame = None
        self._final_numeric_frame = None
        self.video_tab.set_analysis_status(False)
        if self.video_source is not None:
            try:
                self.video_source.close()
            except OSError as exc:
                self.video_tab.set_error_status(str(exc))
                return False
        self.video_source = None
        self._last_frame_position = None
        self._last_frame_bgr = None
        self.video_tab.set_hdmi_connected(False)
        self.video_tab.clear_source_frame()
        self.video_tab.set_video_info("No source connected.")
        return True

    def open_video_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Open DDR gameplay video",
            filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.m4v"), ("All files", "*.*")],
        )
        if not path:
            return
        self.load_video(path)

    def load_video(self, path: str) -> None:
        self._stop_playing()
        try:
            new_source = VideoSource(path)
        except IOError as exc:
            messagebox.showerror("Could not open video", str(exc))
            return

        frame, idx, _ = new_source.read_next()
        if frame is None:
            new_source.close()
            messagebox.showerror("Empty video", "Could not read any frames from this video.")
            return
        if not self._cancel_hdmi_connection() or not self._disconnect_source():
            new_source.close()
            return
        assert idx is not None
        self._activate_source(new_source, frame, idx, reason="video_loaded")

    def _activate_source(
        self, source: VideoSource | LiveVideoSource, frame, frame_index: int, *, reason: str,
    ) -> None:
        self.video_source = source
        is_live = isinstance(source, LiveVideoSource)
        resolution = (source.width, source.height)
        old_resolution = self._calibration_resolution
        if self.regions and old_resolution is not None and old_resolution != resolution:
            old_width, old_height = old_resolution
            fit = messagebox.askyesno(
                "Source resolution changed",
                f"Existing regions were calibrated at {old_width}x{old_height}; "
                f"the new source is {source.width}x{source.height}.\n\n"
                "Fit the current regions and reference images to this source?\n"
                "No removes them from this view; saved layouts are unchanged.",
            )
            self.regions = (
                layout_store.rescale_profile(
                    LayoutProfile("Current regions", old_width, old_height, self.regions),
                    source.width, source.height,
                ).regions if fit else []
            )
            self.analysis.set_regions(self.regions)
            self.video_tab.refresh_region_list(self.regions)
            self._refresh_arrow_reference_thumbnails()
        self._calibration_resolution = resolution
        self._analysis_generation += 1
        self.analysis.stop()
        self.event_bus.source_mode = source.source_mode
        timestamp = source.video_time_ms_for_frame(frame_index)
        self.analysis.new_session(frame_index, timestamp, reason=reason)
        self._confirmation_frame = None
        self._latest_numeric_frame = None
        self._final_numeric_frame = None
        self._last_frame_position = None
        self._last_frame_bgr = None
        self.events_tab.clear()
        for event in self.event_bus.events:
            self.events_tab.append_event(event)
        self.video_tab.set_analysis_status(False)
        self.video_tab.set_error_status("")
        self.video_tab.set_source_selection("HDMI input" if is_live else "Video file")
        self.video_tab.set_live_mode(is_live)
        self.video_tab.set_hdmi_connected(is_live)
        if isinstance(source, LiveVideoSource):
            fps = f"{source.fps:.2f} fps reported" if source.fps else "FPS not reported by device"
            info = f"LIVE: {source.device.name} | {source.width}x{source.height} | {fps}"
        else:
            info = (
                f"{os.path.basename(source.path)} | {source.width}x{source.height} | "
                f"{source.fps:.2f} fps | {source.frame_count} frames"
            )
        self.video_tab.set_video_info(info)
        self._process_and_update(frame, frame_index, timestamp, is_seek=False)
        self._refresh_score_calibration_status()

    def step_forward(self) -> None:
        if isinstance(self.video_source, LiveVideoSource):
            self.video_tab.set_error_status("Live input cannot step. Freeze or resume the live preview instead.")
            return
        if self.video_source is None or self.is_playing:
            return
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            self._flush_final_numeric_frame()
            messagebox.showinfo("End of video", "No more frames.")
            return
        self._process_and_update(frame, idx, self.video_source.video_time_ms_for_frame(idx), is_seek)

    def step_backward(self) -> None:
        if isinstance(self.video_source, LiveVideoSource):
            self.video_tab.set_error_status("Live input cannot rewind.")
            return
        if self.video_source is None or self.is_playing:
            return
        current_next = self.video_source.current_frame_index()
        target = max(current_next - 2, 0)
        self.video_source.seek_to_frame(target)
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            return
        self._process_and_update(frame, idx, self.video_source.video_time_ms_for_frame(idx), is_seek)

    def seek_to(self, frame_index: int) -> None:
        if isinstance(self.video_source, LiveVideoSource):
            self.video_tab.set_error_status("Live input cannot seek.")
            return
        if self.video_source is None:
            return
        self._stop_playing()
        self.video_source.seek_to_frame(frame_index)
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            return
        self._process_and_update(frame, idx, self.video_source.video_time_ms_for_frame(idx), is_seek)

    def toggle_play(self) -> None:
        if self.video_source is None:
            messagebox.showerror("No source", "Open a video or connect an HDMI capture device first.")
            return
        if self.is_playing:
            self._stop_playing()
        else:
            self._start_playing()

    def _start_playing(self) -> None:
        if self.video_source.is_at_end():
            # Playback reached the end; restart from the beginning so Play
            # never appears stuck on the last frame.
            self.video_source.seek_to_frame(0)
            self.video_tab.set_error_status("")
        self.is_playing = True
        self._playback_started_at = time.perf_counter()
        self._playback_start_frame = self.video_source.current_frame_index()
        self.video_tab.set_play_button_label(True)
        self._schedule_play_tick(0)

    def _stop_playing(self) -> None:
        self.is_playing = False
        self.video_tab.set_play_button_label(False)
        if self._play_after_id is not None:
            self.after_cancel(self._play_after_id)
            self._play_after_id = None

    def _schedule_play_tick(self, delay_ms: int) -> None:
        self._play_after_id = self.after(delay_ms, self._play_tick)

    def _play_tick(self) -> None:
        if not self.is_playing or self.video_source is None:
            return
        if isinstance(self.video_source, LiveVideoSource):
            self._play_live_tick()
            return
        fps = self.video_source.fps
        if fps > 0:
            elapsed = time.perf_counter() - self._playback_started_at
            target_frame = self._playback_start_frame + int(elapsed * fps)
            if target_frame < self.video_source.current_frame_index():
                next_deadline = (
                    self._playback_started_at
                    + (
                        (self.video_source.current_frame_index() - self._playback_start_frame)
                        / fps
                    )
                )
                delay = max(1, round((next_deadline - time.perf_counter()) * 1000))
                self._schedule_play_tick(delay)
                return
            if target_frame > self.video_source.current_frame_index():
                try:
                    self.video_source.skip_to_frame(target_frame)
                except OSError as exc:
                    self._stop_playing()
                    self.video_tab.set_error_status(str(exc))
                    return
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            self._stop_playing()
            self._flush_final_numeric_frame()
            self.video_tab.set_error_status("Reached end of video.")
            return
        self._process_and_update(frame, idx, self.video_source.video_time_ms_for_frame(idx), is_seek)
        if fps > 0:
            next_deadline = (
                self._playback_started_at
                + ((idx + 1 - self._playback_start_frame) / fps)
            )
            delay = max(1, round((next_deadline - time.perf_counter()) * 1000))
        else:
            delay = next_playback_delay_ms(0, 0)
        self._schedule_play_tick(delay)

    def _play_live_tick(self) -> None:
        source = self.video_source
        if not isinstance(source, LiveVideoSource):
            return
        try:
            frame, index, _ = source.read_next()
        except OSError as exc:
            self._stop_playing()
            self.analysis.stop()
            self._analysis_generation += 1
            self._confirmation_frame = None
            self._latest_numeric_frame = None
            self._final_numeric_frame = None
            self.video_tab.set_analysis_status(False)
            self.video_tab.set_error_status(str(exc))
            return
        if frame is not None:
            assert index is not None
            self._process_and_update(
                frame, index, source.video_time_ms_for_frame(index), is_seek=False,
            )
        self._schedule_play_tick(15)

    # ------------------------------------------------------------------
    # Frame processing + display
    def _process_and_update(self, frame_bgr, frame_index: int, video_time_ms: int, is_seek: bool) -> None:
        if is_seek:
            self._analysis_generation += 1
            self.analysis.reset_tracking()

        self._last_frame_bgr = frame_bgr  # available for arrow reference capture regardless of analysis state
        self._last_frame_position = (frame_index, video_time_ms)
        self.live_scoreboard.advance_video(video_time_ms, is_seek=is_seek)
        self._display_current_frame(frame_bgr)
        self.video_tab.set_frame_position(frame_index, self.video_source.frame_count, video_time_ms)

        if any(region.kind == "game_end" for region in self.regions):
            boundary = self.analysis.process_game_end_frame(frame_bgr, frame_index, video_time_ms)
            if boundary is not None and any(
                event.payload.get("reason") == "game_end" for event in boundary.new_events
            ):
                # Rotation drains the OCR locks; its completed old-game result must remain visible.
                self._confirmation_frame = None
                self._latest_numeric_frame = None
                self._final_numeric_frame = None
                future = self._analysis_future
                if future is not None and future.done():
                    self._poll_analysis_result()
                self._analysis_generation += 1
                self._confirmation_frame = None
                self._latest_numeric_frame = None
                self._final_numeric_frame = None
                self.video_tab.set_processing_status("Game ended - a new session GUID is ready.")
            self._apply_analysis_result(boundary, skipped_frames=0)
        arrow_result = self.analysis.process_arrow_frame(frame_bgr, frame_index, video_time_ms)
        self._apply_analysis_result(arrow_result, skipped_frames=0)
        self._submit_numeric_analysis(frame_bgr, frame_index, video_time_ms)

    def _submit_numeric_analysis(
        self, frame_bgr, frame_index: int, video_time_ms: int, *, confirmation: bool = False,
        region_keys: Optional[frozenset[str]] = None,
    ) -> None:
        if not self.analysis.is_running:
            return
        if not any(
            region.kind in ("score", "combo")
            for region in self.regions
        ):
            return
        if self._analysis_future is not None:
            new_generation = self._analysis_future_generation != self._analysis_generation
            if (new_generation or frame_index > self._analysis_frame_index) and (
                self._latest_numeric_frame is None
                or self._latest_numeric_frame[3] != self._analysis_generation
                or frame_index > self._latest_numeric_frame[1]
            ):
                self._latest_numeric_frame = (
                    frame_bgr, frame_index, video_time_ms, self._analysis_generation,
                )
            if (
                (new_generation or not self._analysis_is_confirmation)
                and (new_generation or frame_index > self._analysis_frame_index)
                and (
                    self._confirmation_frame is None
                    or self._confirmation_frame[3] != self._analysis_generation
                )
            ):
                self._confirmation_frame = (
                    frame_bgr, frame_index, video_time_ms, self._analysis_generation,
                )
                return
            self._dropped_analysis_frames += 1
            return
        self._analysis_future_generation = self._analysis_generation
        self._analysis_frame_index = frame_index
        self._analysis_is_confirmation = confirmation
        self._analysis_future = self._analysis_executor.submit(
            self.analysis.process_background_numeric_frame,
            frame_bgr,
            frame_index,
            video_time_ms,
            expected_session_id=self.event_bus.session_id,
            expected_generation=self.analysis.generation,
            region_keys=region_keys,
        )
        self._analysis_poll_after_id = self.after(10, self._poll_analysis_result)

    def _flush_final_numeric_frame(self) -> None:
        if not self.analysis.is_running or self._last_frame_position is None:
            return
        index, timestamp = self._last_frame_position
        self._final_numeric_frame = (
            self._last_frame_bgr, index, timestamp, self._analysis_generation,
        )
        self._submit_final_numeric_analysis()

    def _submit_final_numeric_analysis(self) -> None:
        if self._analysis_future is not None or self._final_numeric_frame is None:
            return
        frame, index, timestamp, generation = self._final_numeric_frame
        self._final_numeric_frame = None
        if generation != self._analysis_generation:
            return
        if self._analysis_future_generation == generation and index <= self._analysis_frame_index:
            return
        self._submit_numeric_analysis(frame, index, timestamp, confirmation=True)

    def _poll_analysis_result(self) -> None:
        self._analysis_poll_after_id = None
        future = self._analysis_future
        if future is None:
            return
        if not future.done():
            self._analysis_poll_after_id = self.after(10, self._poll_analysis_result)
            return

        self._analysis_future = None
        pending = self._confirmation_frame
        self._confirmation_frame = None
        latest = self._latest_numeric_frame
        self._latest_numeric_frame = None
        completed_generation = self._analysis_future_generation
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - worker failures must be visible in the UI
            self.video_tab.set_error_status(f"Analysis worker error: {exc}")
            return
        if completed_generation == self._analysis_generation:
            self._apply_analysis_result(result, self._dropped_analysis_frames)
        self._dropped_analysis_frames = 0
        use_confirmation = (
            completed_generation == self._analysis_generation
            and not self._analysis_is_confirmation
            and result is not None
            and result.needs_confirmation
            and pending is not None
            and pending[3] == self._analysis_generation
        )
        next_frame = pending if use_confirmation else latest
        if next_frame is not None and next_frame[3] == self._analysis_generation:
            frame, index, timestamp, generation = next_frame
            region_keys = (
                frozenset(rr.region.key() for rr in result.region_results if rr.needs_confirmation)
                if use_confirmation else None
            )
            self._submit_numeric_analysis(
                frame, index, timestamp, confirmation=use_confirmation, region_keys=region_keys,
            )
            if use_confirmation and latest is not None and latest[1] > index:
                self._latest_numeric_frame = latest
        self._submit_final_numeric_analysis()

    def _apply_analysis_result(
        self, result, skipped_frames: int
    ) -> None:
        if result is None:
            return

        is_numeric_result = (
            bool(result.region_results) and result.region_results[0].region.kind in ("score", "combo")
        )
        if is_numeric_result:
            self.video_tab.set_processing_status(
                f"Last OCR pass: {result.processing_ms:.0f} ms across "
                f"{len(result.region_results)} region(s); "
                f"{skipped_frames} video frame(s) skipped by OCR"
            )
        errors = [rr.error for rr in result.region_results if rr.error]
        if errors:
            self.video_tab.set_error_status("; ".join(errors))
        elif is_numeric_result:
            self.video_tab.set_error_status("")

        for rr in result.region_results:
            self._update_recognition_row(rr)
        for event in result.new_events:
            self.events_tab.append_event(event)

    def _display_current_frame(self, frame_bgr) -> None:
        src_h, src_w = frame_bgr.shape[:2]
        max_w, max_h = self.video_tab.video_display_limits()
        disp_w, disp_h, scale = fit_display_size(src_w, src_h, max_w, max_h)
        display_frame = frame_bgr
        if scale != 1.0:
            display_frame = cv2.resize(
                frame_bgr, (disp_w, disp_h), interpolation=cv2.INTER_AREA
            )
        rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        self.video_tab.show_frame_image(pil_img, scale)
        self.video_tab.redraw_region_overlays(self.regions)

    def _update_recognition_row(self, rr: RegionFrameResult) -> None:
        if rr.region.kind in ("arrow", "game_end"):
            now = time.perf_counter()
            display_state = (
                self.analysis.generation, rr.arrow_state, rr.error, rr.is_unreadable,
            )
            previous = self._arrow_display_state.get(rr.region.key())
            if previous is not None and previous[1] == display_state and now - previous[0] < 0.1:
                return
        photo = self._make_thumbnail_photo(
            rr.crop, self._recognition_photos.get(rr.region.key()),
        )
        self._recognition_photos[rr.region.key()] = photo
        if rr.region.kind in ("arrow", "game_end"):
            tracker_status: ArrowTrackerStatus = self.analysis.tracker_for(rr.region).status()
            status_text = self._format_arrow_status(rr, tracker_status)
            self.video_tab.update_arrow_recognition_row(
                rr.region.key(), photo, rr.arrow_state, rr.confidence, status_text
            )
            self._arrow_display_state[rr.region.key()] = (now, display_state)
        else:
            tracker_status: TrackerStatus = self.analysis.tracker_for(rr.region).status()
            status_text = self._format_numeric_status(rr, tracker_status)
            self.video_tab.update_recognition_row(
                rr.region.key(), photo, rr.raw_text, rr.candidate_value, rr.accepted_value, status_text,
                frame_index=rr.frame_index, video_time_ms=rr.video_time_ms,
            )

    @staticmethod
    def _make_thumbnail_photo(crop, existing=None, *, max_width=240, max_height=96) -> Optional[ImageTk.PhotoImage]:
        if crop is None or crop.size == 0:
            return None
        crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(crop_rgb)
        w, h = pil_img.size
        scale = min(max(1, 120 // max(w, 1)), max_width / w, max_height / h)
        if scale != 1:
            pil_img = pil_img.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))),
                Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.NEAREST,
            )
        if existing is not None and (existing.width(), existing.height()) == pil_img.size:
            existing.paste(pil_img)
            return existing
        return ImageTk.PhotoImage(pil_img)

    @staticmethod
    def _format_numeric_status(rr: RegionFrameResult, ts: TrackerStatus) -> str:
        if rr.error:
            return f"OCR error: {rr.error}"
        if rr.validation_note:
            return rr.validation_note
        if rr.is_unreadable:
            return "unreadable this frame (previous accepted value kept)"
        if ts.pending_value is not None and ts.pending_value != ts.accepted_value and ts.pending_count > 0:
            return f"pending {ts.pending_count}/{ts.required_count} frames toward {ts.pending_value}"
        return "stable"

    @staticmethod
    def _format_arrow_status(rr: RegionFrameResult, ts: ArrowTrackerStatus) -> str:
        if rr.error:
            return rr.error
        if rr.is_unreadable:
            return "unreadable this frame (region out of frame bounds?)"
        if ts.pending_state is not None and ts.pending_state != ts.active_state and ts.pending_count > 0:
            return f"pending {ts.pending_count}/{ts.required_count} frames toward '{ts.pending_state}'"
        return f"active: {ts.active_state}" if ts.active_state else "idle"

    # ------------------------------------------------------------------
    # Region calibration
    def on_region_drawn(self, x: int, y: int, w: int, h: int) -> None:
        existing_keys = {r.key() for r in self.regions}
        dialog = RegionDialog(self, existing_keys=existing_keys)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        label, player, kind, direction = dialog.result
        region = Region(label=label, player=player, kind=kind, x=x, y=y, w=w, h=h, direction=direction)
        self.regions.append(region)
        self._analysis_generation += 1
        self.analysis.set_regions(self.regions)
        self.video_tab.refresh_region_list(self.regions)
        self.video_tab.redraw_region_overlays(self.regions)
        self._refresh_arrow_reference_thumbnails()
        if region.kind == "score":
            self._refresh_score_calibration_status()

    def delete_region(self, index: int) -> None:
        if 0 <= index < len(self.regions):
            del self.regions[index]
            self._analysis_generation += 1
            self.analysis.set_regions(self.regions)
            self.video_tab.refresh_region_list(self.regions)
            self.video_tab.redraw_region_overlays(self.regions)
            self._refresh_arrow_reference_thumbnails()

    # ------------------------------------------------------------------
    # Image calibration (arrow states and the shared game-end indicator)
    def capture_arrow_reference(self, region: Region, state: str) -> None:
        if self._last_frame_bgr is None:
            messagebox.showerror("No frame", "Open a video and display a frame first.")
            return
        frame_h, frame_w = self._last_frame_bgr.shape[:2]
        x0, y0 = max(0, region.x), max(0, region.y)
        x1, y1 = min(region.x + region.w, frame_w), min(region.y + region.h, frame_h)
        if x1 <= x0 or y1 <= y0:
            messagebox.showerror("Out of bounds", "This region falls outside the current frame.")
            return
        crop = self._last_frame_bgr[y0:y1, x0:x1]
        references = region.reference_images.setdefault(state, [])
        references.append(image_utils.encode_crop_to_base64_png(crop))
        self._analysis_generation += 1
        self.analysis.set_regions(self.regions)  # reload decoded references, resets tracking

        photo = self._make_thumbnail_photo(crop, max_width=96, max_height=64)
        self.video_tab.set_arrow_reference_thumbnail(region.key(), state, photo, len(references))
        self.video_tab.set_processing_status(
            f"Captured '{state}' reference {len(references)} for {region.label} at the current frame."
        )

    def clear_arrow_reference(self, region: Region, state: str) -> None:
        if state in region.reference_images:
            del region.reference_images[state]
            self._analysis_generation += 1
            self.analysis.set_regions(self.regions)
        self.video_tab.set_arrow_reference_thumbnail(region.key(), state, None, 0)

    def set_arrow_similarity_threshold(self, value: float) -> None:
        self.analysis.set_arrow_similarity_threshold(value)

    def set_arrow_min_confirm_frames(self, n: int) -> None:
        self.analysis.set_arrow_min_confirm_frames(n)

    def set_game_end_settings(self, threshold: float, confirm_frames: int) -> None:
        self.analysis.set_game_end_settings(threshold, confirm_frames)

    # ------------------------------------------------------------------
    # Layout profiles
    def list_layout_names(self):
        return layout_store.list_layout_names()

    def layouts_dir(self) -> str:
        os.makedirs(config.LAYOUTS_DIR, exist_ok=True)
        return config.LAYOUTS_DIR

    def _build_current_profile(self, name: str) -> Optional[LayoutProfile]:
        if self.video_source is None:
            messagebox.showerror("No video", "Open a video first (layouts store the video resolution).")
            return None
        return LayoutProfile(
            profile_name=name,
            source_width=self.video_source.width,
            source_height=self.video_source.height,
            regions=list(self.regions),
        )

    def save_layout(self, name: str) -> None:
        profile = self._build_current_profile(name)
        if profile is None:
            return
        path = layout_store.save_layout(profile)
        self.video_tab.refresh_layout_names()
        messagebox.showinfo("Layout saved", f"Saved layout '{name}' to:\n{path}")

    def save_layout_to_path(self, path: str) -> None:
        """Save the current regions to a caller-chosen file path (Save As...)."""
        name = os.path.splitext(os.path.basename(path))[0]
        profile = self._build_current_profile(name)
        if profile is None:
            return
        saved_path = layout_store.save_layout_to_path(profile, path)
        if os.path.dirname(os.path.abspath(saved_path)) == os.path.abspath(config.LAYOUTS_DIR):
            self.video_tab.refresh_layout_names()
        messagebox.showinfo("Layout saved", f"Saved layout to:\n{saved_path}")

    def _apply_loaded_profile(self, load_fn) -> None:
        """Shared logic for loading a profile (by name or by path): resolution
        check, optional rescale prompt, then applying regions to the UI."""
        if self.video_source is None:
            messagebox.showerror("No video", "Open a video first so its resolution can be checked.")
            return
        try:
            profile = load_fn(self.video_source.width, self.video_source.height)
        except FileNotFoundError:
            messagebox.showerror("Not found", "That layout file could not be found.")
            return
        except ResolutionMismatchError as exc:
            proceed = messagebox.askyesno(
                "Resolution mismatch",
                f"{exc}\n\nFit the saved region coordinates to the current video without "
                "distortion and load anyway? Regions will be scaled uniformly and centered. "
                "Choose 'No' to cancel and keep your current regions.",
            )
            if not proceed:
                return
            profile = layout_store.rescale_profile(
                exc.profile, self.video_source.width, self.video_source.height
            )

        unsupported_arrows = [
            region for region in profile.regions
            if region.kind == "arrow" and region.direction not in config.ARROW_DIRECTIONS
        ]
        if unsupported_arrows:
            messagebox.showwarning(
                "Arrow recalibration required",
                f"Removed {len(unsupported_arrows)} unsupported arrow region(s) from the loaded layout. "
                "Old directions cannot be mapped reliably to the five-direction setup.\n\n"
                "Score, combo, game-end, and supported arrow regions are kept. "
                "Draw and teach new arrow regions for Up-Right, Up-Left, Down-Right, Down-Left, "
                "and Center for each player.\n\n"
                "The saved layout file is unchanged until you save it.",
            )
        self.regions = [
            region for region in profile.regions
            if region.kind != "arrow" or region.direction in config.ARROW_DIRECTIONS
        ]
        self._analysis_generation += 1
        self.analysis.set_regions(self.regions)
        self.video_tab.refresh_region_list(self.regions)
        self.video_tab.redraw_region_overlays(self.regions)
        self._refresh_arrow_reference_thumbnails()
        self._refresh_score_calibration_status()

    def _refresh_score_calibration_status(self) -> None:
        statuses = []
        for region in self.regions:
            if region.kind != "score":
                continue
            if (
                region.digit_templates_reviewed
                and region.score_template_version != config.SCORE_TEMPLATE_VERSION
            ):
                statuses.append(f"{region.label}: OCR active; outdated templates ignored")
                continue
            if self.analysis.has_complete_score_templates(region):
                statuses.append(f"{region.label}: OCR active; digit references ready")
            else:
                known = (
                    set(region.digit_templates) if self.analysis.uses_score_templates(region) else set()
                )
                missing = "".join(digit for digit in "0123456789" if digit not in known)
                mode = "OCR with digit references" if self.analysis.uses_score_templates(region) else "OCR"
                statuses.append(f"{region.label}: {mode}; untaught digits {missing}")
        self.video_tab.set_calibration_status(
            "; ".join(statuses) if statuses else "Score calibration: no score region"
        )

    def set_combo_yellow_filter(self, region: Region, enabled: bool) -> None:
        self.analysis.set_combo_yellow_filter(region, enabled)
        self._analysis_generation += 1
        self.video_tab.set_processing_status(
            f"{region.label}: yellow-digit filter {'on' if enabled else 'off'}. "
            "Save Layout to keep this setting. Accepted values and existing events are unchanged."
        )
        if self._last_frame_position is not None:
            index, timestamp = self._last_frame_position
            self._submit_numeric_analysis(self._last_frame_bgr, index, timestamp)

    def set_combo_fast_mode(self, region: Region, enabled: bool) -> None:
        self.analysis.set_combo_fast_mode(region, enabled)
        self._analysis_generation += 1
        self.video_tab.set_processing_status(
            f"{region.label}: fast live combo {'on' if enabled else 'off'}. "
            "Save Layout to keep this setting. Acceptance thresholds are unchanged."
        )
        if self._last_frame_position is not None:
            index, timestamp = self._last_frame_position
            self._submit_numeric_analysis(self._last_frame_bgr, index, timestamp)

    def teach_score(self, region: Region) -> None:
        if self._last_frame_bgr is None:
            messagebox.showerror("No frame", "Open a video and display a clear score first.")
            return
        self._stop_playing()
        frame_h, frame_w = self._last_frame_bgr.shape[:2]
        x0, y0 = max(0, region.x), max(0, region.y)
        x1, y1 = min(frame_w, region.x + region.w), min(frame_h, region.y + region.h)
        if x1 <= x0 or y1 <= y0:
            messagebox.showerror("Out of bounds", "The score region is outside the frame.")
            return
        dialog = ScoreCalibrationDialog(self, region, self._last_frame_bgr[y0:y1, x0:x1])
        self.wait_window(dialog)
        if dialog.result is None:
            return
        region.digit_templates = dialog.result
        region.digit_templates_reviewed = True
        region.score_template_version = config.SCORE_TEMPLATE_VERSION
        self.analysis.update_score_templates(region)
        self._analysis_generation += 1
        self._refresh_score_calibration_status()
        self.video_tab.set_processing_status(
            "Score samples learned. Advance/play to verify with OCR. Save Layout to keep them. Existing events and "
            "accepted values are unchanged; New Session resets incorrect history."
        )
        if self.video_source is not None:
            index = max(0, self.video_source.current_frame_index() - 1)
            self._submit_numeric_analysis(
                self._last_frame_bgr, index, self.video_source.video_time_ms_for_frame(index)
            )

    def load_layout(self, name: str) -> None:
        self._apply_loaded_profile(
            lambda w, h: layout_store.load_layout_checked(name, w, h)
        )

    def load_layout_from_path(self, path: str) -> None:
        """Load a layout from a caller-chosen file path (Load Layout From...)."""
        self._apply_loaded_profile(
            lambda w, h: layout_store.load_layout_from_path_checked(path, w, h)
        )

    def _refresh_arrow_reference_thumbnails(self) -> None:
        """Show captured arrow/game-end reference images after layout changes,
        without waiting for a new
        frame to be processed."""
        for region in self.regions:
            if region.kind not in ("arrow", "game_end"):
                continue
            for state, encoded_images in region.reference_images.items():
                if not encoded_images:
                    continue
                img = image_utils.decode_base64_png_to_crop(encoded_images[-1])
                photo = self._make_thumbnail_photo(img, max_width=96, max_height=64)
                self.video_tab.set_arrow_reference_thumbnail(
                    region.key(), state, photo, len(encoded_images)
                )

    # ------------------------------------------------------------------
    # Analysis controls
    def set_consistency_frames(self, n: int) -> None:
        self.analysis.set_required_consecutive_frames(n)

    def toggle_analysis(self) -> None:
        if self.analysis.is_running:
            self._analysis_generation += 1
            self.analysis.stop()
        else:
            if self.video_source is None:
                messagebox.showerror("No source", "Open a video or connect an HDMI capture device first.")
                return
            if not self.regions:
                messagebox.showwarning(
                    "No regions", "Add at least one region (score, combo, arrow, or game_end) before starting analysis."
                )
                return
            self.analysis.start()
        self.video_tab.set_analysis_status(self.analysis.is_running)

    def new_session(self) -> None:
        self._analysis_generation += 1
        position = self._last_frame_position or (None, None)
        previous_count = len(self.event_bus.events)
        self.analysis.new_session(*position, clear_events=False)
        self._confirmation_frame = None
        self._latest_numeric_frame = None
        self._final_numeric_frame = None
        for event in self.event_bus.events[previous_count:]:
            self.events_tab.append_event(event)
        self.video_tab.set_error_status("")
        self.video_tab.set_processing_status(f"New session: {self.event_bus.session_id}. Any previous active session was ended.")

    # ------------------------------------------------------------------
    # Events tab support
    def toggle_publishing(self) -> None:
        if self._publishing_enabled:
            self.event_bus.set_delivery_sink(None)
            self._publishing_enabled = False
            self.publisher.stop()
            return
        connection = os.environ.get("DDR_EVENTSTREAM_CONNECTION_STRING", "")
        entered_locally = False
        if not connection:
            try:
                connection = credential_store.load_connection()
            except CredentialStorageError as exc:
                messagebox.showerror("Fabric credential storage", str(exc))
                return
        if not connection:
            connection = simpledialog.askstring(
                "Connect to Fabric",
                "Paste the Event Hub SAS connection string (including EntityPath).\n"
                "It will be saved in Windows Credential Manager for this Windows user.\n"
                "Use a rotated, unexposed key. Nothing is saved in source files or Git.\n\n"
                "Connecting sends queued events and future recorded events, including\n"
                "unconfirmed score observations. No video or reference images are sent.",
                show="*", parent=self,
            )
            entered_locally = True
        if not connection:
            return
        try:
            self.publisher.start(connection)
        except ValueError as exc:
            messagebox.showerror("Fabric configuration", str(exc))
            return
        except (sqlite3.Error, OSError):
            messagebox.showerror(
                "Fabric queue unavailable",
                "Cannot open the local delivery queue. Check free disk space and data-folder permissions.",
            )
            return
        self.event_bus.set_delivery_sink(self.publisher.enqueue)
        self._publishing_enabled = True
        if entered_locally:
            try:
                credential_store.save_connection(connection.strip())
            except CredentialStorageError as exc:
                messagebox.showwarning("Fabric credential not saved", str(exc))

    def forget_fabric_connection(self) -> None:
        if not messagebox.askyesno(
            "Forget Fabric connection",
            "Disconnect publishing and remove the saved Windows credential?\n\n"
            "Pending events remain queued. An in-flight send may still finish.",
        ):
            return
        self.event_bus.set_delivery_sink(None)
        self._publishing_enabled = False
        self.publisher.stop()
        try:
            credential_store.forget_connection()
        except CredentialStorageError as exc:
            messagebox.showerror("Fabric credential storage", str(exc))
            return
        if os.environ.get("DDR_EVENTSTREAM_CONNECTION_STRING"):
            messagebox.showinfo(
                "Saved credential removed",
                "DDR_EVENTSTREAM_CONNECTION_STRING is still set in this process's environment. "
                "Clear it in your launcher and restart the app to use a different connection.",
            )

    def _poll_publisher_status(self) -> None:
        status = self.publisher.status()
        self.events_tab.set_publishing_status(status.text(), self._publishing_enabled)
        self.events_tab.set_scoreboard_status(self.live_scoreboard.status())
        self.events_tab.scoreboard_var.set(self.live_scoreboard.active)
        self._publisher_poll_after_id = self.after(500, self._poll_publisher_status)

    def toggle_scoreboard(self, enabled: bool) -> None:
        if enabled:
            if not messagebox.askyesno(
                "Share live scoreboard",
                "Publish future recorded scores, combos and arrow-hit insights directly to Rayfin? "
                "Microsoft sign-in may open once. Authorized scoreboard viewers can see these values.\n\n"
                "No video, reference images or credentials are shared. "
                "Fabric publishing is independent. Start sharing before starting a new game.",
            ):
                self.events_tab.scoreboard_var.set(False)
                return
            try:
                self.live_scoreboard.start(self.event_bus.session_id)
            except ValueError as exc:
                self.events_tab.scoreboard_var.set(False)
                messagebox.showerror("Scoreboard publisher", str(exc))
        else:
            self.live_scoreboard.stop()

    def send_mapping_sample(self) -> None:
        if not self._publishing_enabled:
            messagebox.showerror("Fabric disconnected", "Connect to Fabric before sending a sample.")
            return
        sample_bus = EventBus(source_mode=config.SOURCE_MODE_SYNTHETIC)
        sample = sample_bus.make_event(
            "score_changed",
            {
                "field": "score", "region_label": "Mapping sample - not gameplay",
                "previous_value": 1000, "new_value": 1250, "is_test": True,
            },
            60, 1000, player="P1", confidence=0.95,
        )
        self.event_bus.record(sample)
        self.events_tab.append_event(sample)

    def set_recording_enabled(self, enabled: bool) -> None:
        self.analysis.recording_enabled = enabled

    def clear_events(self) -> None:
        self.event_bus.clear_events()
        self.events_tab.clear()

    def export_events(self, path: str) -> int:
        return self.event_bus.export_jsonl(path)

    def data_dir(self) -> str:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        return config.DATA_DIR

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        if not self._cancel_hdmi_connection() or not self._disconnect_source():
            return
        self._stop_playing()
        self.analysis.stop()
        self.event_bus.set_live_sink(None)
        self.live_scoreboard.stop()
        self.event_bus.set_delivery_sink(None)
        if self._publisher_poll_after_id is not None:
            self.after_cancel(self._publisher_poll_after_id)
        self.publisher.close()
        if self._analysis_poll_after_id is not None:
            self.after_cancel(self._analysis_poll_after_id)
            self._analysis_poll_after_id = None
        self._analysis_executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def run() -> None:
    app = App()
    app.mainloop()
