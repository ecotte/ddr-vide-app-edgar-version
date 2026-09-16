"""A small modal dialog for labeling a newly-drawn region."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple

from app import config


class RegionDialog(tk.Toplevel):
    """Blocks until the user confirms or cancels; result is in ``self.result``
    as (label, player, kind, direction). ``direction`` is None unless
    kind == "arrow"."""

    def __init__(self, parent: tk.Widget, existing_keys: Optional[set] = None):
        super().__init__(parent)
        self.title("Label region")
        self.resizable(False, False)
        self.result: Optional[Tuple[str, str, str, Optional[str]]] = None
        self._existing_keys = existing_keys or set()

        frm = ttk.Frame(self, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm, text="Label:").grid(row=0, column=0, sticky="w", pady=4)
        self.label_var = tk.StringVar(value="Score")
        ttk.Entry(frm, textvariable=self.label_var, width=24).grid(row=0, column=1, pady=4)

        ttk.Label(frm, text="Player:").grid(row=1, column=0, sticky="w", pady=4)
        self.player_var = tk.StringVar(value=config.PLAYER_CHOICES[0])
        ttk.Combobox(
            frm, textvariable=self.player_var, values=config.PLAYER_CHOICES,
            state="readonly", width=21,
        ).grid(row=1, column=1, pady=4)

        ttk.Label(frm, text="Field kind:").grid(row=2, column=0, sticky="w", pady=4)
        self.kind_var = tk.StringVar(value=config.REGION_KINDS[0])
        kind_combo = ttk.Combobox(
            frm, textvariable=self.kind_var, values=config.REGION_KINDS, state="readonly", width=21,
        )
        kind_combo.grid(row=2, column=1, pady=4)
        kind_combo.bind("<<ComboboxSelected>>", lambda e: self._on_kind_changed())

        self.direction_label = ttk.Label(frm, text="Arrow direction:")
        self.direction_label.grid(row=3, column=0, sticky="w", pady=4)
        self.direction_var = tk.StringVar(value=config.ARROW_DIRECTIONS[0])
        self.direction_combo = ttk.Combobox(
            frm, textvariable=self.direction_var, values=config.ARROW_DIRECTIONS,
            state="readonly", width=21,
        )
        self.direction_combo.grid(row=3, column=1, pady=4)

        self.error_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.error_var, foreground="red", wraplength=320).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, pady=(8, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self._on_cancel).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Add region", command=self._on_confirm).grid(row=0, column=1, padx=4)

        self._on_kind_changed()

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_visibility()
        self.focus()

    def _on_kind_changed(self) -> None:
        is_arrow = self.kind_var.get() == "arrow"
        state = "readonly" if is_arrow else "disabled"
        self.direction_combo.configure(state=state)

    def _on_confirm(self) -> None:
        label = self.label_var.get().strip()
        player = self.player_var.get()
        kind = self.kind_var.get()
        direction = self.direction_var.get() if kind == "arrow" else None
        if not label:
            self.error_var.set("Label cannot be empty.")
            return
        key = f"{player}:{kind}:{direction}" if kind == "arrow" else f"{player}:{kind}"
        if key in self._existing_keys:
            detail = f"{kind} ({direction})" if kind == "arrow" else kind
            self.error_var.set(
                f"A '{detail}' region for {player} already exists. Delete it first or pick a different player/kind/direction."
            )
            return
        self.result = (label, player, kind, direction)
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()
