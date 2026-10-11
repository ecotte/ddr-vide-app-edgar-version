"""Central configuration and constants for the DDR Video Event Extractor.

Keep tunable values here so they are easy to find and change without
touching recognition or UI logic.
"""
from __future__ import annotations

import os

# --- Paths -------------------------------------------------------------
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUTS_DIR = os.path.join(APP_ROOT, "layouts")
DATA_DIR = os.path.join(APP_ROOT, "data")

# --- Event envelope ------------------------------------------------------
SCHEMA_VERSION = "1.0"

SOURCE_MODE_REPLAY = "replay"
SOURCE_MODE_LIVE = "live"           # reserved for a later milestone (HDMI capture)
SOURCE_MODE_SYNTHETIC = "synthetic"  # used only by tests/demos, never real gameplay

# --- Recognition tuning ---------------------------------------------------
# Number of consecutive matching frame readings required before a new value
# is "accepted" and an event is emitted. Higher values reject more transient
# digit-animation glitches but delay detection of genuine fast changes
# (e.g. rapid combo increments). This is intentionally configurable from the
# UI (Video tab) rather than hard-coded.
DEFAULT_REQUIRED_CONSECUTIVE_FRAMES = 1

# Sanity ceilings used as a format check: OCR text producing more digits than
# this for a given field kind is treated as an invalid/unreadable reading
# rather than accepted at face value (guards against OCR bleeding in
# neighboring UI text).
FIELD_MAX_DIGITS = {
    "score": 10,
    "combo": 5,
}

SCORE_MIN_GLYPH_CONFIDENCE = 0.90

# Combo digits may be completely hidden by passing arrows. OCR output from
# those frames must not replace a previously trusted combo with an impossible
# jump. Genuine combo resets become visible again at a low value.
COMBO_MIN_ALLOWED_INCREASE = 10
COMBO_MAX_INCREASE_PER_SECOND = 60
COMBO_RESET_MAX_VALUE = 30

# Recognized region "kinds" for milestone 1+. Each numeric kind maps to an
# event type; "arrow" is handled separately (see ARROW_EVENT_TYPES below).
FIELD_EVENT_TYPES = {
    "score": "score_changed",
    "combo": "combo_changed",
}

REGION_KINDS = ["score", "combo", "arrow"]

PLAYER_CHOICES = ["P1", "P2", "Unknown"]

# --- Arrow (receptor) hit/miss detection ----------------------------------
# One region per (player, direction) is compared against user-captured
# reference snapshots ("hit", "miss") rather than any built-in template -
# this app has no built-in idea of what a hit/miss looks like for a given
# note skin.
ARROW_DIRECTIONS = ["Up", "Down", "Left", "Right"]
ARROW_STATES = ["hit", "miss"]
ARROW_EVENT_TYPES = {
    "hit": "arrow_hit",
    "miss": "arrow_miss",
}

# Similarity score (0..1, see app.image_utils.similarity) above which a live
# crop is considered a match for a captured reference. Configurable from the
# UI; higher = fewer false positives but more likely to miss real hits/misses
# if lighting/animation varies frame to frame.
DEFAULT_ARROW_SIMILARITY_THRESHOLD = 0.90

# Consecutive frames a candidate arrow state must be observed before it is
# confirmed and turned into an event. Real hit/miss flashes can be very
# brief (sometimes a single frame), so this defaults low; raise it only if
# single-frame noise is producing false events.
DEFAULT_ARROW_MIN_CONFIRM_FRAMES = 1

# --- OCR ------------------------------------------------------------------
# EasyOCR language model(s) to load. Kept to English/digits for score+combo.
OCR_LANGUAGES = ["en"]
# Digit-only allowlist keeps OCR focused on numeric fields, which are the
# only field kinds supported in milestone 1.
OCR_DIGIT_ALLOWLIST = "0123456789"
