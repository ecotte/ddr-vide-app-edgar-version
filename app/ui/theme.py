"""Shared dark styling for the desktop app and its dialogs."""
from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

BACKGROUND = "#10151f"
SURFACE = "#18212f"
FIELD = "#0d131d"
BORDER = "#334155"
TEXT = "#e6edf7"
MUTED = "#a4b3c8"
ACCENT = "#79aaff"
ERROR = "#ff9b9b"


def apply_dark_theme(root: tk.Tk) -> None:
    font.nametofont("TkDefaultFont").configure(family="Segoe UI", size=10)
    font.nametofont("TkTextFont").configure(family="Segoe UI", size=10)
    font.nametofont("TkFixedFont").configure(family="Consolas", size=10)
    root.configure(background=BACKGROUND)

    # Classic Tk widgets and combobox popups do not inherit ttk styles.
    for option, value in {
        "*background": SURFACE,
        "*foreground": TEXT,
        "*font": "TkDefaultFont",
        "*activeBackground": BORDER,
        "*activeForeground": TEXT,
        "*selectBackground": "#284c7d",
        "*selectForeground": TEXT,
        "*insertBackground": TEXT,
        "*highlightBackground": BORDER,
        "*highlightColor": ACCENT,
        "*Listbox.background": FIELD,
        "*Listbox.selectBorderWidth": 0,
        "*Listbox.relief": "flat",
        "*Listbox.highlightThickness": 1,
        "*Entry.background": FIELD,
        "*Canvas.background": SURFACE,
        "*Toplevel.background": BACKGROUND,
    }.items():
        root.option_add(option, value)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", background=SURFACE, foreground=TEXT, bordercolor=BORDER,
        lightcolor=SURFACE, darkcolor=SURFACE, troughcolor=FIELD,
        selectbackground="#284c7d", selectforeground=TEXT,
        font="TkDefaultFont",
    )
    style.map(".", foreground=[("disabled", "#8190a5")])
    style.configure("TFrame", background=SURFACE)
    style.configure("Header.TFrame", background=BACKGROUND)
    style.configure("Title.TLabel", background=BACKGROUND, font=("Segoe UI", 20, "bold"))
    style.configure("Subtitle.TLabel", background=BACKGROUND, foreground=MUTED)
    style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Error.TLabel", foreground=ERROR)
    style.configure("TLabelframe", borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", foreground=ACCENT, font=("Segoe UI", 10, "bold"))
    style.configure("TButton", padding=(10, 6), borderwidth=1, relief="flat", background="#253349")
    style.map(
        "TButton",
        background=[("disabled", SURFACE), ("pressed", "#314b6e"), ("active", "#344760")],
        bordercolor=[("focus", ACCENT)],
    )
    style.configure("Accent.TButton", background=ACCENT, foreground=FIELD)
    style.map(
        "Accent.TButton",
        background=[("disabled", BORDER), ("pressed", "#6398f0"), ("active", "#9bc0ff")],
        foreground=[("disabled", MUTED), ("!disabled", FIELD)],
    )
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, fieldbackground=FIELD, foreground=TEXT, padding=5, arrowsize=14)
        style.map(
            widget,
            fieldbackground=[("disabled", SURFACE), ("readonly", FIELD)],
            foreground=[("disabled", "#8190a5"), ("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
            selectbackground=[("!disabled", "#284c7d")],
            selectforeground=[("!disabled", TEXT)],
        )
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, padding=(0, 4), indicatorbackground=FIELD)
        style.map(
            widget, background=[("active", SURFACE)],
            indicatorbackground=[("disabled", BORDER), ("selected", ACCENT), ("active", BORDER)],
        )
    tab_images = []
    for background, underline in (
        (BACKGROUND, BORDER), (SURFACE, BORDER), ("#253349", ACCENT),
    ):
        image = tk.PhotoImage(master=root, width=8, height=8)
        image.put(background, to=(0, 0, 8, 8))
        image.put(underline, to=(0, 5, 8, 8))
        tab_images.append(image)
    # Tk stores image names, not Python references.
    root._notebook_tab_images = tab_images
    style.element_create(
        "FlatNotebook.tab", "image", tab_images[0],
        ("selected", tab_images[2]), ("active", tab_images[1]),
        border=(0, 0, 0, 3), sticky="nsew",
    )
    style.layout("TNotebook.Tab", [
        ("FlatNotebook.tab", {"sticky": "nsew", "children": [
            ("Notebook.padding", {"sticky": "nsew", "children": [
                ("Notebook.focus", {"sticky": "nsew", "children": [
                    ("Notebook.label", {"sticky": "nsew"}),
                ]}),
            ]}),
        ]}),
    ])
    style.configure("TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "TNotebook.Tab", padding=(18, 9), background=BACKGROUND, foreground=MUTED,
        borderwidth=0, relief="flat", font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "TNotebook.Tab", background=[("selected", "#253349"), ("active", SURFACE)],
        foreground=[("selected", ACCENT), ("active", TEXT)],
        expand=[("selected", (0, 0, 0, 0)), ("!selected", (0, 0, 0, 0))],
        padding=[("selected", (18, 9)), ("!selected", (18, 9))],
    )
    style.configure("Treeview", background=FIELD, fieldbackground=FIELD, rowheight=32, borderwidth=0)
    style.configure("Treeview.Heading", background="#253349", foreground=MUTED, padding=(10, 8))
    style.map("Treeview", background=[("selected", "#284c7d")], foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", "#344760")])
    style.configure("TScrollbar", background=BORDER, arrowcolor=MUTED, borderwidth=0, arrowsize=14)
    style.map("TScrollbar", background=[("pressed", "#526580"), ("active", "#42536c")])
    style.configure("TScale", background=ACCENT, borderwidth=0, troughcolor=FIELD)
