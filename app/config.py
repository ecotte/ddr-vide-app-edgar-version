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
SOURCE_MODE_LIVE = "live"           # HDMI/USB video capture
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

SCORE_DIRECT_ACCEPT_CONFIDENCE = 0.70
SCORE_EVIDENCE_MIN_CONFIDENCE = 0.30
NUMERIC_CONFIRMATION_SAMPLES = 2
NUMERIC_CONFIRMATION_WINDOW_MS = 2000
SCORE_TEMPLATE_VERSION = 1
DIGIT_TEMPLATE_VERSIONS = {"score": SCORE_TEMPLATE_VERSION, "combo": 2}

# Combo digits may be completely hidden by passing arrows. OCR output from
# those frames must not replace a previously trusted combo with an impossible
# jump. Lower values require repeated high-confidence numeric evidence.
COMBO_MIN_ALLOWED_INCREASE = 10
COMBO_MAX_INCREASE_PER_SECOND = 60
COMBO_MIN_CONFIDENCE = 0.90

# Recognized region "kinds" for milestone 1+. Each numeric kind maps to an
# event type; arrow outcomes are determined by judgment correlation.
FIELD_EVENT_TYPES = {
    "score": "score_changed",
    "combo": "combo_changed",
}
SCORE_OBSERVED_EVENT_TYPE = "score_observed"

REGION_KINDS = ["score", "combo", "arrow", "judgment", "game_end"]
JUDGMENT_CORRELATION_LOOKBACK_MS = 500
JUDGMENT_CORRELATION_LOOKAHEAD_MS = 150
SIMULTANEOUS_STEP_WINDOW_MS = 150
JUDGMENT_REARM_MS = 250
JUDGMENT_POINTS = {"good": 10, "great": 20, "perfect": 40, "miss": 0}
NEW_SESSION_EVENT_TYPE = "new_session"
SESSION_END_EVENT_TYPE = "session_end"
DEFAULT_GAME_END_SIMILARITY_THRESHOLD = 0.95
DEFAULT_GAME_END_CONFIRM_FRAMES = 2

PLAYER_CHOICES = ["P1", "P2", "Unknown"]

# --- Arrow activity detection -------------------------------------------
# One region per (player, direction) is compared against user-captured
# reference snapshots ("active"). Only a matched player
# judgment determines hit/miss; activity never implies an outcome.
ARROW_DIRECTIONS = ["Up-Right", "Up-Left", "Down-Right", "Down-Left", "Center"]
ARROW_STATES = ["active", "empty"]
ARROW_EMPTY_REFERENCE_MARGIN = 0.02

# Similarity score (0..1, see app.image_utils.similarity) above which a live
# crop is considered a match for a captured reference. Configurable from the
# UI; higher = fewer false positives but more likely to miss real hits/misses
# if lighting/animation varies frame to frame.
DEFAULT_ARROW_SIMILARITY_THRESHOLD = 0.90

# Consecutive frames a candidate arrow state must be observed before it is
# confirmed and correlated with a judgment. Activity flashes can be very
# brief (sometimes a single frame), so this defaults low; raise it only if
# single-frame noise is producing false events.
DEFAULT_ARROW_MIN_CONFIRM_FRAMES = 1

# --- OCR ------------------------------------------------------------------
# EasyOCR language model(s) to load. Kept to English/digits for score+combo.
OCR_LANGUAGES = ["en"]
# Score glyph OCR uses this allowlist; combo OCR recognizes surrounding text
# and then keeps only digits, rather than forcing words into numbers.
OCR_DIGIT_ALLOWLIST = "0123456789"
