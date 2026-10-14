"""Teach score digits by entering the number shown in one video crop."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

from app import config, image_utils
from app.models import Region
from app.ocr_engine import _extract_score_glyphs, score_templates_from_label


class ScoreCalibrationDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, region: Region, crop: np.ndarray):
        super().__init__(parent)
        self.title(f"Teach score - {region.label}")
        self.resizable(False, False)
        self.result: dict[str, list[str]] | None = None
        self._region = region
        self._crop = crop.copy()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        self._image_label = ttk.Label(body)
        self._image_label.pack(pady=6)
        ttk.Label(
            body, text="Type all visible score digits, including leading zeros:",
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
            body, textvariable=self.error, foreground="red", wraplength=560,
        ).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(anchor="e", pady=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Learn", command=self._confirm).pack(side="left")
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
        _, boxes, _ = _extract_score_glyphs(self._crop)
        try:
            learned = score_templates_from_label(self._crop, self.score.get())
            labels = [digit for digit, _ in learned]
        except ValueError:
            labels = ["?"] * len(boxes)
        draw = ImageDraw.Draw(preview)
        for (left, right, top, bottom), label in zip(boxes, labels):
            draw.rectangle((left, top + 20, right, bottom + 20), outline="lime")
            draw.text((left, 3), label, fill="white")
        preview.thumbnail((600, 200))
        self._photo = ImageTk.PhotoImage(preview, master=self)
        self._image_label.configure(image=self._photo)

    def _confirm(self) -> None:
        try:
            learned = score_templates_from_label(self._crop, self.score.get())
        except ValueError as exc:
            self.error.set(str(exc))
            return
        templates = (
            {digit: list(images) for digit, images in self._region.digit_templates.items()}
            if (
                self._region.digit_templates_reviewed
                and self._region.score_template_version == config.SCORE_TEMPLATE_VERSION
            ) else {}
        )
        for digit, glyph in learned:
            encoded = image_utils.encode_crop_to_base64_png(glyph)
            examples = templates.setdefault(digit, [])
            if encoded not in examples:
                examples.append(encoded)
        self.result = templates
        self.destroy()
