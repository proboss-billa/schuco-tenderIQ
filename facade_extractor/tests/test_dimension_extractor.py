"""Tests for extractors/dimension_extractor.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from parsers.base_parser import (
    DrawingSheet, DimensionEntity, TextEntity, LineSegment, Point2D
)
from classifiers.scale_extractor import ScaleResult
from extractors.dimension_extractor import DimensionExtractor, extract_dimensions


def _make_sheet(dims=None, texts=None, lines=None, scale_known=True):
    sheet = DrawingSheet()
    sheet.dimensions = dims or []
    sheet.texts = texts or []
    sheet.lines = lines or []
    if scale_known:
        sheet.scale_result = ScaleResult(
            scale_denominator=50, source="TITLEBLOCK",
            drawing_unit="mm", mm_per_unit=1.0 / 50
        )
    else:
        sheet.scale_result = ScaleResult(source="UNKNOWN")
    return sheet


def test_pass_a_basic():
    dim = DimensionEntity(value_mm=1500.0, raw_text="1500", dim_type="LINEAR", layer="A-DIM")
    sheet = _make_sheet(dims=[dim])
    results = extract_dimensions(sheet)
    assert any(r.value_mm == pytest.approx(1500.0) for r in results)


def test_pass_a_confidence_high():
    dim = DimensionEntity(value_mm=600.0, raw_text="600")
    sheet = _make_sheet(dims=[dim])
    results = extract_dimensions(sheet)
    r = next(r for r in results if r.extraction_method == "DIMENSION_ENTITY")
    assert r.confidence >= 0.85


def test_pass_a_scale_unknown_penalty():
    dim = DimensionEntity(value_mm=600.0, raw_text="600")
    sheet_known   = _make_sheet(dims=[dim], scale_known=True)
    sheet_unknown = _make_sheet(dims=[dim], scale_known=False)
    r_known   = extract_dimensions(sheet_known)
    r_unknown = extract_dimensions(sheet_unknown)
    assert r_known[0].confidence > r_unknown[0].confidence


def test_pass_b_text_mm():
    te = TextEntity(text="Mullion face width 52mm", x=0, y=0, layer="A-TEXT")
    sheet = _make_sheet(texts=[te])
    results = extract_dimensions(sheet)
    vals = [r.value_mm for r in results if r.extraction_method in ("TEXT", "MTEXT")]
    assert 52.0 in vals or any(abs(v - 52.0) < 0.5 for v in vals)


def test_pass_b_context_words_collected():
    te = TextEntity(text="Air gap 16mm between panes", x=100, y=100)
    sheet = _make_sheet(texts=[te])
    results = extract_dimensions(sheet)
    text_results = [r for r in results if r.value_mm == pytest.approx(16.0)]
    if text_results:
        words = text_results[0].context_words
        assert any("gap" in w or "air" in w or "pane" in w for w in words)


def test_zero_value_dim_ignored():
    dim = DimensionEntity(value_mm=0.0, raw_text="0")
    sheet = _make_sheet(dims=[dim])
    results = extract_dimensions(sheet)
    assert not any(r.value_mm == 0.0 for r in results)


def test_multiple_dims():
    dims = [
        DimensionEntity(value_mm=1500.0, raw_text="1500"),
        DimensionEntity(value_mm=600.0,  raw_text="600"),
        DimensionEntity(value_mm=52.0,   raw_text="52"),
    ]
    sheet = _make_sheet(dims=dims)
    results = extract_dimensions(sheet)
    vals = {r.value_mm for r in results}
    assert {1500.0, 600.0, 52.0}.issubset(vals)
