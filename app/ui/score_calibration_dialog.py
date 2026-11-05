"""Teach score or combo digits by entering the number shown in one video crop."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

from app import config, image_utils
from app.models import Region
from app.ocr_engine import _extract_combo_glyphs, _extract_score_glyphs, score_templates_from_label


class ScoreCalibrationDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, region: Region, crop: np.ndarray):
        super().__init__(parent)
        self.title(f"Teach {region.kind} - {region.label}")
        self.resizable(False, False)
        self.result: dict[str, list[str]] | None = None
        self._region = region
        self._crop = crop.copy()
        self._manual_boxes: list[list[int]] | None = None
        self._drag_start: tuple[int, int] | None = None
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        self._image_label = tk.Canvas(body, highlightthickness=0)
        self._image_label.pack(pady=6)
        self._image_label.bind("<ButtonPress-1>", self._start_box)
        self._image_label.bind("<B1-Motion>", self._drag_box)
        self._image_label.bind("<ButtonRelease-1>", self._finish_box)
        ttk.Label(
            body, text=f"Type all visible {region.kind} digits, including leading zeros:",
        ).pack(anchor="w")
        if region.kind == "combo":
            ttk.Button(body, text="Draw digit boxes manually", command=self._reset_boxes).pack(anchor="w")
            ttk.Button(body, text="Use automatic boxes", command=self._automatic_boxes).pack(anchor="w")
            ttk.Label(
                body, text="Manual: drag a tight box around each digit, left to right. Reset to redraw.",
                wraplength=560,
            ).pack(anchor="w")
        self.score = tk.StringVar(self)
        entry = ttk.Entry(body, textvariable=self.score, width=24)
        entry.pack(fill="x", pady=6)
        self.error = tk.StringVar(self)
        ttk.Label(
            body, text="Check that each label above the image matches its digit before Learn.",
            wraplength=560,
        ).pack(anchor="w")
        ttk.Label(
            body, textvariable=self.error, style="Error.TLabel", wraplength=560,
        ).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(anchor="e", pady=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Learn", style="Accent.TButton", command=self._confirm).pack(side="left")
        self.bind("<Return>", lambda _: self._confirm())
        self.bind("<Escape>", lambda _: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.score.trace_add("write", self._update_preview)
        self._update_preview()
        entry.focus_set()

    def _update_preview(self, *_args) -> None:
        image = Image.fromarray(cv2.cvtColor(self._crop, cv2.COLOR_BGR2RGB))
        preview = Image.new("RGB", (image.width, image.height + 20), "black")
        preview.paste(image, (0, 20))
        _, boxes, _ = (
            _extract_combo_glyphs(self._crop) if self._region.kind == "combo"
            else _extract_score_glyphs(self._crop)
        )
        if self._manual_boxes is not None:
            boxes = self._manual_boxes
        try:
            learned = self._learned()
            labels = [digit for digit, _ in learned]
        except ValueError:
            labels = ["?"] * len(boxes)
        draw = ImageDraw.Draw(preview)
        for (left, right, top, bottom), label in zip(boxes, labels):
            draw.rectangle((left, top + 20, right, bottom + 20), outline="lime")
            draw.text((left, 3), label, fill="white")
        preview.thumbnail((600, 200))
        self._preview_size = preview.size
        self._photo = ImageTk.PhotoImage(preview, master=self)
        self._image_label.configure(width=preview.width, height=preview.height)
        self._drag_start = None
        self._image_label.delete("all")
        self._image_label.create_image(0, 0, anchor="nw", image=self._photo)

    def _learned(self):
        if self._region.kind == "combo":
            return score_templates_from_label(
                self._crop, self.score.get(), kind="combo", boxes=self._manual_boxes,
            )
        return score_templates_from_label(self._crop, self.score.get())

    def _reset_boxes(self) -> None:
        self._manual_boxes = []
        self._drag_start = None
        self._image_label.configure(cursor="crosshair")
        self.error.set("")
        self._update_preview()

    def _automatic_boxes(self) -> None:
        self._manual_boxes = None
        self._drag_start = None
        self._image_label.configure(cursor="")
        self.error.set("")
        self._update_preview()

    def _image_position(self, event) -> tuple[int, int]:
        h, w = self._crop.shape[:2]
        return (
            round(event.x * w / self._preview_size[0]),
            round(event.y * (h + 20) / self._preview_size[1]) - 20,
        )

    def _start_box(self, event) -> None:
        if self._manual_boxes is not None:
            self._drag_start = self._image_position(event)
            self._image_label.delete("digit-drag")
            self._image_label.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="lime", width=2, dash=(4, 2), tags="digit-drag",
            )

    def _drag_box(self, event) -> None:
        if self._manual_boxes is None or self._drag_start is None:
            return
        x, y = self._drag_start
        h, w = self._crop.shape[:2]
        self._image_label.coords(
            "digit-drag", x * self._preview_size[0] / w,
            (y + 20) * self._preview_size[1] / (h + 20), event.x, event.y,
        )

    def _finish_box(self, event) -> None:
        if self._manual_boxes is None or self._drag_start is None:
            return
        x, y = self._drag_start
        end_x, end_y = self._image_position(event)
        self._drag_start = None
        self._image_label.delete("digit-drag")
        box = [min(x, end_x), max(x, end_x), min(y, end_y), max(y, end_y)]
        h, w = self._crop.shape[:2]
        if not (0 <= box[0] < box[1] <= w and 0 <= box[2] < box[3] <= h):
            self.error.set("Draw each box inside the digit image, below the labels.")
            return
        if self._manual_boxes and box[0] < self._manual_boxes[-1][1]:
            self.error.set("Draw non-overlapping digit boxes from left to right. Reset to redraw.")
            return
        self._manual_boxes.append(box)
        self.error.set("")
        self._update_preview()

    def _confirm(self) -> None:
        try:
            learned = self._learned()
        except ValueError as exc:
            self.error.set(str(exc))
            return
        templates = (
            {digit: list(images) for digit, images in self._region.digit_templates.items()}
            if (
                self._region.digit_templates_reviewed
                and self._region.score_template_version == config.DIGIT_TEMPLATE_VERSIONS[self._region.kind]
            ) else {}
        )
        for digit, glyph in learned:
            encoded = image_utils.encode_crop_to_base64_png(glyph)
            examples = templates.setdefault(digit, [])
            if encoded not in examples:
                examples.append(encoded)
        self.result = templates
        self.destroy()
