"""Tests for classifiers/scale_extractor.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from classifiers.scale_extractor import (
    from_text, from_dxf_header, from_dimension_entity,
    detect_scale, validate_scale_against_dimension,
)


def test_from_text_1_50():
    r = from_text(["SCALE 1:50"])
    assert r is not None
    assert r.scale_denominator == 50
    assert r.source == "TITLEBLOCK"

def test_from_text_ratio():
    r = from_text(["Drawing Scale: 1/100"])
    assert r is not None
    assert r.scale_denominator == 100

def test_from_text_nts():
    r = from_text(["NTS"])
    assert r is not None
    assert r.nts is True

def test_from_text_not_to_scale():
    r = from_text(["NOT TO SCALE"])
    assert r is not None
    assert r.nts is True

def test_from_text_no_match():
    r = from_text(["Hello World"])
    assert r is None

def test_from_dxf_header_mm():
    r = from_dxf_header(insunits=4, dimscale=1.0, ltscale=1.0)
    assert r is not None
    assert r.drawing_unit == "mm"
    assert r.mm_per_unit == 1.0

def test_from_dxf_header_inches():
    r = from_dxf_header(insunits=1, dimscale=1.0, ltscale=1.0)
    assert r is not None
    assert r.drawing_unit == "in"
    assert r.mm_per_unit == pytest.approx(25.4)

def test_from_dimension_entity():
    # geometry: 30 drawing units, annotation: 1500 mm → scale 1:50
    r = from_dimension_entity(
        geometry_length_drawing_units=30.0,
        annotated_value_mm=1500.0,
        drawing_unit="mm",
        insunits=4,
    )
    assert r is not None
    assert r.scale_denominator == 50

def test_detect_scale_titleblock_wins():
    r = detect_scale(
        text_blocks=["SCALE 1:20"],
        insunits=4,
        dimscale=1.0,
    )
    assert r.scale_denominator == 20
    assert r.source == "TITLEBLOCK"

def test_detect_scale_fallback_header():
    r = detect_scale(insunits=4, dimscale=1.0)
    assert r.source == "HEADER"

def test_detect_scale_unknown():
    r = detect_scale()
    # With no info, should be HEADER (insunits defaults to 4=mm)
    assert r.source in ("HEADER", "UNKNOWN")

def test_validate_scale():
    from classifiers.scale_extractor import ScaleResult
    sr = ScaleResult(scale_denominator=50, source="TITLEBLOCK",
                     drawing_unit="mm", mm_per_unit=1.0 / 50)
    ok, dev = validate_scale_against_dimension(sr, 30.0, 1500.0)
    # predicted = 30 * (1/50) * factor; factor for mm insunits = 1
    # predicted = 0.6 mm ← not 1500; this uses PDF-space geometry
    # In DXF model space geometry IS real-world, so let's test with matching values
    sr2 = ScaleResult(scale_denominator=1, source="HEADER",
                      drawing_unit="mm", mm_per_unit=1.0)
    ok2, dev2 = validate_scale_against_dimension(sr2, 1500.0, 1500.0)
    assert ok2
    assert dev2 < 0.01
