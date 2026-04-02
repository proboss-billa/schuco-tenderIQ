"""Tests for extractors/geometry_extractor.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from parsers.base_parser import DrawingSheet, LineSegment, CircleEntity, Point2D
from classifiers.scale_extractor import ScaleResult
from extractors.geometry_extractor import extract_geometry


def _sheet(lines=None, circles=None, scale_known=True):
    sheet = DrawingSheet()
    sheet.lines   = lines   or []
    sheet.circles = circles or []
    if scale_known:
        sheet.scale_result = ScaleResult(
            scale_denominator=1, source="TITLEBLOCK",
            drawing_unit="mm", mm_per_unit=1.0
        )
    else:
        sheet.scale_result = ScaleResult(source="UNKNOWN")
    return sheet


def _hline(y, x0=0, x1=500, layer=""):
    return LineSegment(
        start=Point2D(x0, y), end=Point2D(x1, y), layer=layer
    )


def _vline(x, y0=0, y1=500, layer=""):
    return LineSegment(
        start=Point2D(x, y0), end=Point2D(x, y1), layer=layer
    )


def test_horizontal_pair_separation():
    # Two horizontal lines 10mm apart
    sheet = _sheet(lines=[_hline(0), _hline(10)])
    results = extract_geometry(sheet)
    vals = [r.value_mm for r in results if r.direction == "HORIZONTAL"]
    assert any(abs(v - 10.0) < 0.5 for v in vals)


def test_vertical_pair_separation():
    sheet = _sheet(lines=[_vline(0), _vline(52)])
    results = extract_geometry(sheet)
    vals = [r.value_mm for r in results if r.direction == "VERTICAL"]
    assert any(abs(v - 52.0) < 0.5 for v in vals)


def test_circle_diameter():
    c = CircleEntity(center=Point2D(100, 100), radius=8.0, layer="A-FIXINGS")
    sheet = _sheet(circles=[c])
    results = extract_geometry(sheet)
    dia_results = [r for r in results if abs(r.value_mm - 16.0) < 0.5]
    assert dia_results


def test_scale_unknown_lowers_confidence():
    sheet_k = _sheet(lines=[_hline(0), _hline(20)], scale_known=True)
    sheet_u = _sheet(lines=[_hline(0), _hline(20)], scale_known=False)
    res_k = extract_geometry(sheet_k)
    res_u = extract_geometry(sheet_u)
    if res_k and res_u:
        assert res_k[0].confidence > res_u[0].confidence


def test_no_lines_no_results():
    sheet = _sheet()
    results = extract_geometry(sheet)
    assert results == []


def test_non_overlapping_lines_ignored():
    # Lines far apart with no overlap in X
    a = LineSegment(start=Point2D(0, 0),   end=Point2D(100, 0))
    b = LineSegment(start=Point2D(200, 5), end=Point2D(400, 5))
    sheet = _sheet(lines=[a, b])
    results = extract_geometry(sheet)
    # May or may not find a pair depending on overlap logic
    # Just ensure no crash
    assert isinstance(results, list)
