"""Tests for extractors/pattern_extractor.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from parsers.base_parser import DrawingSheet, LineSegment, Point2D
from classifiers.scale_extractor import ScaleResult
from extractors.pattern_extractor import extract_patterns


def _sheet_with_x_spacing(spacing_mm: float, count: int = 8):
    lines = []
    for i in range(count):
        x = i * spacing_mm
        lines.append(LineSegment(
            start=Point2D(x, 0), end=Point2D(x, 3000)
        ))
    sheet = DrawingSheet()
    sheet.lines = lines
    sheet.scale_result = ScaleResult(
        scale_denominator=1, source="TITLEBLOCK",
        drawing_unit="mm", mm_per_unit=1.0
    )
    return sheet


def _sheet_with_y_spacing(spacing_mm: float, count: int = 6):
    lines = []
    for i in range(count):
        y = i * spacing_mm
        lines.append(LineSegment(
            start=Point2D(0, y), end=Point2D(3000, y)
        ))
    sheet = DrawingSheet()
    sheet.lines = lines
    sheet.scale_result = ScaleResult(
        scale_denominator=1, source="TITLEBLOCK",
        drawing_unit="mm", mm_per_unit=1.0
    )
    return sheet


def test_detect_1500_horizontal():
    sheet = _sheet_with_x_spacing(1500)
    results = extract_patterns(sheet)
    h_spacings = [r.value_mm for r in results if r.direction == "HORIZONTAL"]
    assert any(abs(v - 1500.0) < 50 for v in h_spacings), h_spacings


def test_detect_3600_vertical():
    sheet = _sheet_with_y_spacing(3600)
    results = extract_patterns(sheet)
    v_spacings = [r.value_mm for r in results if r.direction == "VERTICAL"]
    assert any(abs(v - 3600.0) < 100 for v in v_spacings), v_spacings


def test_confidence_range():
    sheet = _sheet_with_x_spacing(600, count=10)
    results = extract_patterns(sheet)
    for r in results:
        assert 0.0 <= r.confidence <= 1.0


def test_empty_sheet():
    sheet = DrawingSheet()
    sheet.scale_result = ScaleResult(source="UNKNOWN")
    results = extract_patterns(sheet)
    assert results == []


def test_extraction_method_label():
    sheet = _sheet_with_x_spacing(600)
    results = extract_patterns(sheet)
    for r in results:
        assert r.extraction_method == "PATTERN"
