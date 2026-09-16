"""Events table: recorded score_changed / combo_changed events."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

COLUMNS = (
    "sequence",
    "event_type",
    "player",
    "field",
    "detail",
    "video_time_ms",
    "observed_at_utc",
)


class EventsTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, controller):
        super().__init__(parent, padding=6)
        self.controller = controller
        self._build_layout()

    def _build_layout(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))

        self.recording_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top, text="Record events", variable=self.recording_var, command=self._on_recording_toggle
        ).pack(side="left")

        ttk.Button(top, text="Clear Table", command=self._on_clear).pack(side="left", padx=8)
        ttk.Button(top, text="Export to JSONL...", command=self._on_export).pack(side="left", padx=8)

        self.count_var = tk.StringVar(value="0 events recorded")
        ttk.Label(top, textvariable=self.count_var).pack(side="right")

        self.tree = ttk.Treeview(self, columns=COLUMNS, show="headings", height=20)
        headings = {
            "sequence": "#",
            "event_type": "Event type",
            "player": "Player",
            "field": "Field",
            "detail": "Detail",
            "video_time_ms": "Video time (ms)",
            "observed_at_utc": "Observed at (UTC)",
        }
        widths = {
            "sequence": 50,
            "event_type": 120,
            "player": 60,
            "field": 70,
            "detail": 160,
            "video_time_ms": 120,
            "observed_at_utc": 190,
        }
        for col in COLUMNS:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")
        self.tree.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)

    def append_event(self, event) -> None:
        self.tree.insert(
            "",
            tk.END,
            values=(
                event.sequence,
                event.event_type,
                event.player or "-",
                event.payload.get("field", "-"),
                self._format_detail(event),
                event.video_time_ms,
                event.observed_at_utc,
            ),
        )
        self.tree.yview_moveto(1.0)
        self._refresh_count()

    @staticmethod
    def _format_detail(event) -> str:
        payload = event.payload
        if "previous_value" in payload or "new_value" in payload:
            prev = payload.get("previous_value")
            new = payload.get("new_value")
            return f"{prev if prev is not None else 'unknown'} -> {new}"
        if payload.get("field") == "arrow":
            return f"{payload.get('direction', '?')} - {payload.get('state', '?').upper()}"
        return "-"

    def clear(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._refresh_count()

    def _refresh_count(self) -> None:
        self.count_var.set(f"{len(self.tree.get_children())} events recorded")

    def _on_recording_toggle(self) -> None:
        self.controller.set_recording_enabled(self.recording_var.get())

    def _on_clear(self) -> None:
        if messagebox.askyesno(
            "Clear events", "Clear the events table? This also clears events available for export."
        ):
            self.controller.clear_events()

    def _on_export(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".jsonl",
            filetypes=[("JSON Lines", "*.jsonl"), ("All files", "*.*")],
            initialdir=self.controller.data_dir(),
            title="Export events to JSONL",
        )
        if not path:
            return
        count = self.controller.export_events(path)
        messagebox.showinfo("Export complete", f"Wrote {count} event(s) to:\n{path}")
