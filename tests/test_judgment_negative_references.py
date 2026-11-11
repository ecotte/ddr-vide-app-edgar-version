from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from app import config, image_utils, layout_store
from app.analysis import AnalysisSession
from app.event_bus import EventBus
from app.models import LayoutProfile, Region
from app.recognition import JudgmentMatch, match_judgment
from app.ui.app_window import App

pytestmark = pytest.mark.usefixtures("synthetic_judgment_matching")


def image(value):
    return np.full((20, 20, 3), value, dtype=np.uint8)


def encoded(value):
    return image_utils.encode_crop_to_base64_png(image(value))


@pytest.mark.parametrize("positive,negative,state", [
    (.8, .9, "idle"), (.9, .9, "idle"), (.91, .9, "idle"),
    (.92, .9, "idle"), (.93, .9, "perfect"), (.6, .1, "idle"),
])
def test_no_judgment_samples_override_lookalike_words(monkeypatch, positive, negative, state):
    word, background = image(255), image(128)
    monkeypatch.setattr(image_utils, "similarity", lambda crop, ref: positive if ref is word else negative)
    match = match_judgment(image(100), {"perfect": [word], "empty": [background]}, .65)
    assert match.state == state
    assert match.similarity == positive
    assert match.empty_similarity == negative
    assert match.suppressed_by_empty == (positive >= .65 and state == "idle")


def test_negative_samples_are_optional_and_legacy_idle_is_not_reinterpreted():
    word, background = image(255), image(128)
    assert match_judgment(background, {"perfect": [word]}, .45).state == "perfect"
    assert match_judgment(background, {"perfect": [word], "empty": [image(0), background]}, .45).state == "idle"
    assert match_judgment(word, {"perfect": [word], "idle": [word]}, .9).state == "perfect"
    assert match_judgment(word, {"empty": [word]}, .65).state is None


def test_default_confirmation_is_independent_for_arrows_judgments_and_game_end():
    session = AnalysisSession(EventBus())
    regions = [
        Region("Arrow", "P1", "arrow", 0, 0, 20, 20, direction="Center"),
        Region("Word", "P1", "judgment", 20, 0, 20, 20),
        Region("End", "Unknown", "game_end", 40, 0, 20, 20),
    ]
    session.set_regions(regions)
    assert [session.tracker_for(r).min_confirm_frames for r in regions] == [1, 2, 2]
    session.set_arrow_min_confirm_frames(3)
    assert [session.tracker_for(r).min_confirm_frames for r in regions] == [3, 2, 2]
    session.set_judgment_min_confirm_frames(4)
    assert [session.tracker_for(r).min_confirm_frames for r in regions] == [3, 4, 2]
    session.set_regions(regions)
    assert [session.tracker_for(r).min_confirm_frames for r in regions] == [3, 4, 2]


@pytest.mark.parametrize("setter", ["set_arrow_min_confirm_frames", "set_judgment_min_confirm_frames"])
@pytest.mark.parametrize("value", [0, -1, 11, 1.5, True])
def test_confirmation_rejects_invalid_values(setter, value):
    session = AnalysisSession(EventBus())
    with pytest.raises(ValueError, match="integer from 1 to 10"):
        getattr(session, setter)(value)
    assert session.arrow_min_confirm_frames == 1
    assert session.judgment_min_confirm_frames == 2


@pytest.mark.parametrize("player", ["P1", "P2"])
def test_background_cannot_count_but_words_confirm_without_delaying_arrows(player):
    bus = EventBus()
    session = AnalysisSession(bus)
    arrow = Region("Center", player, "arrow", 0, 0, 20, 20, direction="Center",
                   reference_images={"active": [encoded(255)], "empty": [encoded(128)]})
    word = Region("Word", player, "judgment", 20, 0, 20, 20,
                  reference_images={"perfect": [encoded(255)], "empty": [encoded(128)]})
    session.set_regions([arrow, word])
    session.set_judgment_similarity_threshold(.45)
    session.start()
    background = np.concatenate((image(128), image(128)), axis=1)
    first = session.process_arrow_frame(background, 0, 0)
    assert first.region_results[-1].arrow_state == "idle"
    assert "No judgment reference wins" in first.region_results[-1].judgment_diagnostics
    assert "No judgment: 1.000" in first.region_results[-1].judgment_diagnostics
    session.process_arrow_frame(background, 1, 50)
    active = np.concatenate((image(255), image(255)), axis=1)
    first_word = session.process_arrow_frame(active, 2, 100)
    assert [e.event_type for e in first_word.new_events] == ["arrow"]
    assert "Confirming PERFECT: 1/2" in first_word.region_results[-1].judgment_diagnostics
    session.process_arrow_frame(active, 2, 100)
    assert session.tracker_for(word).status().pending_count == 1
    accepted = session.process_arrow_frame(active, 3, 133)
    assert "Accepted PERFECT" in accepted.region_results[-1].judgment_diagnostics
    session.process_arrow_frame(active, 4, 750)
    assert [e.event_type for e in bus.events] == ["new_session", "arrow", "judgement"]
    assert bus.events[-1].payload["judgment_video_time_ms"] == 100
    assert bus.events[-1].player == player


@pytest.mark.parametrize("interruption", ["idle", "ambiguous", "error", "negative"])
def test_judgment_confirmation_requires_consecutive_matching_frames(interruption):
    session = AnalysisSession(EventBus())
    region = Region("Word", "P1", "judgment", 0, 0, 20, 20,
                    reference_images={"perfect": [encoded(255)]})
    session.set_regions([region])
    session.start()
    session.process_arrow_frame(image(255), 0, 0)
    if interruption == "error":
        region.x = 100
        session.process_arrow_frame(image(255), 1, 33)
        region.x = 0
    elif interruption == "ambiguous":
        with patch("app.recognition.match_judgment", return_value=JudgmentMatch(None, .99, "perfect", "great", .99)):
            session.process_arrow_frame(image(255), 1, 33)
    elif interruption == "negative":
        session._arrow_references[region.key()]["empty"] = [image(128)]
        session.set_judgment_similarity_threshold(.45)
        session.process_arrow_frame(image(128), 1, 33)
    else:
        session.process_arrow_frame(image(128), 1, 33)
    session.process_arrow_frame(image(255), 2, 66)
    assert not session._pending_judgments
    assert session.tracker_for(region).status().pending_count == 1
    session.process_arrow_frame(image(255), 3, 100)
    assert len(session._pending_judgments) == 1
    assert session._pending_judgments[0].onset == 66


def test_changing_judgment_confirmation_clears_pending_without_rearming_active_word():
    session = AnalysisSession(EventBus())
    region = Region("Word", "P1", "judgment", 0, 0, 20, 20,
                    reference_images={"perfect": [encoded(255)]})
    session.set_regions([region])
    session.start()
    session.process_arrow_frame(image(255), 0, 0)
    session.set_judgment_min_confirm_frames(3)
    for index in (1, 2):
        session.process_arrow_frame(image(255), index, index * 33)
        assert not session._pending_judgments
    session.process_arrow_frame(image(255), 3, 99)
    assert len(session._pending_judgments) == 1
    session.set_judgment_min_confirm_frames(1)
    session.process_arrow_frame(image(255), 4, 133)
    assert len(session._pending_judgments) == 1
    assert session.tracker_for(region).status().active_state == "perfect"


def test_negative_capture_delete_and_rescaled_layout_keep_players_independent(tmp_path):
    regions = [
        Region(player, player, "judgment", i * 20, 0, 20, 20,
               reference_images={"perfect": [encoded(255)]})
        for i, player in enumerate(("P1", "P2"))
    ]
    session = AnalysisSession(EventBus())
    session.set_regions(regions)
    app = SimpleNamespace(
        regions=regions, analysis=session, _analysis_generation=0,
        _last_frame_bgr=np.concatenate((image(128), image(64)), axis=1),
        video_tab=Mock(), _refresh_arrow_reference_thumbnails=Mock(),
    )
    for region in regions:
        App.capture_arrow_reference(app, region, "empty")
        App.capture_arrow_reference(app, region, "empty")
    assert App.delete_arrow_reference(app, regions[0], "empty", 0)
    assert len(regions[0].reference_images["empty"]) == 1
    assert len(regions[1].reference_images["empty"]) == 2
    path = str(tmp_path / "negatives.json")
    layout_store.save_layout_to_path(LayoutProfile("negative", 40, 20, regions), path)
    loaded = layout_store.rescale_profile(layout_store.load_layout_from_path(path), 80, 40)
    session.set_regions(loaded.regions)
    for region, expected in zip(loaded.regions, (128, 64)):
        refs = session._arrow_references[region.key()]
        assert refs["empty"][0].shape == (40, 40, 3)
        assert np.all(refs["empty"][0] == expected)
        assert match_judgment(refs["empty"][0], refs, .45).state == "idle"
        assert match_judgment(refs["perfect"][0], refs, .45).state == "perfect"
    app.regions = loaded.regions
    App.clear_arrow_reference(app, loaded.regions[0], "empty")
    assert "empty" not in loaded.regions[0].reference_images
    assert "perfect" in loaded.regions[0].reference_images
    assert len(loaded.regions[1].reference_images["empty"]) == 2
