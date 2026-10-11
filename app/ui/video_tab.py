"""Video playback, region calibration, and live recognition panel."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import List, Optional

from PIL import Image, ImageTk

from app import config

MAX_DISPLAY_W = 960
MAX_DISPLAY_H = 540


def fit_display_size(
    source_width: int,
    source_height: int,
    max_width: int,
    max_height: int,
) -> tuple[int, int, float]:
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Source dimensions must be positive.")
    if max_width <= 0 or max_height <= 0:
        raise ValueError("Display dimensions must be positive.")
    scale = min(1.0, max_width / source_width, max_height / source_height)
    return (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
        scale,
    )


def next_playback_delay_ms(fps: float, processing_seconds: float) -> int:
    frame_interval_ms = 1000.0 / fps if fps > 0 else 33.0
    processing_ms = max(0.0, processing_seconds * 1000.0)
    return max(1, round(frame_interval_ms - processing_ms))


class _ScrollableFrame(ttk.Frame):
    """A vertically scrollable container.

    Put widgets inside ``.inner`` rather than directly inside the frame
    this class returns. This exists because this tab's control panels grow
    over time (layout profile controls, analysis settings, one recognition
    row per calibrated region...) and a plain ``ttk.Frame`` has no way to
    reach content that no longer fits the window - it is silently pushed
    below the visible area with no scrollbar and no indication anything is
    missing. Wrapping in a scrollable canvas means every control stays
    reachable no matter the window size or how many regions are calibrated.
    """

    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.inner = ttk.Frame(self.canvas)
        self._window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        # Keep the inner frame's width tracking the visible canvas width so
        # widgets packed/gridded with fill="x" still stretch as expected.
        self.canvas.itemconfigure(self._window_id, width=event.width)

    def _bind_mousewheel(self, event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class VideoTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, controller):
        super().__init__(parent, padding=6)
        self.controller = controller
        self.display_scale = 1.0
        self._draw_mode = False
        self._rect_start = None
        self._rect_id = None
        self._overlay_ids: List[int] = []
        self._region_thumb_refs = {}  # keep PhotoImage refs alive, keyed by region key
        self._photo_image = None  # keep current frame PhotoImage alive
        self._region_rows = {}  # region key -> dict of widgets
        self._suppress_slider_event = False

        self._build_layout()

    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        left_scroll = _ScrollableFrame(self)
        left_scroll.grid(row=0, column=0, sticky="nsew")
        self._left_scroll = left_scroll
        left = left_scroll.inner

        right_scroll = _ScrollableFrame(self)
        right_scroll.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right = right_scroll.inner

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # --- top: open video + resolution/time info -----------------
        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Button(top, text="Open Video...", command=self.controller.open_video_dialog).pack(
            side="left"
        )
        self.video_info_var = tk.StringVar(value="No video loaded.")
        ttk.Label(top, textvariable=self.video_info_var).pack(side="left", padx=10)

        # --- canvas ---------------------------------------------------
        self.canvas = tk.Canvas(left, width=MAX_DISPLAY_W, height=MAX_DISPLAY_H, bg="#222222")
        self.canvas.pack(pady=6)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

        # --- playback controls -----------------------------------------
        playback = ttk.Frame(left)
        playback.pack(fill="x", pady=(0, 6))
        ttk.Button(playback, text="|< Step", command=self.controller.step_backward).pack(side="left")
        self.play_button = ttk.Button(playback, text="Play", command=self.controller.toggle_play)
        self.play_button.pack(side="left", padx=4)
        ttk.Button(playback, text="Step >|", command=self.controller.step_forward).pack(side="left")

        self.frame_pos_var = tk.StringVar(value="Frame -/- | 0 ms")
        ttk.Label(playback, textvariable=self.frame_pos_var).pack(side="left", padx=10)

        self.seek_scale = ttk.Scale(
            left, from_=0, to=1, orient="horizontal", command=self._on_seek_scale
        )
        self.seek_scale.pack(fill="x")

        # --- region calibration controls --------------------------------
        region_box = ttk.LabelFrame(left, text="Regions", padding=6)
        region_box.pack(fill="x", pady=6)

        self.draw_mode_button = ttk.Button(
            region_box, text="Draw Region: OFF", command=self._toggle_draw_mode
        )
        self.draw_mode_button.grid(row=0, column=0, sticky="w")
        ttk.Button(region_box, text="Delete Selected", command=self._delete_selected_region).grid(
            row=0, column=1, padx=6
        )

        self.region_listbox = tk.Listbox(region_box, height=5, width=50)
        self.region_listbox.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)

        # --- layout profile controls -------------------------------------
        layout_box = ttk.LabelFrame(left, text="Layout profile", padding=6)
        layout_box.pack(fill="x", pady=6)

        ttk.Label(layout_box, text="Name:").grid(row=0, column=0, sticky="w")
        self.layout_name_var = tk.StringVar()
        ttk.Entry(layout_box, textvariable=self.layout_name_var, width=24).grid(row=0, column=1, padx=4)
        ttk.Button(layout_box, text="Save Layout", command=self._on_save_layout).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(layout_box, text="Save Layout As...", command=self._on_save_layout_as).grid(
            row=0, column=3, padx=4
        )

        ttk.Label(layout_box, text="Load:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.layout_choice_var = tk.StringVar()
        self.layout_combo = ttk.Combobox(
            layout_box, textvariable=self.layout_choice_var, state="readonly", width=22
        )
        self.layout_combo.grid(row=1, column=1, padx=4, pady=(4, 0))
        ttk.Button(layout_box, text="Load Layout", command=self._on_load_layout).grid(
            row=1, column=2, padx=4, pady=(4, 0)
        )
        ttk.Button(layout_box, text="Refresh List", command=self.refresh_layout_names).grid(
            row=1, column=3, padx=4, pady=(4, 0)
        )
        ttk.Button(layout_box, text="Load Layout From...", command=self._on_load_layout_from_file).grid(
            row=1, column=4, padx=4, pady=(4, 0)
        )

        # --- analysis controls -------------------------------------------
        analysis_box = ttk.LabelFrame(left, text="Analysis", padding=6)
        analysis_box.pack(fill="x", pady=6)

        ttk.Label(analysis_box, text="Consistency frames:").grid(row=0, column=0, sticky="w")
        self.consistency_var = tk.IntVar(value=config.DEFAULT_REQUIRED_CONSECUTIVE_FRAMES)
        consistency_spin = ttk.Spinbox(
            analysis_box, from_=1, to=15, width=4, textvariable=self.consistency_var,
            command=self._on_consistency_changed,
        )
        consistency_spin.grid(row=0, column=1, sticky="w")
        consistency_spin.bind("<Return>", lambda e: self._on_consistency_changed())

        ttk.Label(analysis_box, text="Arrow similarity threshold:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.arrow_threshold_var = tk.DoubleVar(value=config.DEFAULT_ARROW_SIMILARITY_THRESHOLD)
        arrow_threshold_spin = ttk.Spinbox(
            analysis_box, from_=0.50, to=1.00, increment=0.01, width=5,
            textvariable=self.arrow_threshold_var, command=self._on_arrow_threshold_changed,
        )
        arrow_threshold_spin.grid(row=1, column=1, sticky="w", pady=(4, 0))
        arrow_threshold_spin.bind("<Return>", lambda e: self._on_arrow_threshold_changed())

        ttk.Label(analysis_box, text="Arrow confirm frames:").grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.arrow_confirm_var = tk.IntVar(value=config.DEFAULT_ARROW_MIN_CONFIRM_FRAMES)
        arrow_confirm_spin = ttk.Spinbox(
            analysis_box, from_=1, to=10, width=4, textvariable=self.arrow_confirm_var,
            command=self._on_arrow_confirm_changed,
        )
        arrow_confirm_spin.grid(row=1, column=3, sticky="w", pady=(4, 0))
        arrow_confirm_spin.bind("<Return>", lambda e: self._on_arrow_confirm_changed())

        self.start_stop_button = ttk.Button(
            analysis_box, text="Start Analysis", command=self.controller.toggle_analysis
        )
        self.start_stop_button.grid(row=0, column=2, padx=10)
        ttk.Button(analysis_box, text="New Session", command=self.controller.new_session).grid(
            row=0, column=3, padx=4
        )

        self.analysis_status_var = tk.StringVar(value="Analysis: stopped")
        ttk.Label(analysis_box, textvariable=self.analysis_status_var).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0)
        )

        # --- status bar ----------------------------------------------------
        status_box = ttk.Frame(left)
        status_box.pack(fill="x", pady=(6, 0))
        self.ocr_status_var = tk.StringVar(value="OCR engine: not loaded yet")
        ttk.Label(status_box, textvariable=self.ocr_status_var).pack(anchor="w")
        self.processing_status_var = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self.processing_status_var).pack(anchor="w")
        self.error_status_var = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self.error_status_var, foreground="red").pack(anchor="w")

        # --- right: recognition panel --------------------------------------
        ttk.Label(right, text="Recognition (crop / raw OCR / accepted value)", font=("", 10, "bold")).pack(
            anchor="w"
        )
        self.recognition_container = ttk.Frame(right)
        self.recognition_container.pack(fill="both", expand=True, pady=6)

    # ------------------------------------------------------------------
    # Canvas / video frame display
    def video_display_limits(self) -> tuple[int, int]:
        visible_width = self._left_scroll.canvas.winfo_width()
        if visible_width <= 1:
            visible_width = MAX_DISPLAY_W
        return max(1, min(MAX_DISPLAY_W, visible_width - 12)), MAX_DISPLAY_H

    def show_frame_image(self, pil_image: Image.Image, display_scale: float) -> None:
        self.display_scale = display_scale
        w, h = pil_image.size
        self.canvas.config(width=w, height=h)
        self._photo_image = ImageTk.PhotoImage(pil_image)
        self.canvas.delete("frame_image")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo_image, tags="frame_image")
        self.canvas.tag_lower("frame_image")

    def set_video_info(self, text: str) -> None:
        self.video_info_var.set(text)

    def set_frame_position(self, frame_index: int, frame_count: int, video_time_ms: int) -> None:
        self.frame_pos_var.set(f"Frame {frame_index}/{max(frame_count - 1, 0)} | {video_time_ms} ms")
        self._suppress_slider_event = True
        try:
            self.seek_scale.configure(to=max(frame_count - 1, 0))
            self.seek_scale.set(frame_index)
        finally:
            self._suppress_slider_event = False

    def set_play_button_label(self, is_playing: bool) -> None:
        self.play_button.config(text="Pause" if is_playing else "Play")

    def set_analysis_status(self, is_running: bool) -> None:
        self.analysis_status_var.set(f"Analysis: {'running' if is_running else 'stopped'}")
        self.start_stop_button.config(text="Stop Analysis" if is_running else "Start Analysis")

    def set_ocr_status(self, text: str) -> None:
        self.ocr_status_var.set(text)

    def set_processing_status(self, text: str) -> None:
        self.processing_status_var.set(text)

    def set_error_status(self, text: str) -> None:
        self.error_status_var.set(text)

    # ------------------------------------------------------------------
    # Region list + overlays
    def refresh_region_list(self, regions) -> None:
        self.region_listbox.delete(0, tk.END)
        for r in regions:
            self.region_listbox.insert(tk.END, f"{r.label}  [{r.player} / {r.kind}]  {r.w}x{r.h}")
        self._rebuild_recognition_rows(regions)

    def redraw_region_overlays(self, regions) -> None:
        for oid in self._overlay_ids:
            self.canvas.delete(oid)
        self._overlay_ids = []
        for r in regions:
            x0 = r.x * self.display_scale
            y0 = r.y * self.display_scale
            x1 = (r.x + r.w) * self.display_scale
            y1 = (r.y + r.h) * self.display_scale
            rect = self.canvas.create_rectangle(x0, y0, x1, y1, outline="#00ff00", width=2)
            label_text = f"{r.player}:{r.kind}" + (f":{r.direction}" if r.kind == "arrow" else "")
            text = self.canvas.create_text(
                x0 + 3, y0 + 10, text=label_text, anchor="w", fill="#00ff00"
            )
            self._overlay_ids.extend([rect, text])

    def _rebuild_recognition_rows(self, regions) -> None:
        for widget in self.recognition_container.winfo_children():
            widget.destroy()
        self._region_rows = {}
        for r in regions:
            if r.kind == "arrow":
                self._build_arrow_row(r)
            else:
                self._build_numeric_row(r)

    def _build_numeric_row(self, r) -> None:
        row = ttk.Frame(self.recognition_container, relief="groove", padding=4)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=f"{r.label} ({r.player}/{r.kind})", font=("", 9, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        thumb_label = ttk.Label(row)
        thumb_label.grid(row=1, column=0, rowspan=3, padx=(0, 8))
        raw_var = tk.StringVar(value="raw OCR: -")
        candidate_var = tk.StringVar(value="candidate: -")
        accepted_var = tk.StringVar(value="accepted: unknown")
        status_var = tk.StringVar(value="status: -")
        ttk.Label(row, textvariable=raw_var).grid(row=1, column=1, sticky="w")
        ttk.Label(row, textvariable=candidate_var).grid(row=2, column=1, sticky="w")
        ttk.Label(row, textvariable=accepted_var).grid(row=3, column=1, sticky="w")
        ttk.Label(row, textvariable=status_var).grid(row=4, column=1, sticky="w")
        self._region_rows[r.key()] = {
            "kind": "numeric",
            "thumb_label": thumb_label,
            "raw_var": raw_var,
            "candidate_var": candidate_var,
            "accepted_var": accepted_var,
            "status_var": status_var,
        }

    def _build_arrow_row(self, r) -> None:
        row = ttk.Frame(self.recognition_container, relief="groove", padding=4)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=f"{r.label} ({r.player}/arrow/{r.direction})", font=("", 9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w"
        )

        thumb_label = ttk.Label(row)
        thumb_label.grid(row=1, column=0, rowspan=3, padx=(0, 8))

        state_var = tk.StringVar(value="state: -")
        confidence_var = tk.StringVar(value="similarity: -")
        status_var = tk.StringVar(value="status: -")
        ttk.Label(row, textvariable=state_var).grid(row=1, column=1, sticky="w")
        ttk.Label(row, textvariable=confidence_var).grid(row=2, column=1, sticky="w")
        ttk.Label(row, textvariable=status_var).grid(row=3, column=1, sticky="w")

        ttk.Label(row, text="Hit ref:").grid(row=1, column=2, sticky="w", padx=(12, 0))
        hit_ref_label = ttk.Label(row, text="(none)")
        hit_ref_label.grid(row=1, column=3, sticky="w")
        ttk.Label(row, text="Miss ref:").grid(row=2, column=2, sticky="w", padx=(12, 0))
        miss_ref_label = ttk.Label(row, text="(none)")
        miss_ref_label.grid(row=2, column=3, sticky="w")

        btns = ttk.Frame(row)
        btns.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Button(
            btns, text="Add Hit ref", command=lambda reg=r: self.controller.capture_arrow_reference(reg, "hit")
        ).grid(row=0, column=0, padx=2)
        ttk.Button(
            btns, text="Clear Hit refs", command=lambda reg=r: self.controller.clear_arrow_reference(reg, "hit")
        ).grid(row=0, column=1, padx=2)
        ttk.Button(
            btns, text="Add Miss ref", command=lambda reg=r: self.controller.capture_arrow_reference(reg, "miss")
        ).grid(row=0, column=2, padx=2)
        ttk.Button(
            btns, text="Clear Miss refs", command=lambda reg=r: self.controller.clear_arrow_reference(reg, "miss")
        ).grid(row=0, column=3, padx=2)

        self._region_rows[r.key()] = {
            "kind": "arrow",
            "thumb_label": thumb_label,
            "state_var": state_var,
            "confidence_var": confidence_var,
            "status_var": status_var,
            "hit_ref_label": hit_ref_label,
            "miss_ref_label": miss_ref_label,
        }

    def update_recognition_row(self, region_key: str, crop_photo, raw_text, candidate, accepted, status_text) -> None:
        row = self._region_rows.get(region_key)
        if row is None or row.get("kind") != "numeric":
            return
        if crop_photo is not None:
            self._region_thumb_refs[region_key] = crop_photo
            row["thumb_label"].configure(image=crop_photo)
        row["raw_var"].set(f"raw OCR: {raw_text!r}" if raw_text else "raw OCR: (empty)")
        row["candidate_var"].set(f"candidate: {candidate if candidate is not None else 'unreadable'}")
        row["accepted_var"].set(f"accepted: {accepted if accepted is not None else 'unknown'}")
        row["status_var"].set(f"status: {status_text}")

    def update_arrow_recognition_row(
        self, region_key: str, crop_photo, observed_state, confidence, status_text
    ) -> None:
        row = self._region_rows.get(region_key)
        if row is None or row.get("kind") != "arrow":
            return
        if crop_photo is not None:
            self._region_thumb_refs[region_key] = crop_photo
            row["thumb_label"].configure(image=crop_photo)
        row["state_var"].set(f"state: {observed_state if observed_state is not None else 'unreadable'}")
        row["confidence_var"].set(
            f"similarity: {confidence:.2f}" if confidence is not None else "similarity: -"
        )
        row["status_var"].set(f"status: {status_text}")

    def set_arrow_reference_thumbnail(self, region_key: str, state: str, photo, count: int) -> None:
        row = self._region_rows.get(region_key)
        if row is None or row.get("kind") != "arrow":
            return
        label_key = "hit_ref_label" if state == "hit" else "miss_ref_label"
        label = row[label_key]
        if photo is None:
            label.configure(image="", text="(none)")
        else:
            self._region_thumb_refs[f"{region_key}:{state}_ref"] = photo
            label.configure(image=photo, text=f" {count} ref{'s' if count != 1 else ''}", compound="left")


    def refresh_layout_names(self) -> None:
        names = self.controller.list_layout_names()
        self.layout_combo.configure(values=names)

    # ------------------------------------------------------------------
    # Event handlers
    def _on_seek_scale(self, value) -> None:
        if self._suppress_slider_event:
            return
        self.controller.seek_to(int(float(value)))

    def _toggle_draw_mode(self) -> None:
        self._draw_mode = not self._draw_mode
        self.draw_mode_button.config(text=f"Draw Region: {'ON' if self._draw_mode else 'OFF'}")

    def _on_canvas_press(self, event) -> None:
        if not self._draw_mode:
            return
        self._rect_start = (event.x, event.y)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ffcc00", width=2
        )

    def _on_canvas_drag(self, event) -> None:
        if not self._draw_mode or self._rect_id is None:
            return
        x0, y0 = self._rect_start
        self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _on_canvas_release(self, event) -> None:
        if not self._draw_mode or self._rect_id is None:
            return
        x0, y0 = self._rect_start
        x1, y1 = event.x, event.y
        self.canvas.delete(self._rect_id)
        self._rect_id = None
        self._rect_start = None

        cx0, cx1 = sorted((x0, x1))
        cy0, cy1 = sorted((y0, y1))
        if (cx1 - cx0) < 4 or (cy1 - cy0) < 4:
            return  # too small to be a meaningful region; ignore accidental clicks

        scale = self.display_scale or 1.0
        sx0, sy0 = int(cx0 / scale), int(cy0 / scale)
        sw, sh = int((cx1 - cx0) / scale), int((cy1 - cy0) / scale)
        self.controller.on_region_drawn(sx0, sy0, sw, sh)

    def _delete_selected_region(self) -> None:
        sel = self.region_listbox.curselection()
        if not sel:
            return
        self.controller.delete_region(sel[0])

    def _on_save_layout(self) -> None:
        name = self.layout_name_var.get().strip()
        if not name:
            self.set_error_status("Enter a layout profile name before saving.")
            return
        self.controller.save_layout(name)

    def _on_save_layout_as(self) -> None:
        suggested_name = self.layout_name_var.get().strip() or "layout"
        path = filedialog.asksaveasfilename(
            title="Save layout as",
            defaultextension=".json",
            filetypes=[("Layout JSON", "*.json"), ("All files", "*.*")],
            initialdir=self.controller.layouts_dir(),
            initialfile=f"{suggested_name}.json",
        )
        if not path:
            return
        self.controller.save_layout_to_path(path)

    def _on_load_layout(self) -> None:
        name = self.layout_choice_var.get().strip()
        if not name:
            self.set_error_status("Choose a layout profile to load.")
            return
        self.controller.load_layout(name)

    def _on_load_layout_from_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load layout from file",
            filetypes=[("Layout JSON", "*.json"), ("All files", "*.*")],
            initialdir=self.controller.layouts_dir(),
        )
        if not path:
            return
        self.controller.load_layout_from_path(path)

    def _on_consistency_changed(self) -> None:
        try:
            n = int(self.consistency_var.get())
        except (tk.TclError, ValueError):
            return
        self.controller.set_consistency_frames(n)

    def _on_arrow_threshold_changed(self) -> None:
        try:
            value = float(self.arrow_threshold_var.get())
        except (tk.TclError, ValueError):
            return
        self.controller.set_arrow_similarity_threshold(value)

    def _on_arrow_confirm_changed(self) -> None:
        try:
            n = int(self.arrow_confirm_var.get())
        except (tk.TclError, ValueError):
            return
        self.controller.set_arrow_min_confirm_frames(n)
