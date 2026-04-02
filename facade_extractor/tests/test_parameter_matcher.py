"""Tests for matchers/parameter_matcher.py + fuzzy_matcher.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from extractors.dimension_extractor import RawMeasurement
from matchers.parameter_matcher import ParameterMatcher, SpecCheck
from matchers.fuzzy_matcher import score_against_parameter, find_best_match

# ── Sample catalog ────────────────────────────────────────────────────────────

CATALOG = [
    {
        "id": 1,
        "name": "Panel Width",
        "aliases": ["bay width", "panel modulation", "module width"],
        "extraction_type": "LINEAR",
        "unit": "mm",
        "dimension_direction": "HORIZONTAL",
        "relevant_sheet_types": ["PLAN", "ELEVATION", "DETAIL"],
        "relevant_layer_groups": ["structural_frame"],
        "spec_value": None,
        "spec_tolerance": None,
        "confidence_threshold": 0.40,
    },
    {
        "id": 5,
        "name": "Glass Thickness",
        "aliases": ["glazing thickness", "glass thk", "pane thickness"],
        "extraction_type": "LINEAR",
        "unit": "mm",
        "dimension_direction": "ANY",
        "relevant_sheet_types": ["SECTION", "DETAIL"],
        "relevant_layer_groups": ["glazing"],
        "spec_value": None,
        "spec_tolerance": None,
        "confidence_threshold": 0.40,
    },
]

SPEC_REFS = [
    {
        "parameter_name": "Glass Thickness",
        "spec_value": 6.0,
        "unit": "mm",
        "tolerance": 0.0,
        "direction": "MIN",
        "source": "Test Spec Clause 3",
    }
]


# ── fuzzy_matcher ─────────────────────────────────────────────────────────────

def test_score_exact_alias():
    s = score_against_parameter(
        context_words=["bay", "width"],
        source_text="bay width 1500",
        param=CATALOG[0],
    )
    assert s.score > 0.5
    assert s.keyword_hit is True


def test_score_direction_mismatch_penalty():
    s_ok = score_against_parameter(
        context_words=["panel", "width"],
        source_text="1500",
        param=CATALOG[0],
        measurement_direction="HORIZONTAL",
    )
    s_bad = score_against_parameter(
        context_words=["panel", "width"],
        source_text="1500",
        param=CATALOG[0],
        measurement_direction="VERTICAL",
    )
    assert s_ok.score >= s_bad.score


def test_find_best_match():
    best = find_best_match(
        context_words=["glass", "thickness", "pane"],
        source_text="10mm",
        catalog=CATALOG,
        min_score=0.20,
    )
    assert best is not None
    assert best.parameter_id == 5


def test_find_best_match_no_hit():
    best = find_best_match(
        context_words=["irrelevant", "stuff"],
        source_text="xyz",
        catalog=CATALOG,
        min_score=0.99,   # very high threshold
    )
    assert best is None


# ── ParameterMatcher ──────────────────────────────────────────────────────────

def _meas(value_mm, context_words, direction="ANY", method="DIMENSION_ENTITY", conf=0.90):
    return RawMeasurement(
        value_mm=value_mm,
        unit="mm",
        confidence=conf,
        extraction_method=method,
        source_text=str(value_mm),
        context_words=context_words,
        direction=direction,
    )


def test_matcher_basic():
    m = _meas(1500.0, ["bay", "width"], direction="HORIZONTAL")
    matcher = ParameterMatcher(catalog=CATALOG)
    matched, unmatched = matcher.match([m], sheet_type="ELEVATION")
    ids = [mp.id for mp in matched]
    assert 1 in ids


def test_matcher_spec_match():
    m = _meas(10.0, ["glass", "thickness", "pane"])
    matcher = ParameterMatcher(catalog=CATALOG, spec_refs=SPEC_REFS)
    matched, _ = matcher.match([m])
    glass = next((mp for mp in matched if mp.id == 5), None)
    assert glass is not None
    assert glass.spec_check.result == "MATCH"   # 10 >= 6 min


def test_matcher_spec_conflict():
    m = _meas(4.0, ["glass", "thickness", "pane"])  # < 6mm min
    matcher = ParameterMatcher(catalog=CATALOG, spec_refs=SPEC_REFS)
    matched, _ = matcher.match([m])
    glass = next((mp for mp in matched if mp.id == 5), None)
    if glass:
        assert glass.spec_check.result == "CONFLICT"


def test_matcher_unmatched():
    m = _meas(99999.0, ["nonsense", "random", "zzz"])
    matcher = ParameterMatcher(catalog=CATALOG)
    matched, unmatched = matcher.match([m])
    # Should end up in unmatched
    assert unmatched   # at least one unmatched


def test_matcher_confidence_below_threshold():
    m = _meas(1500.0, ["bay", "width"], conf=0.10)  # very low conf
    matcher = ParameterMatcher(catalog=CATALOG, min_confidence=0.40)
    matched, _ = matcher.match([m])
    # Should be filtered out by threshold
    panel = next((mp for mp in matched if mp.id == 1), None)
    assert panel is None
