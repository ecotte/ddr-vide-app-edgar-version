"""Main application window: wires video, regions, recognition, and events."""
from __future__ import annotations

import os
import threading
import tkinter as tk
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
from app.ui.video_tab import MAX_DISPLAY_H, MAX_DISPLAY_W, VideoTab
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
        self._last_frame_bgr = None  # last displayed frame; used for arrow reference capture

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

        # A new video is a new session: reset everything recognition-related.
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
        self.is_playing = True
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
        frame, idx, is_seek = self.video_source.read_next()
        if frame is None:
            self._stop_playing()
            self.video_tab.set_error_status("Reached end of video.")
            return
        self._process_and_update(frame, idx, self.video_source.video_time_ms_for_frame(idx), is_seek)
        # While analysis is running, OCR time dominates so we just go as fast
        # as possible; otherwise pace roughly to the source frame rate so
        # preview playback looks natural. Either way this is not a
        # frame-accurate real-time guarantee - see README.
        if self.analysis.is_running:
            delay = 1
        else:
            delay = int(1000 / self.video_source.fps) if self.video_source.fps > 0 else 33
        self._schedule_play_tick(delay)

    # ------------------------------------------------------------------
    # Frame processing + display
    def _process_and_update(self, frame_bgr, frame_index: int, video_time_ms: int, is_seek: bool) -> None:
        if is_seek:
            self.analysis.reset_tracking()

        self._last_frame_bgr = frame_bgr  # available for arrow reference capture regardless of analysis state
        self._display_current_frame(frame_bgr)
        self.video_tab.set_frame_position(frame_index, self.video_source.frame_count, video_time_ms)

        result = self.analysis.process_frame(frame_bgr, frame_index, video_time_ms)
        if result is None:
            return

        self.video_tab.set_processing_status(
            f"Last recognition pass: {result.processing_ms:.0f} ms across {len(result.region_results)} region(s)"
        )
        errors = [rr.error for rr in result.region_results if rr.error]
        self.video_tab.set_error_status("; ".join(errors) if errors else "")

        for rr in result.region_results:
            self._update_recognition_row(rr)
        for event in result.new_events:
            self.events_tab.append_event(event)

    def _display_current_frame(self, frame_bgr) -> None:
        src_h, src_w = frame_bgr.shape[:2]
        scale = min(1.0, MAX_DISPLAY_W / src_w, MAX_DISPLAY_H / src_h)
        disp_w, disp_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        if scale != 1.0:
            pil_img = pil_img.resize((disp_w, disp_h), Image.BILINEAR)
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
        self.analysis.set_regions(self.regions)
        self.video_tab.refresh_region_list(self.regions)
        self.video_tab.redraw_region_overlays(self.regions)

    def delete_region(self, index: int) -> None:
        if 0 <= index < len(self.regions):
            del self.regions[index]
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
        region.reference_images[state] = image_utils.encode_crop_to_base64_png(crop)
        self.analysis.set_regions(self.regions)  # reload decoded references, resets tracking

        photo = self._make_thumbnail_photo(crop)
        self.video_tab.set_arrow_reference_thumbnail(region.key(), state, photo)
        self.video_tab.set_processing_status(
            f"Captured '{state}' reference for {region.label} at the current frame."
        )

    def clear_arrow_reference(self, region: Region, state: str) -> None:
        if state in region.reference_images:
            del region.reference_images[state]
            self.analysis.set_regions(self.regions)
        self.video_tab.set_arrow_reference_thumbnail(region.key(), state, None)

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
                f"{exc}\n\nRescale the saved region coordinates to fit the current video "
                "and load anyway? Choose 'No' to cancel and keep your current regions.",
            )
            if not proceed:
                return
            profile = layout_store.rescale_profile(
                exc.profile, self.video_source.width, self.video_source.height
            )

        self.regions = list(profile.regions)
        self.analysis.set_regions(self.regions)
        self.video_tab.refresh_region_list(self.regions)
        self.video_tab.redraw_region_overlays(self.regions)
        self._refresh_arrow_reference_thumbnails()

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
            for state, b64 in region.reference_images.items():
                img = image_utils.decode_base64_png_to_crop(b64)
                photo = self._make_thumbnail_photo(img)
                self.video_tab.set_arrow_reference_thumbnail(region.key(), state, photo)

    # ------------------------------------------------------------------
    # Analysis controls
    def set_consistency_frames(self, n: int) -> None:
        self.analysis.set_required_consecutive_frames(n)

    def toggle_analysis(self) -> None:
        if self.analysis.is_running:
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
        if self.video_source is not None:
            self.video_source.close()
        self.destroy()


def run() -> None:
    app = App()
    app.mainloop()
