"""Tests for app.models.Region: arrow-specific fields and key uniqueness."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import config
from app.models import Region


def test_numeric_region_key_ignores_direction():
    r = Region(label="Score", player="P1", kind="score", x=0, y=0, w=10, h=10)
    assert r.key() == "P1:score"


def test_arrow_region_key_includes_direction():
    assert config.ARROW_DIRECTIONS == ["Up-Right", "Up-Left", "Down-Right", "Down-Left", "Center"]
    keys = [
        Region(direction, player, "arrow", 0, 0, 10, 10, direction=direction).key()
        for player in ("P1", "P2") for direction in config.ARROW_DIRECTIONS
    ]
    assert len(set(keys)) == 10
    assert "P1:arrow:Center" in keys
    assert "P2:arrow:Up-Right" in keys


@pytest.mark.parametrize("direction", config.ARROW_DIRECTIONS)
def test_arrow_region_round_trips_through_dict(direction):
    r = Region(
        label=f"P1 {direction}", player="P1", kind="arrow", x=1, y=2, w=3, h=4, direction=direction,
        reference_images={"hit": "deadbeef"},
    )
    restored = Region.from_dict(r.to_dict())
    assert restored.direction == direction
    assert restored.reference_images == {"hit": ["deadbeef"]}
    assert restored.key() == r.key()


def test_legacy_single_reference_is_upgraded_to_a_list():
    restored = Region.from_dict({
        "label": "P1 Left", "player": "P1", "kind": "arrow",
        "x": 1, "y": 2, "w": 3, "h": 4, "direction": "Left",
        "reference_images": {"hit": "legacy-image"},
    })
    assert restored.reference_images == {"hit": ["legacy-image"]}


def test_score_digit_templates_round_trip():
    region = Region(
        label="Score", player="P1", kind="score", x=1, y=2, w=3, h=4,
        digit_templates={"9": ["template-a", "template-b"]},
        digit_templates_reviewed=True,
    )

    restored = Region.from_dict(region.to_dict())

    assert restored.digit_templates == {"9": ["template-a", "template-b"]}
    assert restored.digit_templates_reviewed is True


def test_old_layout_templates_are_not_automatically_trusted():
    data = Region(
        label="Score", player="P1", kind="score", x=0, y=0, w=10, h=10,
        digit_templates={"9": ["old-automatic-example"]},
    ).to_dict()
    del data["digit_templates_reviewed"]
    assert Region.from_dict(data).digit_templates_reviewed is False


def test_numeric_region_round_trip_defaults_direction_to_none():
    r = Region(label="Score", player="P1", kind="score", x=0, y=0, w=10, h=10)
    restored = Region.from_dict(r.to_dict())
    assert restored.direction is None
    assert restored.reference_images == {}
