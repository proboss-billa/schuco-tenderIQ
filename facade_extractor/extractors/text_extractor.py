"""
text_extractor.py
─────────────────
Universal regex-based text parser.

Works on ANY text source:
  • DXF TEXT / MTEXT plain content
  • pdfplumber extracted strings
  • pytesseract OCR output

Returns a list of TextMatch objects, each containing:
  - pattern_name  : which regex fired
  - raw_text      : the matched substring
  - value(s)      : parsed numeric(s) as float
  - unit          : canonical unit string
  - mm_value      : primary value converted to mm (None for annotation types)
  - position      : character offset in source string (for proximity logic)
  - qualifier     : "MIN" | "MAX" | "TYP" | "NTS" | None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from matchers.unit_normaliser import (
    normalise_to_mm,
    normalise_unit_string,
    imperial_fraction_to_mm,
    insunits_to_mm_factor,
)

# ─────────────────────────────────────────────────────────────────────────────
# 1.  MTEXT formatting-code stripper
# ─────────────────────────────────────────────────────────────────────────────

_MTEXT_CODES = re.compile(
    r"""
    \\[AaBbCcFfHhIiLlNnOoPpQqSsTtWw][^;]*;  # \A1; \H2.5; \W1.2; etc.
    |\\[UuOoLl]                              # \U  \O  toggles
    |\\~                                     # non-breaking space
    |\\P                                     # paragraph break
    |\{\}                                    # empty group
    """,
    re.VERBOSE,
)

# Bare braces stripped separately so nested groups are handled correctly
_BARE_BRACES = re.compile(r"[{}]")

_UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def strip_mtext_codes(raw: str) -> str:
    """Remove AutoCAD MTEXT formatting codes and return plain text."""
    # Replace \\U+XXXX unicode escapes first
    def _unicode_sub(m: re.Match) -> str:
        return chr(int(m.group(1), 16))

    text = _UNICODE_ESCAPE.sub(_unicode_sub, raw)
    text = _MTEXT_CODES.sub(" ", text)
    text = _BARE_BRACES.sub("", text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 2.  PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

_PATTERNS: dict[str, str] = {
    # ── 3-D compound first (before 2-D swallows it) ───────────────────────
    "dim_3d": (
        r"(?P<v1>\d+(?:\.\d+)?)\s*[xX×]\s*"
        r"(?P<v2>\d+(?:\.\d+)?)\s*[xX×]\s*"
        r"(?P<v3>\d+(?:\.\d+)?)"
        r"(?:\s*(?P<unit3>mm|cm|m|in|inch|\"|ft|'|yd))?(?:\b|$)"
    ),
    # ── 2-D compound ──────────────────────────────────────────────────────
    "dim_2d": (
        r"(?P<v1>\d+(?:\.\d+)?)\s*[xX×\*]\s*(?P<v2>\d+(?:\.\d+)?)"
        r"(?:\s*(?P<unit2>mm|cm|m|in|inch|\"|ft|'|yd))?(?:\b|$)"
    ),
    # ── Single mm dimension ───────────────────────────────────────────────
    "dim_mm": r"(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|MM|Mm)(?:\b|$)",
    # ── Single metre dimension (not mm) ──────────────────────────────────
    "dim_m": r"(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>[mM])(?!m)(?:\b|$)",
    # ── Inches / feet ─────────────────────────────────────────────────────
    "dim_inch": r'(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>"|in(?:ch(?:es)?)?)(?:\b|$)',
    "dim_feet": r"(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>ft|feet|')(?:\b|$)",
    # ── Thickness callouts ────────────────────────────────────────────────
    "thickness": (
        r"(?:thk|thkns|thick|thickness)\.?\s*[=:\-]?\s*"
        r"(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|inch|\"|ft|'|yd)?"
    ),
    "t_equals": r"\bt\s*=\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m)?",
    # ── Spacing / c/c ────────────────────────────────────────────────────
    "spacing_cc": (
        r"@\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|inch|\")?"
        r"\s*(?:c/?c|C/?C|cts|CTS|ctrs|CTRS)"
    ),
    "spacing_at": (
        r"@\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|inch|\")?"
    ),
    # ── Diameter ──────────────────────────────────────────────────────────
    "diameter": (
        r"[ØøΦφ⌀]\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    "dia_prefix": (
        r"(?:dia|DIA|Dia)\.?\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    # ── Depth / height / width keywords ──────────────────────────────────
    "depth": (
        r"(?:depth|dp)\s*[=:\-]\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    "height": (
        r"(?:height|ht|hgt)\s*[=:\-]\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    "width": (
        r"(?:width|wid|wd)\s*[=:\-]\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    # ── Min / max qualifiers ──────────────────────────────────────────────
    "min_dim": (
        r"(?:min\.?|minimum)\s*[=:\-]?\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    "max_dim": (
        r"(?:max\.?|maximum)\s*[=:\-]?\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m|in|\")?"
    ),
    # ── Imperial fractions ────────────────────────────────────────────────
    "fraction": r"(?P<whole>\d+)\s+(?P<num>\d+)/(?P<den>\d+)",
    # ── Material / alloy annotation ───────────────────────────────────────
    "alloy": r"(?:alloy|grade|temper|series)\s*[=:\-]?\s*(?P<val>[0-9A-Z][0-9A-Z\-T]{1,10})",
    # ── Material finish annotation ────────────────────────────────────────
    "finish": r"(?:finish|coating|surface|anodis(?:e|ing)|powder(?:\s*coat)?)\s*[=:\-]?\s*(?P<val>[A-Z0-9][^\n,;]{0,30})",
    # ── Count ─────────────────────────────────────────────────────────────
    "count_nos": r"(?P<v1>\d+)\s*(?:nos?|NOS?|pcs?|PCS?|units?|no\.?)",
    # ── Scale ─────────────────────────────────────────────────────────────
    "scale": r"(?:scale|SCALE)\s*[=:\-]?\s*1\s*[:/]\s*(?P<v1>\d+)",
    "scale_ratio": r"\b1\s*:\s*(?P<v1>\d+)\b",
    # ── Angular ───────────────────────────────────────────────────────────
    "angle_deg": r"(?P<v1>\d+(?:\.\d+)?)\s*°",
    "angle_word": r"(?P<v1>\d+(?:\.\d+)?)\s*(?:deg|DEG|degrees?)",
    # ── Typical / NTS qualifiers ──────────────────────────────────────────
    "typical": r"\b(?:typ(?:ical)?\.?)\b",
    "nts": r"\b(?:NTS|not\s+to\s+scale)\b",
}

# Pre-compile all patterns (case-insensitive, dotall off)
_COMPILED: dict[str, re.Pattern] = {
    name: re.compile(pat, re.IGNORECASE)
    for name, pat in _PATTERNS.items()
}

# Patterns that are annotation-only (no numeric mm conversion)
_ANNOTATION_PATTERNS = {"alloy", "finish", "typical", "nts"}


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TextMatch:
    pattern_name: str
    raw_text: str           # matched substring
    values: list[float]     # parsed numeric group(s) as floats
    unit: str               # canonical unit ('' for annotation)
    mm_values: list[float]  # values converted to mm (empty for annotation)
    position: int           # char offset in source string
    qualifier: Optional[str] = None   # MIN | MAX | TYP | NTS | None
    annotation_value: Optional[str] = None  # for alloy / finish patterns

    # convenience
    @property
    def primary_mm(self) -> Optional[float]:
        return self.mm_values[0] if self.mm_values else None


# ─────────────────────────────────────────────────────────────────────────────
# 4.  OCR correction rules
# ─────────────────────────────────────────────────────────────────────────────

_OCR_CORRECTIONS = {
    # Character-level substitutions in numeric context
    "O": "0", "o": "0",
    "I": "1", "l": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
}

_OCR_CORRECTION_RE = re.compile(r"(?<=[0-9])[OoIlSBZG]|[OoIlSBZG](?=[0-9])")


def correct_ocr_text(raw: str) -> str:
    """Apply common OCR misread corrections in a numeric context."""
    def _fix(m: re.Match) -> str:
        ch = m.group(0)
        return _OCR_CORRECTIONS.get(ch, ch)
    return _OCR_CORRECTION_RE.sub(_fix, raw)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Core extraction function
# ─────────────────────────────────────────────────────────────────────────────

def _infer_unit_from_context(text: str, position: int, window: int = 80) -> str:
    """
    When no explicit unit is found in a match, search nearby text for a unit.
    Returns empty string if nothing found.
    """
    snippet = text[max(0, position - window): position + window].lower()
    for unit in ("mm", "cm", " m ", "in", "ft"):
        if unit in snippet:
            return unit.strip()
    return ""


def extract_text_matches(
    source: str,
    is_ocr: bool = False,
    source_origin: str = "",
) -> list[TextMatch]:
    """
    Run the full regex suite on *source* text and return all TextMatch objects.

    Parameters
    ----------
    source       : raw text string (MTEXT content, PDF text, OCR output)
    is_ocr       : if True, apply OCR correction rules before parsing
    source_origin: optional label for debugging ("MTEXT", "PDF", "OCR")

    Returns
    -------
    List of TextMatch instances, one per regex hit.
    """
    # Pre-process
    text = strip_mtext_codes(source)
    if is_ocr:
        text = correct_ocr_text(text)

    results: list[TextMatch] = []

    # ── Qualifier flags ────────────────────────────────────────────────────
    qualifier: Optional[str] = None
    if _COMPILED["typical"].search(text):
        qualifier = "TYP"
    if _COMPILED["nts"].search(text):
        # NTS overrides other qualifiers and also needs a match record
        qualifier = "NTS"
        results.append(TextMatch(
            pattern_name="nts",
            raw_text=_COMPILED["nts"].search(text).group(0),
            values=[],
            unit="",
            mm_values=[],
            position=_COMPILED["nts"].search(text).start(),
            qualifier="NTS",
        ))
    if qualifier is None and _COMPILED["min_dim"].search(text):
        qualifier = "MIN"
    if qualifier is None and _COMPILED["max_dim"].search(text):
        qualifier = "MAX"

    # ── Main scan ─────────────────────────────────────────────────────────
    for name, pattern in _COMPILED.items():
        if name in ("typical", "nts"):
            # Already captured as qualifier, skip as standalone
            continue

        for m in pattern.finditer(text):
            match_str = m.group(0)
            pos = m.start()

            # ── Annotation patterns ────────────────────────────────────
            if name in _ANNOTATION_PATTERNS:
                ann_val = m.group("val") if "val" in m.groupdict() else match_str
                results.append(TextMatch(
                    pattern_name=name,
                    raw_text=match_str,
                    values=[],
                    unit="",
                    mm_values=[],
                    position=pos,
                    qualifier=qualifier,
                    annotation_value=ann_val.strip(),
                ))
                continue

            # ── Typical / NTS as qualifier-only ───────────────────────
            if name in ("min_dim", "max_dim"):
                q_local = "MIN" if name == "min_dim" else "MAX"
            else:
                q_local = qualifier

            # ── Compound 3D ───────────────────────────────────────────
            if name == "dim_3d":
                try:
                    vals = [float(m.group("v1")), float(m.group("v2")), float(m.group("v3"))]
                except (IndexError, TypeError, ValueError):
                    continue
                raw_unit = (m.group("unit3") or "").strip()
                if not raw_unit:
                    raw_unit = _infer_unit_from_context(text, pos)
                unit = normalise_unit_string(raw_unit) if raw_unit else "mm"
                try:
                    mm_vals = [normalise_to_mm(v, unit) for v in vals]
                except ValueError:
                    mm_vals = vals[:]
                results.append(TextMatch(
                    pattern_name=name,
                    raw_text=match_str,
                    values=vals,
                    unit=unit,
                    mm_values=mm_vals,
                    position=pos,
                    qualifier=q_local,
                ))
                continue

            # ── Compound 2D ───────────────────────────────────────────
            if name == "dim_2d":
                try:
                    vals = [float(m.group("v1")), float(m.group("v2"))]
                except (IndexError, TypeError, ValueError):
                    continue
                raw_unit = (m.group("unit2") or "").strip()
                if not raw_unit:
                    raw_unit = _infer_unit_from_context(text, pos)
                unit = normalise_unit_string(raw_unit) if raw_unit else "mm"
                try:
                    mm_vals = [normalise_to_mm(v, unit) for v in vals]
                except ValueError:
                    mm_vals = vals[:]
                results.append(TextMatch(
                    pattern_name=name,
                    raw_text=match_str,
                    values=vals,
                    unit=unit,
                    mm_values=mm_vals,
                    position=pos,
                    qualifier=q_local,
                ))
                continue

            # ── Imperial fraction ──────────────────────────────────────
            if name == "fraction":
                try:
                    whole = int(m.group("whole"))
                    num   = int(m.group("num"))
                    den   = int(m.group("den"))
                    if den == 0:
                        continue
                except (IndexError, TypeError, ValueError):
                    continue
                mm_val = imperial_fraction_to_mm(whole, num, den)
                results.append(TextMatch(
                    pattern_name=name,
                    raw_text=match_str,
                    values=[whole + num / den],
                    unit="in",
                    mm_values=[mm_val],
                    position=pos,
                    qualifier=q_local,
                ))
                continue

            # ── Angular ───────────────────────────────────────────────
            if name in ("angle_deg", "angle_word"):
                try:
                    v = float(m.group("v1"))
                except (IndexError, TypeError, ValueError):
                    continue
                results.append(TextMatch(
                    pattern_name=name,
                    raw_text=match_str,
                    values=[v],
                    unit="deg",
                    mm_values=[v],   # degrees stored as-is
                    position=pos,
                    qualifier=q_local,
                ))
                continue

            # ── Count / scale (unitless integer) ──────────────────────
            if name in ("count_nos", "scale", "scale_ratio"):
                try:
                    v = float(m.group("v1"))
                except (IndexError, TypeError, ValueError):
                    continue
                results.append(TextMatch(
                    pattern_name=name,
                    raw_text=match_str,
                    values=[v],
                    unit="",
                    mm_values=[],
                    position=pos,
                    qualifier=q_local,
                ))
                continue

            # ── All other patterns with v1 ─────────────────────────────
            try:
                v = float(m.group("v1"))
            except (IndexError, TypeError, ValueError):
                continue

            gd = m.groupdict()
            raw_unit = (gd.get("unit") or "").strip()
            if not raw_unit:
                raw_unit = _infer_unit_from_context(text, pos)
            unit = normalise_unit_string(raw_unit) if raw_unit else "mm"

            try:
                mm_val = normalise_to_mm(v, unit)
            except ValueError:
                mm_val = v  # best-effort

            results.append(TextMatch(
                pattern_name=name,
                raw_text=match_str,
                values=[v],
                unit=unit,
                mm_values=[mm_val],
                position=pos,
                qualifier=q_local,
            ))

    # Deduplicate overlapping matches (keep the longest / most-specific)
    results = _deduplicate(results)
    return results


def _deduplicate(matches: list[TextMatch]) -> list[TextMatch]:
    """
    Remove matches whose position range is completely contained within a
    longer match (e.g. '150' swallowed by '150x300mm').
    """
    # Sort by position, then by descending raw_text length
    matches.sort(key=lambda x: (x.position, -len(x.raw_text)))
    kept: list[TextMatch] = []
    last_end = -1
    for m in matches:
        start = m.position
        end = start + len(m.raw_text)
        if start >= last_end:
            kept.append(m)
            last_end = end
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Convenience wrapper
# ─────────────────────────────────────────────────────────────────────────────

def extract_dimensions_from_text(
    text: str,
    is_ocr: bool = False,
) -> list[TextMatch]:
    """
    Thin wrapper: strip MTEXT codes, run extract, return only matches that
    have at least one mm_value (i.e. real dimensional hits, not annotations).
    """
    all_matches = extract_text_matches(text, is_ocr=is_ocr)
    return [m for m in all_matches if m.mm_values]


def extract_annotations_from_text(text: str) -> list[TextMatch]:
    """Return only annotation-type matches (alloy, finish, typical, nts)."""
    all_matches = extract_text_matches(text)
    return [m for m in all_matches if m.pattern_name in _ANNOTATION_PATTERNS]
