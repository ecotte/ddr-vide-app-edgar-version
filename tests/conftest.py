import pytest


@pytest.fixture
def synthetic_arrow_matching(monkeypatch):
    """Use flat pixels as activity tokens in legacy tracker/event unit tests."""
    from app import analysis, config, recognition
    from app.arrow_shapes import ArrowShapeMatch

    class SyntheticArrowMatcher:
        def __init__(self, references):
            self.references = references
            self.warnings = []
            self.error = None

        def match(self, crop, threshold):
            state, score = recognition.classify_arrow_state(crop, self.references, threshold)
            return ArrowShapeMatch(
                state, score, suppressed_by_empty=(
                    state == "idle" and bool(self.references.get("empty")) and score >= threshold
                ),
            )

    monkeypatch.setattr(analysis, "ArrowShapeMatcher", SyntheticArrowMatcher)
    monkeypatch.setattr(config, "DEFAULT_ARROW_SIMILARITY_THRESHOLD", .9)
    monkeypatch.setattr(
        analysis, "arrow_search_bounds",
        lambda region, width, height: (
            max(0, min(region.x, width)), max(0, min(region.y, height)),
            max(0, min(region.x + region.w, width)), max(0, min(region.y + region.h, height)),
        ),
    )


@pytest.fixture
def synthetic_judgment_matching(monkeypatch, synthetic_arrow_matching):
    """Keep flat-color tracker/event tests independent of real lettering extraction.

    Production defaults and shape extraction are exercised without this fixture
    in test_judgment_shapes.py. These older tests use solid pixels as state tokens.
    """
    from app import analysis, config, recognition

    class SyntheticMatcher:
        def __init__(self, references):
            self.references = references
            self.warnings = []
            self.error = None

        def match(self, crop, threshold):
            return recognition.match_judgment(crop, self.references, threshold)

    monkeypatch.setattr(analysis, "JudgmentShapeMatcher", SyntheticMatcher)
    monkeypatch.setattr(config, "DEFAULT_JUDGMENT_SIMILARITY_THRESHOLD", .9)
    monkeypatch.setattr(
        analysis, "judgment_search_bounds",
        lambda region, width, height: (
            max(0, min(region.x, width)), max(0, min(region.y, height)),
            max(0, min(region.x + region.w, width)), max(0, min(region.y + region.h, height)),
        ),
    )
