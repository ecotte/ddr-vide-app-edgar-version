"""Video playback, region calibration, and live recognition panel."""
from __future__ import annotations

import tkinter as tk
import time
from tkinter import filedialog, ttk
from typing import List, Optional

from PIL import Image, ImageTk

from app import config, image_utils
from app.models import arrow_search_bounds, judgment_search_bounds
from app.ui.theme import FIELD, MUTED

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
    """A vertically scrollable container with optional horizontal scrolling.

    Put widgets inside ``.inner`` rather than directly inside the frame
    this class returns. This exists because this tab's control panels grow
    over time (layout profile controls, analysis settings, one recognition
    row per calibrated region...) and a plain ``ttk.Frame`` has no way to
    reach content that no longer fits the window - it is silently pushed
    below the visible area with no scrollbar and no indication anything is
    missing. Wrapping in a scrollable canvas means every control stays
    reachable no matter the window size or how many regions are calibrated.
    """

    def __init__(self, parent: tk.Widget, *, horizontal: bool = False):
        super().__init__(parent)
        self._horizontal = horizontal
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        if horizontal:
            self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
            self.hbar.grid(row=1, column=0, sticky="ew")
            self.canvas.configure(xscrollcommand=self.hbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.inner = ttk.Frame(self.canvas)
        self._window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_configure(self, event) -> None:
        width = self.canvas.winfo_width()
        if self._horizontal:
            width = max(width, self.inner.winfo_reqwidth())
        self.canvas.itemconfigure(self._window_id, width=width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _bind_mousewheel(self, event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class ReferenceGalleryDialog(tk.Toplevel):
    """Inspect and remove individual image samples for one region."""

    def __init__(self, parent, controller, region, state: str):
        super().__init__(parent)
        self.controller = controller
        self.region = region
        self.state = state
        self._photos = []
        category = "No judgment" if region.kind == "judgment" and state == "empty" else state.title()
        self.title(f"{region.player} {region.direction or region.kind} - {category} references")
        self.geometry("620x500")
        self.minsize(440, 300)
        self.count_var = tk.StringVar(self)
        ttk.Label(self, textvariable=self.count_var, style="Section.TLabel").pack(
            anchor="w", padx=12, pady=12,
        )
        self.images = _ScrollableFrame(self)
        self.images.pack(fill="both", expand=True, padx=12)
        ttk.Label(
            self, text="Delete removes only this player's selected sample. "
            "Close this window and Save Layout to keep changes.",
            wraplength=560,
        ).pack(anchor="w", padx=12, pady=8)
        ttk.Button(self, text="Close", command=self.destroy).pack(anchor="e", padx=12, pady=(0, 12))
        self._refresh_images()
        self.transient(parent)
        self.grab_set()

    def _refresh_images(self) -> None:
        for widget in self.images.inner.winfo_children():
            widget.destroy()
        self._photos.clear()
        images = self.region.reference_images.get(self.state, [])
        self.count_var.set(f"{len(images)} reference{'s' if len(images) != 1 else ''}")
        if not images:
            ttk.Label(self.images.inner, text="No references captured for this box.").grid(
                row=0, column=0, sticky="w", pady=12,
            )
            return
        for column in range(2):
            self.images.inner.columnconfigure(column, weight=1, uniform="references")
        for index, encoded in enumerate(images):
            card = ttk.LabelFrame(
                self.images.inner, text=f"Reference {index + 1}", padding=8,
            )
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4)
            crop = image_utils.decode_base64_png_to_crop(encoded)
            if crop is None:
                ttk.Label(card, text="Cannot decode this reference image.", wraplength=180).pack()
            else:
                photo = self.controller._make_thumbnail_photo(crop, max_width=160, max_height=120)
                self._photos.append(photo)
                ttk.Label(card, image=photo).pack(pady=4)
                ttk.Label(card, text=f"{crop.shape[1]}x{crop.shape[0]} pixels").pack()
            ttk.Button(
                card, text=f"Delete reference {index + 1}",
                command=lambda selected=index: self._delete_reference(selected),
            ).pack(pady=(8, 0))

    def _delete_reference(self, index: int) -> None:
        if self.controller.delete_arrow_reference(self.region, self.state, index):
            self._refresh_images()


class VideoTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, controller):
        super().__init__(parent, padding=12)
        self.controller = controller
        self.display_scale = 1.0
        self._draw_mode = False
        self._rect_start = None
        self._rect_id = None
        self._move_region = None
        self._move_start = None
        self._overlay_ids: List[int] = []
        self._region_thumb_refs = {}  # keep PhotoImage refs alive, keyed by region key
        self._photo_image = None  # keep current frame PhotoImage alive
        self._frame_image_id = None
        self._frame_image_size = None
        self._overlay_signature = None
        self._region_rows = {}  # region key -> dict of widgets
        self._suppress_slider_event = False
        self._is_live = False
        self._capture_pending = False
        self._capture_connected = False
        self._recording_state = "idle"

        self._build_layout()

    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        left_scroll = _ScrollableFrame(self, horizontal=True)
        left_scroll.grid(row=0, column=0, sticky="nsew")
        self._left_scroll = left_scroll
        left = left_scroll.inner

        right_scroll = _ScrollableFrame(self)
        right_scroll.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right = right_scroll.inner

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # --- source selection -----------------------------------------
        top = ttk.LabelFrame(left, text="Video source", padding=8)
        top.pack(fill="x")
        selector = ttk.Frame(top)
        selector.pack(fill="x")
        ttk.Label(selector, text="Source:").pack(side="left", padx=(0, 8))
        self.source_var = tk.StringVar(value="Video file")
        self.source_combo = ttk.Combobox(
            selector, textvariable=self.source_var, values=("Video file", "HDMI input"),
            state="readonly", width=16,
        )
        self.source_combo.pack(side="left")
        self.source_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.controller.select_source(self.source_var.get()),
        )
        self.file_controls = ttk.Frame(top)
        self.file_controls.pack(fill="x", pady=(8, 0))
        ttk.Button(
            self.file_controls, text="Open Video...", style="Accent.TButton",
            command=self.controller.open_video_dialog,
        ).pack(side="left")
        self.hdmi_controls = ttk.Frame(top)
        self.capture_device_var = tk.StringVar()
        self.capture_combo = ttk.Combobox(
            self.hdmi_controls, textvariable=self.capture_device_var, state="readonly", width=30,
        )
        self.capture_combo.grid(row=0, column=0, sticky="ew")
        self.capture_combo.bind("<<ComboboxSelected>>", lambda e: self._update_capture_controls())
        self.hdmi_controls.columnconfigure(0, weight=1)
        self.refresh_devices_button = ttk.Button(
            self.hdmi_controls, text="Refresh devices", command=self.controller.refresh_capture_devices,
        )
        self.refresh_devices_button.grid(row=0, column=1, padx=(6, 0))
        self.connect_hdmi_button = ttk.Button(
            self.hdmi_controls, text="Connect HDMI", command=self.controller.toggle_hdmi,
            style="Accent.TButton", state="disabled",
        )
        self.connect_hdmi_button.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.record_video_button = ttk.Button(
            self.hdmi_controls, text="Record MP4", command=self.controller.toggle_video_recording,
            state="disabled",
        )
        self.record_video_button.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(
            self.hdmi_controls, text="Select your HDMI capture card, not the built-in camera.",
            style="Muted.TLabel", wraplength=420,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.recording_status_var = tk.StringVar()
        ttk.Label(
            self.hdmi_controls, textvariable=self.recording_status_var,
            style="Muted.TLabel", wraplength=420,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.video_info_var = tk.StringVar(value="No video loaded.")
        self.video_info_label = ttk.Label(
            top, textvariable=self.video_info_var, style="Muted.TLabel", wraplength=520,
        )
        self.video_info_label.pack(fill="x", pady=(8, 0))

        # --- canvas ---------------------------------------------------
        self.canvas = tk.Canvas(
            left, width=MAX_DISPLAY_W, height=MAX_DISPLAY_H, bg=FIELD,
            highlightthickness=0, borderwidth=0,
        )
        self.canvas.create_text(
            24, 32, anchor="nw", text="Your video workspace",
            fill=MUTED, font=("Segoe UI", 18, "bold"), tags="empty_state",
        )
        self.canvas.create_text(
            24, 72, anchor="nw", text="Open a recording or connect an HDMI capture device.",
            fill=MUTED, tags="empty_state",
        )
        self.canvas.pack(pady=6)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

        # --- playback controls -----------------------------------------
        playback = ttk.Frame(left)
        playback.pack(fill="x", pady=(0, 6))
        self.step_back_button = ttk.Button(playback, text="|< Step", command=self.controller.step_backward)
        self.step_back_button.pack(side="left")
        self.play_button = ttk.Button(playback, text="Play", command=self.controller.toggle_play)
        self.play_button.pack(side="left", padx=4)
        self.step_forward_button = ttk.Button(playback, text="Step >|", command=self.controller.step_forward)
        self.step_forward_button.pack(side="left")

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
        ttk.Button(
            region_box, text="Match P2 sizes to P1", command=self.controller.match_player_region_sizes,
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(
            region_box, text="Matching player boxes use P1 dimensions when drawn or loaded. "
            "With Draw Region OFF, drag an existing box to move it without resizing.",
            wraplength=480, style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

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
        ttk.Label(
            layout_box, text="Layouts include all arrow/game-end references and taught score/combo digits.",
            style="Muted.TLabel", wraplength=480,
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

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

        ttk.Label(analysis_box, text="Arrow shape similarity:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.arrow_threshold_var = tk.DoubleVar(value=config.DEFAULT_ARROW_SIMILARITY_THRESHOLD)
        arrow_threshold_spin = ttk.Spinbox(
            analysis_box, from_=0.50, to=1.00, increment=0.01, width=5,
            textvariable=self.arrow_threshold_var, command=self._on_arrow_threshold_changed,
        )
        arrow_threshold_spin.grid(row=1, column=1, sticky="w", pady=(4, 0))
        arrow_threshold_spin.bind("<Return>", lambda e: self._on_arrow_threshold_changed())

        ttk.Label(analysis_box, text="Judgment shape similarity:").grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.judgment_threshold_var = tk.DoubleVar(value=config.DEFAULT_JUDGMENT_SIMILARITY_THRESHOLD)
        judgment_threshold_spin = ttk.Spinbox(
            analysis_box, from_=0.50, to=1.00, increment=0.01, width=5,
            textvariable=self.judgment_threshold_var, command=self._on_judgment_threshold_changed,
        )
        judgment_threshold_spin.grid(row=1, column=3, sticky="w", pady=(4, 0))
        judgment_threshold_spin.bind("<Return>", lambda e: self._on_judgment_threshold_changed())

        ttk.Label(analysis_box, text="Arrow confirm frames:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.arrow_confirm_var = tk.IntVar(value=config.DEFAULT_ARROW_MIN_CONFIRM_FRAMES)
        arrow_confirm_spin = ttk.Spinbox(
            analysis_box, from_=1, to=10, width=4, textvariable=self.arrow_confirm_var,
            command=self._on_arrow_confirm_changed,
        )
        arrow_confirm_spin.grid(row=2, column=1, sticky="w", pady=(4, 0))
        arrow_confirm_spin.bind("<Return>", lambda e: self._on_arrow_confirm_changed())
        ttk.Label(analysis_box, text="Judgment confirm frames:").grid(row=2, column=2, sticky="w", pady=(4, 0))
        self.judgment_confirm_var = tk.IntVar(value=config.DEFAULT_JUDGMENT_MIN_CONFIRM_FRAMES)
        judgment_confirm_spin = ttk.Spinbox(
            analysis_box, from_=1, to=10, width=4, textvariable=self.judgment_confirm_var,
            command=self._on_judgment_confirm_changed,
        )
        judgment_confirm_spin.grid(row=2, column=3, sticky="w", pady=(4, 0))
        judgment_confirm_spin.bind("<Return>", lambda e: self._on_judgment_confirm_changed())

        self.start_stop_button = ttk.Button(
            analysis_box, text="Start Analysis", style="Accent.TButton", command=self.controller.toggle_analysis
        )
        self.start_stop_button.grid(row=0, column=2, padx=10)
        ttk.Button(analysis_box, text="New Session", command=self.controller.new_session).grid(
            row=0, column=3, padx=4
        )

        self.analysis_status_var = tk.StringVar(value="Analysis: stopped")
        ttk.Label(analysis_box, textvariable=self.analysis_status_var).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(4, 0)
        )
        ttk.Label(analysis_box, text="Game-end similarity threshold:").grid(
            row=4, column=0, sticky="w", pady=(8, 0),
        )
        self.game_end_threshold_var = tk.DoubleVar(value=config.DEFAULT_GAME_END_SIMILARITY_THRESHOLD)
        self.game_end_confirm_var = tk.IntVar(value=config.DEFAULT_GAME_END_CONFIRM_FRAMES)
        threshold_spin = ttk.Spinbox(
            analysis_box, from_=0.50, to=1.00, increment=0.01, width=5,
            textvariable=self.game_end_threshold_var, command=self._on_game_end_settings_changed,
        )
        threshold_spin.grid(row=4, column=1, sticky="w", pady=(8, 0))
        threshold_spin.bind("<Return>", lambda e: self._on_game_end_settings_changed())
        ttk.Label(analysis_box, text="Game-end confirm frames:").grid(
            row=4, column=2, sticky="w", pady=(8, 0),
        )
        confirm_spin = ttk.Spinbox(
            analysis_box, from_=1, to=30, width=4, textvariable=self.game_end_confirm_var,
            command=self._on_game_end_settings_changed,
        )
        confirm_spin.grid(row=4, column=3, sticky="w", pady=(8, 0))
        confirm_spin.bind("<Return>", lambda e: self._on_game_end_settings_changed())

        # --- status bar ----------------------------------------------------
        status_box = ttk.Frame(left)
        status_box.pack(fill="x", pady=(6, 0))
        self.ocr_status_var = tk.StringVar(value="OCR engine: not loaded yet")
        ttk.Label(status_box, textvariable=self.ocr_status_var).pack(anchor="w")
        self.calibration_status_var = tk.StringVar(value="Digit calibration: not started")
        ttk.Label(status_box, textvariable=self.calibration_status_var).pack(anchor="w")
        self.processing_status_var = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self.processing_status_var).pack(anchor="w")
        self.error_status_var = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self.error_status_var, style="Error.TLabel").pack(anchor="w")

        # --- right: recognition panel --------------------------------------
        ttk.Label(right, text="Live recognition", style="Section.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            right, text="Region crops, raw readings, and accepted values by player.",
            style="Muted.TLabel", wraplength=420,
        ).pack(anchor="w", pady=(4, 8))
        self.recognition_container = ttk.Frame(right)
        self.recognition_container.pack(fill="both", expand=True, pady=6)
        ttk.Label(
            self.recognition_container,
            text="No regions yet\n\nOpen a video, pause on a clear frame, then use Draw Region.\n"
                 "Your player recognition cards will appear here.",
            style="Muted.TLabel", wraplength=380, justify="left",
        ).pack(anchor="w", padx=12, pady=20)

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
        if self._frame_image_size != pil_image.size:
            self.canvas.delete("empty_state")
            self.canvas.config(width=w, height=h)
            self._photo_image = ImageTk.PhotoImage(pil_image)
            self._frame_image_size = pil_image.size
            if self._frame_image_id is None:
                self._frame_image_id = self.canvas.create_image(
                    0, 0, anchor="nw", image=self._photo_image, tags="frame_image",
                )
                self.canvas.tag_lower(self._frame_image_id)
            else:
                self.canvas.itemconfigure(self._frame_image_id, image=self._photo_image)
        else:
            self._photo_image.paste(pil_image)

    def set_video_info(self, text: str) -> None:
        self.video_info_var.set(text)

    def set_source_selection(self, selection: str) -> None:
        self.source_var.set(selection)
        self.file_controls.pack_forget()
        self.hdmi_controls.pack_forget()
        controls = self.hdmi_controls if selection == "HDMI input" else self.file_controls
        controls.pack(fill="x", pady=(8, 0), before=self.video_info_label)

    def set_live_mode(self, is_live: bool) -> None:
        self._is_live = is_live
        state = "disabled" if is_live else "normal"
        self.step_back_button.configure(state=state)
        self.step_forward_button.configure(state=state)
        self.seek_scale.configure(state=state)
        self.set_play_button_label(False)

    def set_capture_devices(self, labels: list[str]) -> None:
        previous = self.capture_device_var.get()
        self.capture_combo.configure(values=labels)
        self.capture_device_var.set(
            previous if previous in labels else labels[0] if len(labels) == 1 else ""
        )
        self._update_capture_controls()

    def set_capture_pending(self, pending: bool) -> None:
        self._capture_pending = pending
        self._update_capture_controls()

    def set_hdmi_connected(self, connected: bool) -> None:
        self._capture_connected = connected
        self._update_capture_controls()

    def set_recording_state(self, state: str, text: str = "") -> None:
        self._recording_state = state
        self.recording_status_var.set(text)
        self._update_capture_controls()

    def _update_capture_controls(self) -> None:
        busy = self._capture_pending or self._capture_connected
        self.source_combo.configure(state="disabled" if self._capture_pending else "readonly")
        self.capture_combo.configure(state="disabled" if busy else "readonly")
        self.refresh_devices_button.configure(state="disabled" if busy else "normal")
        text = (
            "Cancel HDMI" if self._capture_pending
            else "Disconnect HDMI" if self._capture_connected else "Connect HDMI"
        )
        self.connect_hdmi_button.configure(
            text=text, state="normal" if busy or self.capture_device_var.get() else "disabled",
        )
        recording_text = {
            "idle": "Record MP4", "recording": "Stop recording",
            "stopping": "Finishing MP4...", "unsaved": "Save recording...",
            "saving": "Saving MP4...",
        }[self._recording_state]
        enabled = self._recording_state in ("recording", "unsaved") or (
            self._recording_state == "idle" and self._capture_connected and not self._capture_pending
        )
        self.record_video_button.configure(text=recording_text, state="normal" if enabled else "disabled")

    def clear_source_frame(self) -> None:
        self.canvas.delete("all")
        self._photo_image = None
        self._frame_image_id = None
        self._frame_image_size = None
        self._overlay_ids = []
        self._overlay_signature = None
        self.canvas.create_text(
            24, 32, anchor="nw", text="Choose a video source above to begin.",
            fill=MUTED, tags="empty_state",
        )
        self.frame_pos_var.set("No source connected")

    def set_frame_position(self, frame_index: int, frame_count: int, video_time_ms: int) -> None:
        if self._is_live:
            self.frame_pos_var.set(f"LIVE | Frame {frame_index} | {video_time_ms} ms")
            return
        self.frame_pos_var.set(f"Frame {frame_index}/{max(frame_count - 1, 0)} | {video_time_ms} ms")
        self._suppress_slider_event = True
        try:
            self.seek_scale.configure(to=max(frame_count - 1, 0))
            self.seek_scale.set(frame_index)
        finally:
            self._suppress_slider_event = False

    def set_play_button_label(self, is_playing: bool) -> None:
        if self._is_live:
            text = "Freeze preview" if is_playing else "Resume live"
        else:
            text = "Pause" if is_playing else "Play"
        self.play_button.config(text=text)

    def set_analysis_status(self, is_running: bool) -> None:
        self.analysis_status_var.set(f"Analysis: {'running' if is_running else 'stopped'}")
        self.start_stop_button.config(text="Stop Analysis" if is_running else "Start Analysis")

    def set_ocr_status(self, text: str) -> None:
        self.ocr_status_var.set(text)

    def set_processing_status(self, text: str) -> None:
        self.processing_status_var.set(text)

    def set_calibration_status(self, text: str) -> None:
        self.calibration_status_var.set(text)

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
        signature = (
            self.display_scale,
            self._frame_image_size,
            tuple((r.x, r.y, r.w, r.h, r.player, r.kind, r.direction) for r in regions),
        )
        if signature == self._overlay_signature:
            return
        self._overlay_signature = signature
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
            if r.kind in ("arrow", "judgment") and self._frame_image_size is not None:
                width, height = (round(value / self.display_scale) for value in self._frame_image_size)
                bounds_for = arrow_search_bounds if r.kind == "arrow" else judgment_search_bounds
                sx0, sy0, sx1, sy1 = bounds_for(r, width, height)
                color = "#66ddff" if r.kind == "arrow" else "#ffcc00"
                search = self.canvas.create_rectangle(
                    sx0 * self.display_scale, sy0 * self.display_scale,
                    sx1 * self.display_scale, sy1 * self.display_scale,
                    outline=color, dash=(4, 3), width=1,
                )
                search_label = self.canvas.create_text(
                    sx0 * self.display_scale + 3, sy0 * self.display_scale - 3,
                    text=f"{r.player}:{'arrow' if r.kind == 'arrow' else 'letter'} search", anchor="sw", fill=color,
                )
                self._overlay_ids.extend([search, search_label])

    def _rebuild_recognition_rows(self, regions) -> None:
        for widget in self.recognition_container.winfo_children():
            widget.destroy()
        self._region_rows = {}
        self._region_thumb_refs = {}
        for index, player in enumerate(("P1", "P2")):
            self.recognition_container.columnconfigure(index, weight=1, uniform="players")
            ttk.Label(
                self.recognition_container, text=f"Player {index + 1}", style="Section.TLabel",
            ).grid(row=0, column=index, sticky="w", padx=4, pady=4)
        paired_rows = {("score", None): 1, ("combo", None): 2}
        paired_rows.update({
            ("arrow", direction): index + 3
            for index, direction in enumerate(config.ARROW_DIRECTIONS)
        })
        paired_rows[("judgment", None)] = len(paired_rows) + 1
        next_row = len(paired_rows) + 1
        unknown = None
        if any(r.player == "Unknown" and r.kind != "game_end" for r in regions):
            unknown = ttk.LabelFrame(
                self.recognition_container, text="Unknown player", padding=4,
            )
            unknown.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            next_row += 1
        for r in regions:
            if r.kind == "game_end":
                shared = ttk.LabelFrame(self.recognition_container, text="Game session", padding=8)
                shared.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=(12, 0))
                self._build_game_end_row(r, shared)
            else:
                grid_row = (
                    paired_rows[(r.kind, r.direction if r.kind == "arrow" else None)]
                    if r.player in ("P1", "P2") else None
                )
                parent = self.recognition_container if grid_row is not None else unknown
                if r.kind == "arrow":
                    self._build_arrow_row(r, parent, grid_row=grid_row)
                elif r.kind == "judgment":
                    self._build_judgment_row(r, parent, grid_row=grid_row)
                else:
                    self._build_numeric_row(r, parent, grid_row=grid_row)

    @staticmethod
    def _wrap_card_labels(row, extra_labels=()) -> None:
        labels = [widget for widget in row.winfo_children() if isinstance(widget, ttk.Label)]
        labels.extend(extra_labels)
        for label in labels:
            label.configure(wraplength=240)
        previous_width = 240

        def resize(event):
            nonlocal previous_width
            width = max(40, event.width - 12)
            if width == previous_width:
                return
            previous_width = width
            for label in labels:
                label.configure(wraplength=width)

        row.bind("<Configure>", resize)

    @staticmethod
    def _add_diagnostics(row, grid_row, kind):
        frame = ttk.Frame(row)
        frame.grid(row=grid_row, column=0, sticky="ew", pady=(4, 0))
        text = f"diagnostics: Advance/play to inspect {kind} matching."
        variable = tk.StringVar(value=text)
        expanded = tk.BooleanVar(value=False)
        label = ttk.Label(frame, textvariable=variable, justify="left")
        data = {
            "diagnostics_var": variable, "diagnostics_label": label,
            "diagnostics_text": text, "diagnostics_visible": False,
            "diagnostics_updated_at": 0.0,
        }

        def toggle():
            data["diagnostics_visible"] = expanded.get()
            if data["diagnostics_visible"]:
                variable.set(data["diagnostics_text"])
                data["diagnostics_updated_at"] = time.perf_counter()
                label.pack(anchor="w", fill="x")
            else:
                label.pack_forget()

        button = ttk.Checkbutton(frame, text="Show diagnostics", variable=expanded, command=toggle)
        button.pack(anchor="w")
        return {
            "diagnostics": data, "diagnostics_var": variable,
            "diagnostics_label": label, "diagnostics_toggle": button,
        }

    @staticmethod
    def _update_diagnostics(row, diagnostics):
        row = row["diagnostics"]
        text = f"diagnostics: {diagnostics}" if diagnostics else "diagnostics: No frame diagnostics available."
        row["diagnostics_text"] = text
        if not row["diagnostics_visible"]:
            return
        now = time.perf_counter()
        if now - row["diagnostics_updated_at"] < .25:
            return
        row["diagnostics_updated_at"] = now
        if row["diagnostics_var"].get() != text:
            row["diagnostics_var"].set(text)

    def _build_numeric_row(self, r, parent=None, *, grid_row=None) -> None:
        row = ttk.Frame(parent if parent is not None else self.recognition_container, relief="groove", padding=4)
        if grid_row is None:
            row.pack(fill="x", pady=3)
        else:
            column = 0 if r.player == "P1" else 1
            row.grid(row=grid_row, column=column, sticky="nsew", pady=3,
                     padx=(0, 4) if column == 0 else (4, 0))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text=f"{r.label} ({r.player}/{r.kind})", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        thumb_label = ttk.Label(row)
        thumb_label.grid(row=1, column=0, sticky="w", pady=4)
        raw_var = tk.StringVar(value="raw OCR: -")
        candidate_var = tk.StringVar(value="candidate: -")
        accepted_var = tk.StringVar(value="accepted: unknown")
        status_var = tk.StringVar(value="status: -")
        source_var = tk.StringVar(value="OCR frame: -")
        ttk.Label(row, textvariable=raw_var).grid(row=2, column=0, sticky="w")
        ttk.Label(row, textvariable=candidate_var).grid(row=3, column=0, sticky="w")
        ttk.Label(row, textvariable=accepted_var).grid(row=4, column=0, sticky="w")
        ttk.Label(row, textvariable=status_var).grid(row=5, column=0, sticky="w")
        if r.kind in ("score", "combo"):
            ttk.Button(
                row, text=f"Teach {r.kind}...", command=lambda reg=r: self.controller.teach_score(reg),
            ).grid(row=6, column=0, sticky="w", pady=(4, 0))
            if r.player in ("P1", "P2"):
                ttk.Label(row, text="Teach this player's digits separately; samples are not shared.").grid(
                    row=10, column=0, sticky="w",
                )
        yellow_filter_var = None
        fast_mode_var = None
        if r.kind == "combo":
            yellow_filter_var = tk.BooleanVar(value=r.combo_yellow_filter)
            ttk.Checkbutton(
                row, text="Yellow-digit filter", variable=yellow_filter_var,
                command=lambda reg=r, var=yellow_filter_var: self.controller.set_combo_yellow_filter(
                    reg, var.get()
                ),
            ).grid(row=7, column=0, sticky="w", pady=(4, 0))
            fast_mode_var = tk.BooleanVar(value=r.combo_fast_mode)
            ttk.Checkbutton(
                row, text="Fast live combo", variable=fast_mode_var,
                command=lambda reg=r, var=fast_mode_var: self.controller.set_combo_fast_mode(reg, var.get()),
            ).grid(row=8, column=0, sticky="w")
        ttk.Label(row, textvariable=source_var).grid(row=9, column=0, sticky="w", pady=(4, 0))
        VideoTab._wrap_card_labels(row)
        self._region_rows[r.key()] = {
            "kind": "numeric",
            "thumb_label": thumb_label,
            "raw_var": raw_var,
            "candidate_var": candidate_var,
            "accepted_var": accepted_var,
            "status_var": status_var,
            "yellow_filter_var": yellow_filter_var,
            "source_var": source_var,
            "fast_mode_var": fast_mode_var,
        }

    def _build_arrow_row(self, r, parent=None, *, grid_row=None) -> None:
        row = ttk.Frame(parent if parent is not None else self.recognition_container, relief="groove", padding=4)
        if grid_row is None:
            row.pack(fill="x", pady=3)
        else:
            column = 0 if r.player == "P1" else 1
            row.grid(row=grid_row, column=column, sticky="nsew", pady=3,
                     padx=(0, 4) if column == 0 else (4, 0))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text=f"{r.label} ({r.player}/arrow/{r.direction})", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        thumb_label = ttk.Label(row)
        thumb_label.grid(row=1, column=0, sticky="w", pady=4)

        state_var = tk.StringVar(value="state: -")
        confidence_var = tk.StringVar(value="active shape similarity: -")
        status_var = tk.StringVar(value=(
            "status: Activity only; outcomes require a matching judgment."
            if r.reference_images.get("active") else
            "status: Capture a fresh Active Arrow reference for this player. Old Hit/Miss refs are ignored."
        ))
        ttk.Label(row, textvariable=state_var).grid(row=2, column=0, sticky="w")
        ttk.Label(row, textvariable=confidence_var).grid(row=3, column=0, sticky="w")
        ttk.Label(row, textvariable=status_var).grid(row=4, column=0, sticky="w")

        refs = ttk.Frame(row)
        refs.grid(row=5, column=0, sticky="w", pady=4)
        ttk.Label(refs, text="Active Arrow ref:").grid(row=0, column=0, sticky="w")
        active_ref_label = ttk.Label(refs, text="(none)")
        active_ref_label.grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Label(refs, text="Empty ref:").grid(row=0, column=1, sticky="w")
        empty_ref_label = ttk.Label(refs, text="(none)")
        empty_ref_label.grid(row=1, column=1, sticky="w")

        btns = ttk.Frame(row)
        btns.grid(row=6, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            btns, text="Add Active Arrow ref", command=lambda reg=r: self.controller.capture_arrow_reference(reg, "active"),
        ).grid(row=0, column=0, padx=2)
        ttk.Button(
            btns, text="Clear Active Arrow refs", command=lambda reg=r: self.controller.clear_arrow_reference(reg, "active"),
        ).grid(row=0, column=1, padx=2)
        ttk.Button(
            btns, text="View / delete refs...",
            command=lambda reg=r: self.controller.show_arrow_reference_gallery(reg, "active"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=2, pady=(4, 0))
        ttk.Button(
            btns, text="Add Empty ref",
            command=lambda reg=r: self.controller.capture_arrow_reference(reg, "empty"),
        ).grid(row=2, column=0, padx=2, pady=(4, 0))
        ttk.Button(
            btns, text="Clear Empty refs",
            command=lambda reg=r: self.controller.clear_arrow_reference(reg, "empty"),
        ).grid(row=2, column=1, padx=2, pady=(4, 0))
        ttk.Button(
            btns, text="View / delete Empty refs...",
            command=lambda reg=r: self.controller.show_arrow_reference_gallery(reg, "empty"),
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=2, pady=(4, 0))
        ttk.Label(
            row, text="Capture this arrow while active, not its always-visible idle receptor. "
            "Capture Empty when no note overlaps the receptor. Empty examples suppress "
            "look-alike idle matches; Active must clearly match better. "
            "The judgment box alone decides Perfect/Great/Good/Bad/Miss. "
            "Old Hit/Miss/Appearance refs are ignored.",
        ).grid(row=7, column=0, sticky="w", pady=(4, 0))
        if r.player in ("P1", "P2"):
            ttk.Label(row, text="Capture this player's arrow separately. "
                      "Adding or clearing references does not change the other player.").grid(row=8, column=0, sticky="w")
        diagnostics = VideoTab._add_diagnostics(row, 9, "arrow")
        ttk.Label(
            row, text="Colored-shape matching: capture the colored note, not the white flash. "
            "Cyan dashed bounds search half a box-height below the receptor. "
            "Empty examples are compared in the same learned-color space.",
        ).grid(row=10, column=0, sticky="w", pady=(4, 0))

        VideoTab._wrap_card_labels(row, (diagnostics["diagnostics_label"],))
        self._region_rows[r.key()] = {
            "kind": "arrow",
            "thumb_label": thumb_label,
            "state_var": state_var,
            "confidence_var": confidence_var,
            "status_var": status_var,
            "active_ref_label": active_ref_label,
            "empty_ref_label": empty_ref_label,
            **diagnostics,
        }

    def _build_judgment_row(self, region, parent, *, grid_row=None) -> None:
        row = ttk.Frame(parent, relief="groove", padding=4)
        if grid_row is None:
            row.pack(fill="x", pady=3)
        else:
            column = 0 if region.player == "P1" else 1
            row.grid(row=grid_row, column=column, sticky="nsew", pady=3,
                     padx=(0, 4) if column == 0 else (4, 0))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text=f"{region.label} ({region.player}/judgment)", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w",
        )
        thumb = ttk.Label(row)
        thumb.grid(row=1, column=0, sticky="w", pady=4)
        state = tk.StringVar(value="state: -")
        confidence = tk.StringVar(value="similarity: -")
        status = tk.StringVar(value="status: Capture Perfect, Great, Good, Bad, and Miss examples.")
        for index, variable in enumerate((state, confidence, status), 2):
            ttk.Label(row, textvariable=variable).grid(row=index, column=0, sticky="w")
        diagnostics = VideoTab._add_diagnostics(row, 5, "judgment")
        data = {
            "kind": "judgment", "thumb_label": thumb, "state_var": state,
            "confidence_var": confidence, "status_var": status,
            **diagnostics,
        }
        categories = tuple((name, name.title()) for name in config.JUDGMENT_POINTS) + (
            ("empty", "No judgment"),
        )
        for index, (name, title) in enumerate(categories):
            controls = ttk.Frame(row)
            controls.grid(row=6 + index, column=0, sticky="w", pady=4)
            reference = ttk.Label(controls, text="(none)")
            reference.grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Button(
                controls, text=f"Add {title} ref",
                command=lambda reg=region, value=name: self.controller.capture_arrow_reference(reg, value),
            ).grid(row=1, column=0, padx=2)
            ttk.Button(
                controls, text=f"Clear {title} refs",
                command=lambda reg=region, value=name: self.controller.clear_arrow_reference(reg, value),
            ).grid(row=1, column=1, padx=2)
            ttk.Button(
                controls, text=f"View / delete {title} refs...",
                command=lambda reg=region, value=name: self.controller.show_arrow_reference_gallery(reg, value),
            ).grid(row=2, column=0, columnspan=2, sticky="w", padx=2, pady=(4, 0))
            data[f"{name}_ref_label"] = reference
        ttk.Label(
            row, text="Capture PERFECT, GREAT, GOOD, and BAD separately; all count as hits. "
            "BAD awards 5 points per arrow and leaves the streak unchanged. "
            "Letter-shape matching is the default. Capture clear colored words; "
            "yellow dashed bounds show the larger search area, green bounds the capture box. "
            "MISS counts as a miss. Add optional No judgment samples when no word is visible. "
            "Capture several background appearances; outcome matches must beat these samples. "
            "Old Success references remain ungraded until any success tier is taught. "
            "Taught outcomes require consecutive matching frames (default 2); untaught categories are not recognized. "
            f"Judgments count once, then rearm after {config.JUDGMENT_REARM_MS} ms of video time without any "
            "taught outcome matching. Changing words alone does not rearm. "
            "Ambiguous strong matches hold the current state. Prolonged matching failures "
            "can look like disappearance. Unclear directions count for the player only.",
        ).grid(row=6 + len(categories), column=0, sticky="w")
        if region.player in ("P1", "P2"):
            ttk.Label(row, text="Capture this player's judgments separately. "
                      "Adding or clearing references does not change the other player.").grid(row=7 + len(categories), column=0, sticky="w")
        VideoTab._wrap_card_labels(row, (diagnostics["diagnostics_label"],))
        self._region_rows[region.key()] = data

    def _build_game_end_row(self, region, parent) -> None:
        row = ttk.Frame(parent, padding=4)
        row.pack(fill="x")
        ttk.Label(row, text=region.label, style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            row, text="Capture a distinctive end-of-game indicator. A confirmed match starts the next GUID.",
            wraplength=420, style="Muted.TLabel",
        ).pack(anchor="w", pady=4)
        thumb = ttk.Label(row)
        thumb.pack(anchor="w")
        state = tk.StringVar(value="state: -")
        confidence = tk.StringVar(value="similarity: -")
        status = tk.StringVar(value="status: Capture a game-end reference.")
        for variable in (state, confidence, status):
            ttk.Label(row, textvariable=variable, wraplength=420).pack(anchor="w")
        reference = ttk.Label(row, text="(none)")
        reference.pack(anchor="w", pady=4)
        ttk.Button(
            row, text="Add game-end ref",
            command=lambda: self.controller.capture_arrow_reference(region, "ended"),
        ).pack(anchor="w", pady=4)
        ttk.Button(
            row, text="Clear game-end refs",
            command=lambda: self.controller.clear_arrow_reference(region, "ended"),
        ).pack(anchor="w")
        self._region_rows[region.key()] = {
            "kind": "game_end", "thumb_label": thumb, "state_var": state,
            "confidence_var": confidence, "status_var": status, "ended_ref_label": reference,
        }

    def update_recognition_row(
        self, region_key: str, crop_photo, raw_text, candidate, accepted, status_text,
        *, frame_index=None, video_time_ms=None,
    ) -> None:
        row = self._region_rows.get(region_key)
        if row is None or row.get("kind") != "numeric":
            return
        if crop_photo is not None:
            self._region_thumb_refs[region_key] = crop_photo
            row["thumb_label"].configure(image=crop_photo)
        row["raw_var"].set(f"raw OCR: {raw_text!r}" if raw_text else "raw OCR: (empty)")
        unconfirmed = " (unconfirmed)" if candidate is not None and candidate != accepted else ""
        row["candidate_var"].set(
            f"candidate: {candidate if candidate is not None else 'unreadable'}{unconfirmed}"
        )
        row["accepted_var"].set(f"accepted: {accepted if accepted is not None else 'unknown'}")
        row["status_var"].set(f"status: {status_text}")
        row["source_var"].set(
            f"OCR frame: {frame_index} | {video_time_ms} ms"
            if frame_index is not None else "OCR frame: -"
        )

    def update_arrow_recognition_row(
        self, region_key: str, crop_photo, observed_state, confidence, status_text
    ) -> None:
        row = self._region_rows.get(region_key)
        if row is None or row.get("kind") not in ("arrow", "judgment", "game_end"):
            return
        if crop_photo is not None and self._region_thumb_refs.get(region_key) is not crop_photo:
            self._region_thumb_refs[region_key] = crop_photo
            row["thumb_label"].configure(image=crop_photo)
        similarity_label = "active shape similarity" if row["kind"] == "arrow" else "similarity"
        values = {
            "state_var": f"state: {observed_state if observed_state is not None else 'unreadable'}",
            "confidence_var": f"{similarity_label}: {confidence:.2f}" if confidence is not None else f"{similarity_label}: -",
            "status_var": f"status: {status_text}",
        }
        for key, text in values.items():
            if row[key].get() != text:
                row[key].set(text)

    def set_arrow_diagnostics(self, region_key: str, diagnostics: Optional[str]) -> None:
        row = self._region_rows.get(region_key)
        if row is not None and row.get("kind") == "arrow":
            VideoTab._update_diagnostics(row, diagnostics)

    def set_judgment_diagnostics(self, region_key: str, diagnostics: Optional[str]) -> None:
        row = self._region_rows.get(region_key)
        if row is not None and row.get("kind") == "judgment":
            VideoTab._update_diagnostics(row, diagnostics)

    def set_arrow_reference_thumbnail(self, region_key: str, state: str, photo, count: int) -> None:
        row = self._region_rows.get(region_key)
        if row is None or row.get("kind") not in ("arrow", "judgment", "game_end"):
            return
        label_key = f"{state}_ref_label"
        label = row[label_key]
        if photo is None:
            label.configure(image="", text="(none)")
        else:
            self._region_thumb_refs[f"{region_key}:{state}_ref"] = photo
            label.configure(image=photo, text=f"{count} ref{'s' if count != 1 else ''}", compound="top")
            row["status_var"].set("status: Reference ready. Advance/play to refresh recognition.")


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
        if self._rect_id is not None:
            self.canvas.delete(self._rect_id)
        self._rect_id = None
        self._rect_start = None
        self._move_region = None
        self._move_start = None
        self.canvas.configure(cursor="")
        self._draw_mode = not self._draw_mode
        self.draw_mode_button.config(text=f"Draw Region: {'ON' if self._draw_mode else 'OFF'}")

    def _on_canvas_press(self, event) -> None:
        if not self._draw_mode:
            scale = self.display_scale
            x, y = event.x / scale, event.y / scale
            self._move_region = next((
                region for region in reversed(self.controller.regions)
                if region.x <= x < region.x + region.w and region.y <= y < region.y + region.h
            ), None)
            if self._move_region is not None:
                region = self._move_region
                self._move_start = (event.x, event.y)
                self._rect_id = self.canvas.create_rectangle(
                    region.x * scale, region.y * scale,
                    (region.x + region.w) * scale, (region.y + region.h) * scale,
                    outline="#ffcc00", width=2,
                )
                self.canvas.configure(cursor="fleur")
            return
        self._rect_start = (event.x, event.y)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ffcc00", width=2
        )

    def _on_canvas_drag(self, event) -> None:
        if self._move_region is not None:
            region = self._move_region
            start_x, start_y = self._move_start
            scale = self.display_scale
            x = region.x * scale + event.x - start_x
            y = region.y * scale + event.y - start_y
            self.canvas.coords(self._rect_id, x, y, x + region.w * scale, y + region.h * scale)
            return
        if not self._draw_mode or self._rect_id is None:
            return
        x0, y0 = self._rect_start
        self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _on_canvas_release(self, event) -> None:
        if self._move_region is not None:
            region = self._move_region
            start_x, start_y = self._move_start
            x = region.x + round((event.x - start_x) / self.display_scale)
            y = region.y + round((event.y - start_y) / self.display_scale)
            self.canvas.delete(self._rect_id)
            self._rect_id = None
            self._move_region = None
            self._move_start = None
            self.canvas.configure(cursor="")
            if (x, y) != (region.x, region.y):
                self.controller.move_region(region, x, y)
            return
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
            if not 0.5 <= value <= 1:
                raise ValueError("use a value between 0.50 and 1.00")
            self.controller.set_arrow_similarity_threshold(value)
        except (tk.TclError, ValueError) as exc:
            self.set_error_status(f"Invalid arrow similarity: {exc}")
            return
        self.set_error_status("")

    def _on_judgment_threshold_changed(self) -> None:
        try:
            value = float(self.judgment_threshold_var.get())
            if not 0.5 <= value <= 1:
                raise ValueError("use a value between 0.50 and 1.00")
            self.controller.set_judgment_similarity_threshold(value)
        except (tk.TclError, ValueError) as exc:
            self.set_error_status(f"Invalid judgment similarity: {exc}")
            return
        self.set_error_status("")

    def _on_arrow_confirm_changed(self) -> None:
        try:
            n = int(self.arrow_confirm_var.get())
            if not 1 <= n <= 10:
                raise ValueError("use an integer from 1 to 10")
            self.controller.set_arrow_min_confirm_frames(n)
        except (tk.TclError, ValueError) as exc:
            self.set_error_status(f"Invalid arrow confirmation: {exc}")
            return
        self.set_error_status("")

    def _on_judgment_confirm_changed(self) -> None:
        try:
            n = int(self.judgment_confirm_var.get())
            if not 1 <= n <= 10:
                raise ValueError("use an integer from 1 to 10")
            self.controller.set_judgment_min_confirm_frames(n)
        except (tk.TclError, ValueError) as exc:
            self.set_error_status(f"Invalid judgment confirmation: {exc}")
            return
        self.set_error_status("")

    def _on_game_end_settings_changed(self) -> None:
        try:
            threshold = self.game_end_threshold_var.get()
            confirm_frames = self.game_end_confirm_var.get()
            self.controller.set_game_end_settings(threshold, confirm_frames)
        except (tk.TclError, ValueError) as exc:
            self.set_error_status(f"Invalid game-end settings: {exc}")
