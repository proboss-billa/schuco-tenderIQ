"""
unit_normaliser.py
──────────────────
Converts any length value from any recognised unit into millimetres.
Degrees pass through unchanged.

All external code should call:
    normalise_to_mm(value, unit_string) -> float
    normalise_unit_string(raw) -> canonical_unit_string
"""

from __future__ import annotations

import re
from typing import Optional

# ── Conversion factors → mm ────────────────────────────────────────────────────
_TO_MM: dict[str, float] = {
    # SI
    "mm":  1.0,
    "cm":  10.0,
    "dm":  100.0,
    "m":   1000.0,
    "km":  1_000_000.0,
    # Imperial / US
    "in":  25.4,
    "inch": 25.4,
    '"':   25.4,
    "ft":  304.8,
    "feet": 304.8,
    "'":   304.8,
    "yd":  914.4,
    "yard": 914.4,
    # Drawing-unit codes from DXF $INSUNITS
    # 0=Unitless,1=Inches,2=Feet,3=Miles,4=mm,5=cm,6=m,7=km,8=microinches,
    # 9=mils,10=yards,11=Angstroms,12=nanometres,13=microns,14=decimetres,
    # 15=decametres,16=hectometres,17=gigametres,18=AU,19=lightyears,20=parsecs
    "insunits_0":  1.0,      # unknown → assume mm
    "insunits_1":  25.4,
    "insunits_2":  304.8,
    "insunits_4":  1.0,
    "insunits_5":  10.0,
    "insunits_6":  1000.0,
    "insunits_7":  1_000_000.0,
    "insunits_10": 914.4,
    "insunits_13": 0.001,
    "insunits_14": 100.0,
}

# ── Canonical aliases ──────────────────────────────────────────────────────────
_ALIASES: dict[str, str] = {
    "millimeter":  "mm", "millimetre":  "mm", "millimeters": "mm",
    "centimeter":  "cm", "centimetre":  "cm", "centimeters": "cm",
    "meter":       "m",  "metre":       "m",  "meters":      "m",
    "kilometer":   "km", "kilometre":   "km",
    "inch":        "in", "inches":      "in",
    "foot":        "ft", "feet":        "ft",
    "yard":        "yd", "yards":       "yd",
    # MTEXT formatting artefacts
    "mm.":  "mm", "mm,": "mm",
    "m.":   "m",  "m,":  "m",
}

# ── Regex to detect unit suffix in a raw text string ─────────────────────────
_UNIT_PATTERN = re.compile(
    r"""
    (?ix)
    (?P<value>-?\d+(?:[.,]\d+)?)   # numeric part (optional sign)
    \s*
    (?P<unit>
        mm|cm|dm|m(?!m)|km         # SI — m must not be followed by another m
        |in(?:ch(?:es)?)?          # imperial
        |ft|feet|foot
        |yd|yard(?:s)?
        |"                         # double-quote = inch
        |'                         # single-quote = foot
    )
    \b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def normalise_unit_string(raw: str) -> str:
    """Return the canonical lowercase unit token for a raw unit string."""
    cleaned = raw.strip().lower().rstrip(".,;:")
    return _ALIASES.get(cleaned, cleaned)


def normalise_to_mm(value: float, unit: str) -> float:
    """
    Convert *value* expressed in *unit* to millimetres.

    Parameters
    ----------
    value : float
        Numeric measurement.
    unit : str
        Any recognised unit string (case-insensitive).
        Pass "deg" or "°" to get the value back unchanged (degrees).

    Returns
    -------
    float
        Value in mm (or unchanged degrees).

    Raises
    ------
    ValueError
        If unit is not recognised.
    """
    if unit in ("deg", "°", "degree", "degrees"):
        return float(value)

    canonical = normalise_unit_string(unit)
    factor = _TO_MM.get(canonical)
    if factor is None:
        raise ValueError(
            f"Unknown unit '{unit}' (canonical: '{canonical}'). "
            "Add it to _TO_MM in unit_normaliser.py."
        )
    return float(value) * factor


def insunits_to_mm_factor(insunits_code: int) -> float:
    """
    Return the mm-per-unit factor for a DXF $INSUNITS integer code.
    Unknown codes fall back to 1.0 (assume already in mm).
    """
    key = f"insunits_{insunits_code}"
    return _TO_MM.get(key, 1.0)


def parse_value_with_unit(text: str) -> Optional[tuple[float, str]]:
    """
    Attempt to extract a (value_in_mm, canonical_unit) pair from a raw string.
    Returns None if no numeric+unit pattern is found.

    Examples
    --------
    >>> parse_value_with_unit("  150mm ")
    (150.0, 'mm')
    >>> parse_value_with_unit("6.35 in")
    (161.29, 'in')
    >>> parse_value_with_unit("no number here")
    None
    """
    m = _UNIT_PATTERN.search(text)
    if not m:
        return None
    raw_val = m.group("value").replace(",", ".")
    raw_unit = m.group("unit")
    canonical = normalise_unit_string(raw_unit)
    try:
        mm_value = normalise_to_mm(float(raw_val), canonical)
        return mm_value, canonical
    except ValueError:
        return None


def convert_mm_to_unit(mm_value: float, target_unit: str) -> float:
    """
    Convert a mm value back to *target_unit* (useful for display).
    """
    canonical = normalise_unit_string(target_unit)
    factor = _TO_MM.get(canonical)
    if factor is None or factor == 0:
        raise ValueError(f"Unknown or zero-factor unit: '{target_unit}'")
    return mm_value / factor


# ── Imperial fraction helper ──────────────────────────────────────────────────

def fraction_to_decimal_inches(whole: int, numerator: int, denominator: int) -> float:
    """Convert an imperial fraction (e.g. 3 1/4) to decimal inches."""
    if denominator == 0:
        raise ValueError("Denominator cannot be zero.")
    return whole + numerator / denominator


def imperial_fraction_to_mm(whole: int, numerator: int, denominator: int) -> float:
    """Convert an imperial fraction to mm."""
    decimal_inches = fraction_to_decimal_inches(whole, numerator, denominator)
    return normalise_to_mm(decimal_inches, "in")
