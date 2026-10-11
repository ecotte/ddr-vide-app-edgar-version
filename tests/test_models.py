"""Tests for app.models.Region: arrow-specific fields and key uniqueness."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Region


def test_numeric_region_key_ignores_direction():
    r = Region(label="Score", player="P1", kind="score", x=0, y=0, w=10, h=10)
    assert r.key() == "P1:score"


def test_arrow_region_key_includes_direction():
    left = Region(label="Left", player="P1", kind="arrow", x=0, y=0, w=10, h=10, direction="Left")
    right = Region(label="Right", player="P1", kind="arrow", x=0, y=0, w=10, h=10, direction="Right")
    assert left.key() == "P1:arrow:Left"
    assert right.key() == "P1:arrow:Right"
    assert left.key() != right.key()


def test_arrow_region_round_trips_through_dict():
    r = Region(
        label="P1 Left", player="P1", kind="arrow", x=1, y=2, w=3, h=4, direction="Left",
        reference_images={"hit": "deadbeef"},
    )
    restored = Region.from_dict(r.to_dict())
    assert restored.direction == "Left"
    assert restored.reference_images == {"hit": ["deadbeef"]}
    assert restored.key() == r.key()


def test_legacy_single_reference_is_upgraded_to_a_list():
    restored = Region.from_dict({
        "label": "P1 Left", "player": "P1", "kind": "arrow",
        "x": 1, "y": 2, "w": 3, "h": 4, "direction": "Left",
        "reference_images": {"hit": "legacy-image"},
    })
    assert restored.reference_images == {"hit": ["legacy-image"]}


def test_numeric_region_round_trip_defaults_direction_to_none():
    r = Region(label="Score", player="P1", kind="score", x=0, y=0, w=10, h=10)
    restored = Region.from_dict(r.to_dict())
    assert restored.direction is None
    assert restored.reference_images == {}
