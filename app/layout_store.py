"""Save/load named layout profiles as JSON files on disk.

Layout files are plain JSON so they are easy to inspect, back up, or hand
edit. Resolution checks are deliberately explicit: this module never
silently rescales coordinates. Callers decide what to do when a loaded
profile's resolution does not match the current video.
"""
from __future__ import annotations

import json
import os
import re
from typing import List

from app import config
from app.models import LayoutProfile


class ResolutionMismatchError(Exception):
    """Raised when a layout profile's resolution differs from the caller's.

    Carries the profile so the caller (UI) can decide whether to rescale,
    load anyway, or cancel - never done silently.
    """

    def __init__(self, profile: LayoutProfile, current_width: int, current_height: int):
        super().__init__(
            f"Layout '{profile.profile_name}' was calibrated at "
            f"{profile.source_width}x{profile.source_height}, but the current "
            f"video is {current_width}x{current_height}."
        )
        self.profile = profile
        self.current_width = current_width
        self.current_height = current_height


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip())
    if not cleaned:
        raise ValueError("Layout profile name cannot be empty.")
    return cleaned + ".json"


def _path_for_name(name: str) -> str:
    return os.path.join(config.LAYOUTS_DIR, _safe_filename(name))


def list_layout_names() -> List[str]:
    os.makedirs(config.LAYOUTS_DIR, exist_ok=True)
    names = []
    for fname in sorted(os.listdir(config.LAYOUTS_DIR)):
        if fname.lower().endswith(".json"):
            names.append(fname[:-5])
    return names


def save_layout(profile: LayoutProfile) -> str:
    """Save under the built-in named-profile folder (``config.LAYOUTS_DIR``),
    keyed by ``profile.profile_name``. Used by the quick Name+Save workflow."""
    return save_layout_to_path(profile, _path_for_name(profile.profile_name))


def save_layout_to_path(profile: LayoutProfile, path: str) -> str:
    """Save to an arbitrary, caller-chosen file path (e.g. from a Save As
    dialog), regardless of ``config.LAYOUTS_DIR``."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, indent=2)
    return path


def load_layout(profile_name: str) -> LayoutProfile:
    """Load from the built-in named-profile folder (``config.LAYOUTS_DIR``)."""
    return load_layout_from_path(_path_for_name(profile_name))


def load_layout_from_path(path: str) -> LayoutProfile:
    """Load from an arbitrary, caller-chosen file path (e.g. from an Open
    dialog), regardless of ``config.LAYOUTS_DIR``."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return LayoutProfile.from_dict(data)


def load_layout_checked(profile_name: str, current_width: int, current_height: int) -> LayoutProfile:
    """Load a named layout, raising ResolutionMismatchError if it doesn't match.

    Use this from the UI so a resolution change never results in
    coordinates silently being applied to the wrong-sized frame.
    """
    return load_layout_from_path_checked(_path_for_name(profile_name), current_width, current_height)


def load_layout_from_path_checked(path: str, current_width: int, current_height: int) -> LayoutProfile:
    """Load a layout from an arbitrary path, raising ResolutionMismatchError
    if it doesn't match. Same resolution-safety guarantee as
    ``load_layout_checked``, just for a caller-chosen file location."""
    profile = load_layout_from_path(path)
    if profile.source_width != current_width or profile.source_height != current_height:
        raise ResolutionMismatchError(profile, current_width, current_height)
    return profile


def rescale_profile(profile: LayoutProfile, new_width: int, new_height: int) -> LayoutProfile:
    """Return a copy of ``profile`` fitted into a new video resolution.

    The source coordinate space is scaled uniformly and centered in the new
    frame. This preserves region aspect ratios when the old and new videos
    have different aspect ratios. Arrow reference snapshots are resized by
    the same uniform scale so they stay comparable to future crops.
    """
    from app import image_utils
    from app.models import Region

    if profile.source_width <= 0 or profile.source_height <= 0:
        raise ValueError("Layout source dimensions must be positive.")
    if new_width <= 0 or new_height <= 0:
        raise ValueError("New video dimensions must be positive.")

    scale = min(new_width / profile.source_width, new_height / profile.source_height)
    offset_x = (new_width - profile.source_width * scale) / 2
    offset_y = (new_height - profile.source_height * scale) / 2
    scaled_regions = []
    for r in profile.regions:
        region_width = max(1, round(r.w * scale))
        region_height = max(1, round(r.h * scale))
        rescaled_refs = {}
        for state_name, encoded_images in r.reference_images.items():
            resized_images = []
            for b64 in encoded_images:
                img = image_utils.decode_base64_png_to_crop(b64)
                if img is None:
                    continue
                resized = image_utils.resize_crop(img, region_width, region_height)
                resized_images.append(image_utils.encode_crop_to_base64_png(resized))
            if resized_images:
                rescaled_refs[state_name] = resized_images
        scaled_regions.append(
            Region(
                label=r.label,
                player=r.player,
                kind=r.kind,
                x=round(offset_x + r.x * scale),
                y=round(offset_y + r.y * scale),
                w=region_width,
                h=region_height,
                direction=r.direction,
                reference_images=rescaled_refs,
            )
        )
    return LayoutProfile(
        profile_name=profile.profile_name,
        source_width=new_width,
        source_height=new_height,
        regions=scaled_regions,
    )
