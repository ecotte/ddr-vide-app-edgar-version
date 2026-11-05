"""Plain data structures shared across the application.

Keeping these as simple dataclasses (rather than spreading dicts around)
makes it easier to reason about what a "region", "event", etc. actually
contains, and gives us one place to add fields later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Region:
    """A single labeled rectangle calibrated against one source resolution.

    Coordinates are stored in source-video pixel space (not canvas/display
    space), so they remain meaningful regardless of how the video is scaled
    for on-screen display.

    ``direction`` is only used when ``kind == "arrow"`` and is one of config.ARROW_DIRECTIONS
    ("Up-Right"/"Up-Left"/"Down-Right"/"Down-Left"/"Center"), and ``reference_images`` holds
    user-captured example crops (base64 PNG) grouped by state name ("active",
    optional "appeared") that the arrow classifier compares live frames against.
    Legacy arrow "hit"/"miss" images are ignored and require fresh activity calibration. These
    are never pre-supplied by the app - only what the user captures from
    their own footage. ``game_end`` regions use ``reference_images["ended"]``
    for a shared, game-wide end indicator.
    Judgment regions use "perfect", "great", "good", and "miss" references;
    legacy "idle" images are retained in layouts but ignored by recognition.
    old "hit" references retain ungraded success behavior until tier calibration starts.
    """

    label: str
    player: str  # one of config.PLAYER_CHOICES, e.g. "P1", "P2", "Unknown"
    kind: str    # "score", "combo", "arrow", "judgment", or "game_end"
    x: int
    y: int
    w: int
    h: int
    direction: Optional[str] = None  # only for kind == "arrow"
    reference_images: Dict[str, List[str]] = field(default_factory=dict)
    digit_templates: Dict[str, List[str]] = field(default_factory=dict)
    digit_templates_reviewed: bool = False
    score_template_version: int = 0
    combo_yellow_filter: bool = False
    combo_fast_mode: bool = False

    def __post_init__(self) -> None:
        normalized = {}
        for state, images in self.reference_images.items():
            if isinstance(images, str):
                normalized[state] = [images]
            elif isinstance(images, list) and all(isinstance(image, str) for image in images):
                normalized[state] = list(images)
            else:
                raise ValueError(f"Reference images for '{state}' must be a string or list of strings.")
        self.reference_images = normalized
        normalized_templates = {}
        for digit, images in self.digit_templates.items():
            if isinstance(images, str):
                normalized_templates[digit] = [images]
            elif isinstance(images, list) and all(isinstance(image, str) for image in images):
                normalized_templates[digit] = list(images)
            else:
                raise ValueError(f"Digit templates for '{digit}' must be a string or list of strings.")
        self.digit_templates = normalized_templates

    def key(self) -> str:
        """Stable identity for this region used to key recognition trackers."""
        if self.kind == "game_end":
            return "game_end"
        if self.kind == "arrow":
            return f"{self.player}:{self.kind}:{self.direction}"
        return f"{self.player}:{self.kind}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "player": self.player,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "direction": self.direction,
            "reference_images": {
                state: list(images) for state, images in self.reference_images.items()
            },
            "digit_templates": {
                digit: list(images) for digit, images in self.digit_templates.items()
            },
            "digit_templates_reviewed": self.digit_templates_reviewed,
            "score_template_version": self.score_template_version,
            "combo_yellow_filter": self.combo_yellow_filter,
            "combo_fast_mode": self.combo_fast_mode,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Region":
        return Region(
            label=d["label"],
            player=d["player"],
            kind=d["kind"],
            x=int(d["x"]),
            y=int(d["y"]),
            w=int(d["w"]),
            h=int(d["h"]),
            direction=d.get("direction"),
            reference_images=dict(d.get("reference_images", {})),
            digit_templates=dict(d.get("digit_templates", {})),
            digit_templates_reviewed=d.get("digit_templates_reviewed") is True,
            score_template_version=int(d.get("score_template_version", 0)),
            combo_yellow_filter=d.get("combo_yellow_filter") is True,
            combo_fast_mode=d.get("combo_fast_mode") is True,
        )


@dataclass
class LayoutProfile:
    """A named, saved set of regions calibrated for one source resolution."""

    profile_name: str
    source_width: int
    source_height: int
    regions: list = field(default_factory=list)  # list[Region]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "regions": [r.to_dict() for r in self.regions],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LayoutProfile":
        return LayoutProfile(
            profile_name=d["profile_name"],
            source_width=int(d["source_width"]),
            source_height=int(d["source_height"]),
            regions=[Region.from_dict(r) for r in d.get("regions", [])],
        )


def p1_reference_source(region: Region, regions: list[Region]) -> Optional[Region]:
    """Resolve the matching P1 box without sharing geometry or tracking state."""
    if region.player != "P2" or region.kind not in ("arrow", "judgment", "score", "combo"):
        return region
    key = f"P1:{region.kind}"
    if region.kind == "arrow":
        key += f":{region.direction}"
    return next((candidate for candidate in regions if candidate.key() == key), None)


def share_p1_references(regions: list[Region]) -> None:
    for region in regions:
        source = p1_reference_source(region, regions)
        if source is region:
            continue
        region.reference_images = {
            state: list(images) for state, images in source.reference_images.items()
        } if source else {}
        region.digit_templates = {
            digit: list(images) for digit, images in source.digit_templates.items()
        } if source else {}
        region.digit_templates_reviewed = source.digit_templates_reviewed if source else False
        region.score_template_version = source.score_template_version if source else 0


@dataclass
class Event:
    """The event envelope described in the project spec.

    ``confidence`` is only ever set from a value actually reported by the
    OCR library, and must be treated as an OCR engine score, not a
    guaranteed accuracy probability.
    """

    schema_version: str
    event_id: str
    session_id: str
    sequence: int
    source_mode: str
    frame_index: Optional[int]
    video_time_ms: Optional[int]
    observed_at_utc: str
    event_type: str
    player: Optional[str]
    payload: Dict[str, Any]
    confidence: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "source_mode": self.source_mode,
            "frame_index": self.frame_index,
            "video_time_ms": self.video_time_ms,
            "observed_at_utc": self.observed_at_utc,
            "event_type": self.event_type,
            "player": self.player,
            "payload": self.payload,
            "confidence": self.confidence,
        }
