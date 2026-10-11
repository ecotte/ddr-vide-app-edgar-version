"""Main application window: wires video, regions, recognition, and events."""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from tkinter import ttk, filedialog, messagebox
from typing import Optional

import cv2
from PIL import Image, ImageTk

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession, RegionFrameResult
from app.event_bus import EventBus
from app.layout_store import ResolutionMismatchError
from app.models import LayoutProfile, Region
from app.ocr_engine import OcrEngine
from app.recognition import ArrowTrackerStatus, TrackerStatus
from app.ui.events_tab import EventsTab
from app.ui.region_dialog import RegionDialog
from app.ui.video_tab import VideoTab, fit_display_size, next_playback_delay_ms
from app.video_source import VideoSource


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DDR Video Event Extractor - Milestone 1 (recorded video)")
        self.geometry("1500x900")

        self.ocr_engine = OcrEngine()
        self.event_bus = EventBus(source_mode=config.SOURCE_MODE_REPLAY)
        self.analysis = AnalysisSession(self.event_bus, self.ocr_engine)

        self.video_source: Optional[VideoSource] = None
        self.regions = []  # list[Region]
        self.is_playing = False
        self._play_after_id: Optional[str] = None
        self._playback_started_at = 0.0
        self._playback_start_frame = 0
        self._last_frame_bgr = None  # last displayed frame; used for arrow reference capture
        self._analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._analysis_future: Optional[Future] = None
        self._analysis_future_generation = 0
        self._analysis_generation = 0
        self._analysis_poll_after_id: Optional[str] = None
        self._dropped_analysis_frames = 0
        self._calibration_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="score-calibration"
        )
        self._calibration_future: Optional[Future] = None
        self._calibration_poll_after_id: Optional[str] = None
        self._calibration_attempted_keys = set()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        self.video_tab = VideoTab(notebook, self)
        self.events_tab = EventsTab(notebook, self)
        notebook.add(self.video_tab, text="Video & Regions")
        notebook.add(self.events_tab, text="Events")

        self.video_tab.refresh_layout_names()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._start_ocr_loading()

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
    # Video loading / playback
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

        if self.video_source is not None:
            self.video_source.close()
        self.video_source = new_source
        self._calibration_attempted_keys = set()

        # A new video is a new session: reset everything recognition-related.
        self._analysis_generation += 1
        self.analysis.stop()
        self.event_bus.new_session()
        self.analysis.reset_tracking()
        self.events_tab.clear()
        self.video_tab.set_analysis_status(False)
        self.video_tab.set_error_status("")

        self.video_tab.set_video_info(
            f"{os.path.basename(path)}  |  {new_source.width}x{new_source.height}  |  "
            f"{new_source.fps:.2f} fps  |  {new_source.frame_count} frames"
        )

        frame, idx, _ = new_source.read_next()
        if frame is None:
            messagebox.showerror("Empty video", "Could not read any frames from this video.")
            return
        self._process_and_update(frame, idx, new_source.video_time_ms_for_frame(idx), is_seek=False)
        self._start_score_template_calibration()

    def step_forward(self) -> None:
        if self.video_source is None or self.is_playing:
            return
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            messagebox.showinfo("End of video", "No more frames.")
            return
        self._process_and_update(frame, idx, self.video_source.video_time_ms_for_frame(idx), is_seek)

    def step_backward(self) -> None:
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
            messagebox.showerror("No video", "Open a video first.")
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
                self.video_source.skip_to_frame(target_frame)
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            self._stop_playing()
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

    # ------------------------------------------------------------------
    # Frame processing + display
    def _process_and_update(self, frame_bgr, frame_index: int, video_time_ms: int, is_seek: bool) -> None:
        if is_seek:
            self._analysis_generation += 1
            self.analysis.reset_tracking()

        self._last_frame_bgr = frame_bgr  # available for arrow reference capture regardless of analysis state
        self._display_current_frame(frame_bgr)
        self.video_tab.set_frame_position(frame_index, self.video_source.frame_count, video_time_ms)

        arrow_result = self.analysis.process_arrow_frame(frame_bgr, frame_index, video_time_ms)
        self._apply_analysis_result(arrow_result, skipped_frames=0)
        score_result = self.analysis.process_fast_score_frame(
            frame_bgr, frame_index, video_time_ms
        )
        self._apply_analysis_result(score_result, skipped_frames=0)
        self._submit_numeric_analysis(frame_bgr, frame_index, video_time_ms)

    def _submit_numeric_analysis(self, frame_bgr, frame_index: int, video_time_ms: int) -> None:
        if not self.analysis.is_running:
            return
        if not any(
            region.kind == "combo"
            or (
                region.kind == "score"
                and not self.analysis.has_complete_score_templates(region)
            )
            for region in self.regions
        ):
            return
        if self._analysis_future is not None:
            self._dropped_analysis_frames += 1
            return
        self._analysis_future_generation = self._analysis_generation
        self._analysis_future = self._analysis_executor.submit(
            self.analysis.process_background_numeric_frame,
            frame_bgr,
            frame_index,
            video_time_ms,
        )
        self._analysis_poll_after_id = self.after(10, self._poll_analysis_result)

    def _poll_analysis_result(self) -> None:
        self._analysis_poll_after_id = None
        future = self._analysis_future
        if future is None:
            return
        if not future.done():
            self._analysis_poll_after_id = self.after(10, self._poll_analysis_result)
            return

        self._analysis_future = None
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - worker failures must be visible in the UI
            self.video_tab.set_error_status(f"Analysis worker error: {exc}")
            return
        if self._analysis_future_generation != self._analysis_generation:
            return
        self._apply_analysis_result(result, self._dropped_analysis_frames)
        self._dropped_analysis_frames = 0

    def _apply_analysis_result(
        self, result, skipped_frames: int
    ) -> None:
        if result is None:
            return

        is_numeric_result = (
            bool(result.region_results) and result.region_results[0].region.kind != "arrow"
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
        photo = self._make_thumbnail_photo(rr.crop)
        if rr.region.kind == "arrow":
            tracker_status: ArrowTrackerStatus = self.analysis.tracker_for(rr.region).status()
            status_text = self._format_arrow_status(rr, tracker_status)
            self.video_tab.update_arrow_recognition_row(
                rr.region.key(), photo, rr.arrow_state, rr.confidence, status_text
            )
        else:
            tracker_status: TrackerStatus = self.analysis.tracker_for(rr.region).status()
            status_text = self._format_numeric_status(rr, tracker_status)
            self.video_tab.update_recognition_row(
                rr.region.key(), photo, rr.raw_text, rr.candidate_value, rr.accepted_value, status_text
            )

    @staticmethod
    def _make_thumbnail_photo(crop) -> Optional[ImageTk.PhotoImage]:
        if crop is None or crop.size == 0:
            return None
        crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(crop_rgb)
        w, h = pil_img.size
        up = max(1, 120 // max(w, 1))
        if up > 1:
            pil_img = pil_img.resize((w * up, h * up), Image.NEAREST)
        return ImageTk.PhotoImage(pil_img)

    @staticmethod
    def _format_numeric_status(rr: RegionFrameResult, ts: TrackerStatus) -> str:
        if rr.error:
            return f"OCR error: {rr.error}"
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
        if region.kind == "score":
            self._start_score_template_calibration()

    def delete_region(self, index: int) -> None:
        if 0 <= index < len(self.regions):
            del self.regions[index]
            self._analysis_generation += 1
            self.analysis.set_regions(self.regions)
            self.video_tab.refresh_region_list(self.regions)
            self.video_tab.redraw_region_overlays(self.regions)

    # ------------------------------------------------------------------
    # Arrow lane calibration (capture hit/miss reference snapshots)
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

        photo = self._make_thumbnail_photo(crop)
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

        self.regions = list(profile.regions)
        self._analysis_generation += 1
        self.analysis.set_regions(self.regions)
        self.video_tab.refresh_region_list(self.regions)
        self.video_tab.redraw_region_overlays(self.regions)
        self._refresh_arrow_reference_thumbnails()
        self._start_score_template_calibration()

    def _start_score_template_calibration(self) -> None:
        if self.video_source is None or self._calibration_future is not None:
            return
        region = next(
            (
                candidate
                for candidate in self.regions
                if candidate.kind == "score"
                and not self.analysis.has_complete_score_templates(candidate)
                and candidate.key() not in self._calibration_attempted_keys
            ),
            None,
        )
        if region is None:
            return
        self.video_tab.set_processing_status(
            f"Calibrating fast score digits for {region.label} in the background..."
        )
        self._calibration_attempted_keys.add(region.key())
        self._calibration_future = self._calibration_executor.submit(
            self._calibrate_score_region,
            self.video_source.path,
            self.video_source.frame_count,
            region,
        )
        self._calibration_poll_after_id = self.after(
            100, self._poll_score_template_calibration
        )

    def _calibrate_score_region(self, video_path: str, frame_count: int, region: Region):
        templates = {}
        for digit, encoded_images in region.digit_templates.items():
            decoded = []
            for encoded in encoded_images:
                image = image_utils.decode_base64_png_to_crop(encoded)
                if image is not None:
                    decoded.append(image[:, :, 0] if image.ndim == 3 else image)
            if decoded:
                templates[digit] = decoded

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise IOError(f"Could not open video for score calibration: {video_path}")
        try:
            sample_count = min(120, max(frame_count, 1))
            for sample_index in range(sample_count):
                frame_index = (
                    0
                    if sample_count == 1
                    else round(sample_index * (frame_count - 1) / (sample_count - 1))
                )
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = capture.read()
                if not ok:
                    continue
                frame_h, frame_w = frame.shape[:2]
                x0, y0 = max(0, region.x), max(0, region.y)
                x1 = min(region.x + region.w, frame_w)
                y1 = min(region.y + region.h, frame_h)
                if x1 <= x0 or y1 <= y0:
                    continue
                for digit, glyph in self.ocr_engine.learn_score_templates(
                    frame[y0:y1, x0:x1]
                ):
                    examples = templates.setdefault(digit, [])
                    if len(examples) >= 3:
                        continue
                    if any(
                        float(cv2.absdiff(glyph, example).mean()) < 3.0
                        for example in examples
                    ):
                        continue
                    examples.append(glyph)
                if all(len(templates.get(digit, [])) >= 3 for digit in "0123456789"):
                    break
        finally:
            capture.release()
        return region.key(), templates

    def _poll_score_template_calibration(self) -> None:
        self._calibration_poll_after_id = None
        future = self._calibration_future
        if future is None:
            return
        if not future.done():
            self._calibration_poll_after_id = self.after(
                100, self._poll_score_template_calibration
            )
            return
        self._calibration_future = None
        try:
            region_key, templates = future.result()
        except Exception as exc:  # noqa: BLE001 - worker failures must be visible
            self.video_tab.set_error_status(f"Score calibration error: {exc}")
            return

        region = next((item for item in self.regions if item.key() == region_key), None)
        if region is None:
            return
        region.digit_templates = {
            digit: [
                image_utils.encode_crop_to_base64_png(template)
                for template in examples
            ]
            for digit, examples in templates.items()
        }
        self._analysis_generation += 1
        self.analysis.set_regions(self.regions)
        learned_digits = sum(
            1 for digit in "0123456789"
            if len(region.digit_templates.get(digit, [])) >= 3
        )
        if learned_digits == 10:
            self.video_tab.set_processing_status(
                f"Fast score calibration ready for {region.label}. "
                "Save the layout to keep these templates."
            )
        else:
            self.video_tab.set_error_status(
                f"Score calibration learned {learned_digits}/10 digits for {region.label}."
            )
        self._start_score_template_calibration()

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
        """Show any previously-captured hit/miss reference images for arrow
        regions right after a layout is loaded, without waiting for a new
        frame to be processed."""
        for region in self.regions:
            if region.kind != "arrow":
                continue
            for state, encoded_images in region.reference_images.items():
                if not encoded_images:
                    continue
                img = image_utils.decode_base64_png_to_crop(encoded_images[-1])
                photo = self._make_thumbnail_photo(img)
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
                messagebox.showerror("No video", "Open a video first.")
                return
            if not self.regions:
                messagebox.showwarning(
                    "No regions", "Add at least one region (score, combo, or arrow) before starting analysis."
                )
                return
            self.analysis.start()
        self.video_tab.set_analysis_status(self.analysis.is_running)

    def new_session(self) -> None:
        self._analysis_generation += 1
        self.event_bus.new_session()
        self.analysis.reset_tracking()
        self.events_tab.clear()
        self.video_tab.set_error_status("")
        self.video_tab.set_processing_status("New session started - recognition state reset.")

    # ------------------------------------------------------------------
    # Events tab support
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
        self._stop_playing()
        if self._analysis_poll_after_id is not None:
            self.after_cancel(self._analysis_poll_after_id)
            self._analysis_poll_after_id = None
        self._analysis_executor.shutdown(wait=False, cancel_futures=True)
        if self._calibration_poll_after_id is not None:
            self.after_cancel(self._calibration_poll_after_id)
            self._calibration_poll_after_id = None
        self._calibration_executor.shutdown(wait=False, cancel_futures=True)
        if self.video_source is not None:
            self.video_source.close()
        self.destroy()


def run() -> None:
    app = App()
    app.mainloop()
