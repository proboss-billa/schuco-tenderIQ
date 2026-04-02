"""
scale_extractor.py
──────────────────
Multi-method drawing scale detection.  Tries each method in priority order
and returns the first successful result.

Methods (in order):
  1. DXF HEADER  $DIMSCALE
  2. Title-block text regex  "1:50", "SCALE 1:20", "1 : 100"
  3. Empirical — measured DIMENSION geometry length vs annotated value
  4. Empirical — user-supplied reference dimension (e.g. known floor height)
  5. Fallback  → UNKNOWN

Returns a ScaleResult dataclass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from matchers.unit_normaliser import insunits_to_mm_factor

# ─────────────────────────────────────────────────────────────────────────────
# Result model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScaleResult:
    """
    Encapsulates scale information for one drawing sheet.

    scale_denominator : int  — the X in "1:X".  1 = full scale (1:1).
    source            : str  — HEADER | TITLEBLOCK | EMPIRICAL | REFERENCE | UNKNOWN
    confidence        : float 0-1
    drawing_unit      : str  — "mm" | "m" | "in" | "ft" (native DXF unit)
    mm_per_unit       : float — how many mm one drawing unit represents
                               (= insunits_factor / scale_denominator)
    nts               : bool — True if annotated "NTS" or "Not to Scale"
    raw_text          : str  — the raw string that was parsed (if applicable)
    """
    scale_denominator: int = 1
    source: str = "UNKNOWN"
    confidence: float = 0.0
    drawing_unit: str = "mm"
    mm_per_unit: float = 1.0
    nts: bool = False
    raw_text: str = ""

    @property
    def scale_string(self) -> str:
        if self.nts:
            return "NTS"
        return f"1:{self.scale_denominator}"

    @property
    def is_known(self) -> bool:
        return self.source != "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns
# ─────────────────────────────────────────────────────────────────────────────

_SCALE_PATTERNS = [
    # "SCALE 1:50", "Scale: 1/50", "1 : 100"
    re.compile(
        r"(?:scale\s*[=:\-]?\s*)?1\s*[:\/]\s*(?P<denom>\d{1,6})",
        re.IGNORECASE,
    ),
    # "1=50" style (older drawings)
    re.compile(
        r"1\s*=\s*(?P<denom>\d{1,6})",
        re.IGNORECASE,
    ),
]

_NTS_PATTERN = re.compile(
    r"\bNTS\b|not\s+to\s+scale",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Method 1: DXF HEADER
# ─────────────────────────────────────────────────────────────────────────────

def from_dxf_header(
    insunits: int,
    dimscale: float,
    ltscale: float,
) -> Optional[ScaleResult]:
    """
    Derive scale from DXF HEADER variables.

    Parameters
    ----------
    insunits : $INSUNITS integer code
    dimscale : $DIMSCALE  (dimension scale factor, not drawing scale)
    ltscale  : $LTSCALE   (linetype scale, secondary indicator)

    DXF stores model-space geometry at real-world size (1:1).
    $DIMSCALE controls dimension annotation size, not drawing scale.
    → We can only infer the unit from $INSUNITS here.
    → True drawing scale is a viewport/layout attribute not in HEADER.

    Returns a ScaleResult with source="HEADER", scale_denominator=1 (full
    model-space), and the correct mm_per_unit conversion factor.
    """
    factor = insunits_to_mm_factor(insunits)
    unit_map = {
        0: "mm", 1: "in", 2: "ft", 4: "mm", 5: "cm",
        6: "m", 7: "km", 10: "yd", 13: "micron", 14: "dm",
    }
    unit = unit_map.get(insunits, "mm")

    # dimscale == 0 means "follow PSLTSCALE" — treat as 1
    effective_dimscale = dimscale if dimscale and dimscale != 0 else 1.0

    return ScaleResult(
        scale_denominator=1,
        source="HEADER",
        confidence=0.70,
        drawing_unit=unit,
        mm_per_unit=factor,
        nts=False,
        raw_text=f"$INSUNITS={insunits} $DIMSCALE={dimscale}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Method 2: Title-block text regex
# ─────────────────────────────────────────────────────────────────────────────

def from_text(
    text_blocks: list[str],
    drawing_unit: str = "mm",
) -> Optional[ScaleResult]:
    """
    Search a list of text strings (title-block region preferred) for a scale
    annotation.

    Returns the first matching ScaleResult, or None.
    """
    for text in text_blocks:
        # NTS check first
        if _NTS_PATTERN.search(text):
            return ScaleResult(
                scale_denominator=1,
                source="TITLEBLOCK",
                confidence=0.85,
                drawing_unit=drawing_unit,
                mm_per_unit=1.0,
                nts=True,
                raw_text=text.strip()[:80],
            )

        for pat in _SCALE_PATTERNS:
            m = pat.search(text)
            if m:
                denom = int(m.group("denom"))
                if denom == 0:
                    continue
                factor = insunits_to_mm_factor(
                    {"mm": 4, "m": 6, "in": 1, "ft": 2}.get(drawing_unit, 4)
                )
                return ScaleResult(
                    scale_denominator=denom,
                    source="TITLEBLOCK",
                    confidence=0.90,
                    drawing_unit=drawing_unit,
                    mm_per_unit=factor / denom,
                    nts=False,
                    raw_text=m.group(0).strip()[:80],
                )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Method 3: Empirical — DIMENSION entity geometry vs text value
# ─────────────────────────────────────────────────────────────────────────────

def from_dimension_entity(
    geometry_length_drawing_units: float,
    annotated_value_mm: float,
    drawing_unit: str = "mm",
    insunits: int = 4,
) -> Optional[ScaleResult]:
    """
    Compute empirical scale from one DIMENSION entity.

    geometry_length_drawing_units: raw geometric length in DXF units
    annotated_value_mm           : what the dimension text says (in mm)

    Returns None if lengths are degenerate.
    """
    if geometry_length_drawing_units <= 0 or annotated_value_mm <= 0:
        return None

    unit_factor = insunits_to_mm_factor(insunits)
    real_length_mm = geometry_length_drawing_units * unit_factor

    # scale = annotated / real_mm_in_modelspace
    # For a 1:50 drawing, 1 mm on paper = 50 mm in reality.
    # In DXF model space, geometry IS at real-world size → scale_denom ≈ 1.
    # So this method is most useful for PDF/raster where geometry is at
    # paper size.
    if real_length_mm == 0:
        return None

    empirical_ratio = annotated_value_mm / real_length_mm

    # Round to nearest standard scale denominator
    std_denoms = [
        1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000
    ]
    closest = min(std_denoms, key=lambda d: abs(d - empirical_ratio))
    deviation = abs(empirical_ratio - closest) / closest if closest else 1.0

    confidence = 0.80 if deviation < 0.05 else 0.60

    return ScaleResult(
        scale_denominator=closest,
        source="EMPIRICAL",
        confidence=confidence,
        drawing_unit=drawing_unit,
        mm_per_unit=unit_factor / closest,
        nts=False,
        raw_text=f"geometry={geometry_length_drawing_units:.2f} annotated={annotated_value_mm:.2f}mm",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Method 4: User-supplied reference dimension
# ─────────────────────────────────────────────────────────────────────────────

def from_reference_dimension(
    geometry_length_drawing_units: float,
    known_real_value_mm: float,
    drawing_unit: str = "mm",
    insunits: int = 4,
) -> Optional[ScaleResult]:
    """
    Compute scale from a user-supplied known dimension.
    e.g. "I know the floor-to-floor is 3600 mm, here is the geometric length."
    """
    return from_dimension_entity(
        geometry_length_drawing_units=geometry_length_drawing_units,
        annotated_value_mm=known_real_value_mm,
        drawing_unit=drawing_unit,
        insunits=insunits,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Method 5: Fallback
# ─────────────────────────────────────────────────────────────────────────────

def unknown_scale(drawing_unit: str = "mm") -> ScaleResult:
    """Return a fallback UNKNOWN scale result."""
    return ScaleResult(
        scale_denominator=1,
        source="UNKNOWN",
        confidence=0.0,
        drawing_unit=drawing_unit,
        mm_per_unit=1.0,
        nts=False,
        raw_text="",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def detect_scale(
    text_blocks: list[str] | None = None,
    insunits: int = 4,
    dimscale: float = 1.0,
    ltscale: float = 1.0,
    dimension_geometry_length: float | None = None,
    dimension_annotated_mm: float | None = None,
    reference_geometry_length: float | None = None,
    reference_known_mm: float | None = None,
    drawing_unit: str = "mm",
) -> ScaleResult:
    """
    Try all five scale-detection methods in priority order.

    Parameters
    ----------
    text_blocks               : candidate title-block text lines
    insunits                  : DXF $INSUNITS
    dimscale                  : DXF $DIMSCALE
    ltscale                   : DXF $LTSCALE
    dimension_geometry_length : geometric length (DXF units) of a DIMENSION entity
    dimension_annotated_mm    : what that DIMENSION entity text says (mm)
    reference_geometry_length : geometric length of a known reference feature
    reference_known_mm        : real-world size of that feature (mm)
    drawing_unit              : "mm" | "m" | "in" | "ft"

    Returns
    -------
    ScaleResult (guaranteed — falls back to UNKNOWN)
    """
    # Method 1 — HEADER
    header_result = from_dxf_header(insunits, dimscale, ltscale)

    # Method 2 — Title block text
    if text_blocks:
        tb_result = from_text(text_blocks, drawing_unit=drawing_unit)
        if tb_result:
            return tb_result

    # Method 3 — Empirical from DIMENSION entity
    if dimension_geometry_length and dimension_annotated_mm:
        emp_result = from_dimension_entity(
            dimension_geometry_length,
            dimension_annotated_mm,
            drawing_unit=drawing_unit,
            insunits=insunits,
        )
        if emp_result:
            return emp_result

    # Method 4 — User reference dimension
    if reference_geometry_length and reference_known_mm:
        ref_result = from_reference_dimension(
            reference_geometry_length,
            reference_known_mm,
            drawing_unit=drawing_unit,
            insunits=insunits,
        )
        if ref_result:
            return ref_result

    # Use HEADER result if available (unit info only, scale_denom=1)
    if header_result:
        return header_result

    # Method 5 — Fallback
    return unknown_scale(drawing_unit)


# ─────────────────────────────────────────────────────────────────────────────
# Validation helper
# ─────────────────────────────────────────────────────────────────────────────

def validate_scale_against_dimension(
    scale_result: ScaleResult,
    geometry_length_drawing_units: float,
    annotated_value_mm: float,
    tolerance_pct: float = 0.05,
) -> tuple[bool, float]:
    """
    Check whether a computed scale is consistent with a DIMENSION entity.

    Returns (is_consistent, deviation_fraction).
    """
    if geometry_length_drawing_units <= 0:
        return False, 1.0

    unit_factor = insunits_to_mm_factor(
        {"mm": 4, "m": 6, "in": 1, "ft": 2}.get(scale_result.drawing_unit, 4)
    )
    predicted_mm = geometry_length_drawing_units * scale_result.mm_per_unit * unit_factor
    if predicted_mm == 0:
        return False, 1.0

    deviation = abs(predicted_mm - annotated_value_mm) / annotated_value_mm
    return deviation <= tolerance_pct, deviation
