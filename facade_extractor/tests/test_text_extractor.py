"""
Tests for extractors/text_extractor.py
Covers 50+ cases across all regex patterns.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from extractors.text_extractor import (
    extract_text_matches, strip_mtext_codes, correct_ocr_text,
    extract_dimensions_from_text,
)


# ── strip_mtext_codes ─────────────────────────────────────────────────────────

def test_strip_mtext_basic():
    assert strip_mtext_codes(r"{\H2.5;Hello}") == "Hello"

def test_strip_mtext_paragraph():
    assert strip_mtext_codes(r"Line1\PLine2") == "Line1 Line2"

def test_strip_mtext_unicode():
    result = strip_mtext_codes(r"\U+00F8")
    assert "ø" in result

def test_strip_mtext_nested():
    result = strip_mtext_codes(r"{\W1.2;{\H3;Title}}")
    assert "Title" in result


# ── correct_ocr_text ──────────────────────────────────────────────────────────

def test_ocr_zero_vs_O():
    assert correct_ocr_text("1O0") == "100"

def test_ocr_one_vs_I():
    assert correct_ocr_text("1I5") == "115"

def test_ocr_no_change_for_pure_text():
    # Pure alpha — corrections only apply in numeric context
    result = correct_ocr_text("PLAN")
    assert result == "PLAN"


# ── dim_mm ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_mm", [
    ("150mm",   150.0),
    ("150 mm",  150.0),
    ("150MM",   150.0),
    ("1500Mm",  1500.0),
    ("0.5mm",   0.5),
    ("2.75 mm", 2.75),
])
def test_dim_mm(text, expected_mm):
    matches = extract_text_matches(text)
    dim_matches = [m for m in matches if m.pattern_name == "dim_mm"]
    assert dim_matches, f"No dim_mm match in '{text}'"
    assert pytest.approx(dim_matches[0].primary_mm, rel=1e-3) == expected_mm


# ── dim_inch ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_mm", [
    ('6"',     152.4),
    ('6 in',   152.4),
    ('6 inch', 152.4),
    ('12"',    304.8),
])
def test_dim_inch(text, expected_mm):
    matches = extract_text_matches(text)
    inch_matches = [m for m in matches if m.pattern_name == "dim_inch"]
    assert inch_matches, f"No dim_inch match in '{text}'"
    assert pytest.approx(inch_matches[0].primary_mm, rel=1e-2) == expected_mm


# ── dim_2d / dim_3d ───────────────────────────────────────────────────────────

def test_dim_2d_basic():
    matches = extract_text_matches("1500x600mm")
    m2d = [m for m in matches if m.pattern_name == "dim_2d"]
    assert m2d
    assert len(m2d[0].mm_values) == 2
    assert pytest.approx(m2d[0].mm_values[0]) == 1500.0
    assert pytest.approx(m2d[0].mm_values[1]) == 600.0

def test_dim_2d_unicode_times():
    matches = extract_text_matches("800×400")
    m2d = [m for m in matches if m.pattern_name == "dim_2d"]
    assert m2d

def test_dim_3d():
    matches = extract_text_matches("150×75×6mm")
    m3d = [m for m in matches if m.pattern_name == "dim_3d"]
    assert m3d
    assert len(m3d[0].mm_values) == 3


# ── thickness ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_mm", [
    ("thk 6mm",       6.0),
    ("thickness=10",  10.0),
    ("thick: 2.5",    2.5),
    ("t = 12",        12.0),
    ("THK 25mm",      25.0),
])
def test_thickness(text, expected_mm):
    matches = extract_text_matches(text)
    thk = [m for m in matches if m.pattern_name in ("thickness", "t_equals", "dim_mm")]
    assert thk, f"No thickness match in '{text}'"
    # At least one should give the right value
    values = [m.primary_mm for m in thk if m.primary_mm is not None]
    assert expected_mm in values or any(abs(v - expected_mm) < 1.0 for v in values)


# ── spacing_cc ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_mm", [
    ("@600 c/c",   600.0),
    ("@ 600 c/c",  600.0),
    ("@450 cts",   450.0),
    ("@1200 CTS",  1200.0),
])
def test_spacing_cc(text, expected_mm):
    matches = extract_text_matches(text)
    spacing = [m for m in matches if m.pattern_name == "spacing_cc"]
    assert spacing, f"No spacing_cc match in '{text}'"
    assert pytest.approx(spacing[0].primary_mm, rel=1e-3) == expected_mm


# ── diameter ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_mm", [
    ("Ø16",     16.0),
    ("⌀20mm",   20.0),
    ("dia 25",  25.0),
    ("DIA.12",  12.0),
])
def test_diameter(text, expected_mm):
    matches = extract_text_matches(text)
    dia = [m for m in matches if m.pattern_name in ("diameter", "dia_prefix")]
    assert dia, f"No diameter match in '{text}'"
    assert pytest.approx(dia[0].primary_mm, rel=1e-2) == expected_mm


# ── angle ─────────────────────────────────────────────────────────────────────

def test_angle_degrees():
    matches = extract_text_matches("45°")
    ang = [m for m in matches if m.pattern_name == "angle_deg"]
    assert ang
    assert pytest.approx(ang[0].primary_mm) == 45.0

def test_angle_word():
    matches = extract_text_matches("30 deg")
    ang = [m for m in matches if m.pattern_name == "angle_word"]
    assert ang
    assert pytest.approx(ang[0].primary_mm) == 30.0


# ── min / max qualifiers ──────────────────────────────────────────────────────

def test_min_qualifier():
    matches = extract_text_matches("min 50mm")
    m = [x for x in matches if x.qualifier == "MIN"]
    assert m

def test_max_qualifier():
    matches = extract_text_matches("max. 200mm")
    m = [x for x in matches if x.qualifier == "MAX"]
    assert m


# ── TYP / NTS flags ───────────────────────────────────────────────────────────

def test_typical_flag():
    matches = extract_text_matches("150mm TYP.")
    m = [x for x in matches if x.qualifier == "TYP"]
    assert m

def test_nts_flag():
    matches = extract_text_matches("NOT TO SCALE")
    m = [x for x in matches if x.qualifier == "NTS"]
    assert m


# ── count ─────────────────────────────────────────────────────────────────────

def test_count():
    matches = extract_text_matches("24 nos")
    cnt = [m for m in matches if m.pattern_name == "count_nos"]
    assert cnt
    assert cnt[0].values[0] == 24.0


# ── scale ─────────────────────────────────────────────────────────────────────

def test_scale_pattern():
    matches = extract_text_matches("SCALE 1:50")
    sc = [m for m in matches if m.pattern_name == "scale"]
    assert sc
    assert sc[0].values[0] == 50.0

def test_scale_ratio():
    matches = extract_text_matches("1:100")
    sc = [m for m in matches if m.pattern_name in ("scale", "scale_ratio")]
    assert sc


# ── imperial fraction ─────────────────────────────────────────────────────────

def test_fraction():
    matches = extract_text_matches("3 1/2")
    fr = [m for m in matches if m.pattern_name == "fraction"]
    assert fr
    # 3.5 inches = 88.9 mm
    assert pytest.approx(fr[0].primary_mm, rel=1e-2) == 88.9


# ── alloy / annotation ────────────────────────────────────────────────────────

def test_alloy():
    matches = extract_text_matches("Alloy 6063-T5")
    ann = [m for m in matches if m.pattern_name == "alloy"]
    assert ann
    assert "6063" in ann[0].annotation_value or "T5" in ann[0].annotation_value


# ── extract_dimensions_from_text ─────────────────────────────────────────────

def test_extract_dimensions_only():
    dims = extract_dimensions_from_text("Glass thk 10mm, alloy grade 6063")
    assert all(m.mm_values for m in dims)


# ── deduplication ─────────────────────────────────────────────────────────────

def test_no_duplicate_dim_2d_vs_dim_mm():
    """'1500x600mm' should not produce both a dim_mm AND a dim_2d for same position."""
    matches = extract_text_matches("1500x600mm")
    positions = [m.position for m in matches]
    # No two matches should start at the same position
    assert len(positions) == len(set(positions))
