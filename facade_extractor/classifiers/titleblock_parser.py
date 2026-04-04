"""
titleblock_parser.py
────────────────────
Extracts standard metadata fields from a drawing title block.

Works on ANY source:
  • DXF attribute text (from ATTDEF / ATTRIB entities inside title-block INSERT)
  • PDF text objects (bottom-right region of the page)
  • Raw list of strings (pdfplumber, OCR output)

No project-specific field names are hardcoded.
The field discovery is regex-pattern based.

Returns a TitleBlockData dataclass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from classifiers.scale_extractor import from_text as scale_from_text, ScaleResult

# ─────────────────────────────────────────────────────────────────────────────
# Field patterns — ordered from most to least specific
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_PATTERNS: dict[str, list[str]] = {
    "sheet_number": [
        r"(?:dwg|drg|drawing|sheet|doc)[\s\.\-#]*(?:no\.?|number|num\.?|#)\s*[=:\-]?\s*(?P<val>[A-Z0-9\-\/\.]{2,20})",
        r"^(?P<val>[A-Z]{1,4}[\-\.]\d{3,5}(?:[\-\.]\w{1,6})?)\s*$",  # standalone code
    ],
    "sheet_title": [
        r"(?:title|drawing title|sheet title)\s*[=:\-]?\s*(?P<val>[^\n]{5,80})",
    ],
    "revision": [
        r"(?:rev(?:ision)?\.?|rev\.?\s*#)\s*[=:\-]?\s*(?P<val>[A-Z0-9]{1,4})\b",
    ],
    "date": [
        r"(?:date|issued|drawn)\s*[=:\-]?\s*(?P<val>\d{1,2}[\-/\.]\d{1,2}[\-/\.]\d{2,4})",
        r"(?:date|issued|drawn)\s*[=:\-]?\s*(?P<val>\d{4}[\-/\.]\d{2}[\-/\.]\d{2})",
    ],
    "drawn_by": [
        r"(?:drawn|drn|prepared)\s*(?:by)?\s*[=:\-]?\s*(?P<val>[A-Z][A-Za-z\s\.]{2,30})",
    ],
    "checked_by": [
        r"(?:checked|chkd|chk|approved|appr)\s*(?:by)?\s*[=:\-]?\s*(?P<val>[A-Z][A-Za-z\s\.]{2,30})",
    ],
    "project_title": [
        r"(?:project|proj\.?)\s*[=:\-]?\s*(?P<val>[^\n]{5,80})",
    ],
    "client": [
        r"(?:client|owner|employer)\s*[=:\-]?\s*(?P<val>[^\n]{3,60})",
    ],
    "scale": [
        r"(?:scale|scl)\s*[=:\-]?\s*(?P<val>(?:NTS|1\s*[:/]\s*\d{1,6}|not\s+to\s+scale))",
    ],
    "north_point": [
        r"\b(?P<val>N(?:orth)?)\b",
    ],
    "status": [
        r"(?:status|purpose|stage)\s*[=:\-]?\s*(?P<val>[A-Za-z\s\-]{3,40})",
    ],
}

_COMPILED_FIELDS: dict[str, list[re.Pattern]] = {
    field_name: [re.compile(pat, re.IGNORECASE | re.MULTILINE) for pat in pats]
    for field_name, pats in _FIELD_PATTERNS.items()
}


# ─────────────────────────────────────────────────────────────────────────────
# DXF ATTRIB tag → field mapping (common conventions — not hardcoded per project)
# ─────────────────────────────────────────────────────────────────────────────

_ATTRIB_TAG_MAP: dict[str, str] = {
    # Key = lowercased ATTRIB tag; Value = our field name
    "drgno":     "sheet_number",
    "dwgno":     "sheet_number",
    "sheetno":   "sheet_number",
    "drwnumber": "sheet_number",
    "drawingnumber": "sheet_number",
    "title":     "sheet_title",
    "drawingtitle": "sheet_title",
    "sheettitle": "sheet_title",
    "rev":       "revision",
    "revision":  "revision",
    "revno":     "revision",
    "date":      "date",
    "issuedate": "date",
    "drawn":     "drawn_by",
    "drawnby":   "drawn_by",
    "checked":   "checked_by",
    "checkedby": "checked_by",
    "approved":  "checked_by",
    "project":   "project_title",
    "projectname": "project_title",
    "client":    "client",
    "scale":     "scale",
    "status":    "status",
    "stage":     "status",
}


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TitleBlockData:
    sheet_number: str = ""
    sheet_title: str = ""
    revision: str = ""
    date: str = ""
    drawn_by: str = ""
    checked_by: str = ""
    project_title: str = ""
    client: str = ""
    scale_raw: str = ""
    scale_result: Optional[ScaleResult] = None
    status: str = ""
    raw_fields: dict[str, str] = field(default_factory=dict)   # anything else found
    parse_source: str = ""    # "DXF_ATTRIB" | "TEXT_REGEX" | "MIXED"

    def to_dict(self) -> dict:
        return {
            "sheet_number":  self.sheet_number,
            "sheet_title":   self.sheet_title,
            "revision":      self.revision,
            "date":          self.date,
            "drawn_by":      self.drawn_by,
            "checked_by":    self.checked_by,
            "project_title": self.project_title,
            "client":        self.client,
            "scale":         self.scale_raw,
            "status":        self.status,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Parser — DXF ATTRIB entities
# ─────────────────────────────────────────────────────────────────────────────

def parse_from_dxf_attribs(
    attribs: list[dict[str, str]],
) -> TitleBlockData:
    """
    Parse title-block data from a list of DXF ATTRIB entity dicts.

    Each dict should have:
        {"tag": "DRGNO", "text": "FA-001-A"}

    Returns TitleBlockData.
    """
    data = TitleBlockData(parse_source="DXF_ATTRIB")
    for attrib in attribs:
        tag = attrib.get("tag", "").lower().replace(" ", "").replace("_", "")
        text = (attrib.get("text") or "").strip()
        if not text:
            continue

        field_name = _ATTRIB_TAG_MAP.get(tag)
        if field_name and hasattr(data, field_name):
            current = getattr(data, field_name)
            if not current:   # first hit wins
                setattr(data, field_name, text)
        else:
            data.raw_fields[tag] = text

    _resolve_scale(data)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Parser — raw text strings (PDF / OCR)
# ─────────────────────────────────────────────────────────────────────────────

def parse_from_text(
    text_lines: list[str],
    drawing_unit: str = "mm",
) -> TitleBlockData:
    """
    Parse title-block data from a list of raw text strings.

    Each string is one text element from pdfplumber / OCR output.
    Strings are processed in order; first match per field wins.
    """
    data = TitleBlockData(parse_source="TEXT_REGEX")
    combined = "\n".join(text_lines)

    for field_name, patterns in _COMPILED_FIELDS.items():
        if field_name == "scale":
            continue   # handled separately via scale_extractor
        for pat in patterns:
            m = pat.search(combined)
            if m:
                val = m.group("val").strip()
                if val and not getattr(data, field_name, ""):
                    if hasattr(data, field_name):
                        setattr(data, field_name, val)
                    else:
                        data.raw_fields[field_name] = val
                break

    # Scale — use scale_extractor for full 5-method detection
    scale_res = scale_from_text(text_lines, drawing_unit=drawing_unit)
    if scale_res:
        data.scale_raw = scale_res.scale_string
        data.scale_result = scale_res

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Parser — mixed (DXF attribs + supplementary text)
# ─────────────────────────────────────────────────────────────────────────────

def parse_titleblock(
    attribs: list[dict[str, str]] | None = None,
    text_lines: list[str] | None = None,
    drawing_unit: str = "mm",
) -> TitleBlockData:
    """
    Unified parser: try DXF attribs first, fill gaps from text regex.
    """
    data = TitleBlockData(parse_source="MIXED")

    if attribs:
        dxf_data = parse_from_dxf_attribs(attribs)
        _merge(data, dxf_data)

    if text_lines:
        text_data = parse_from_text(text_lines, drawing_unit=drawing_unit)
        _merge(data, text_data)   # only fills empty fields

    _resolve_scale(data)
    return data


def _merge(target: TitleBlockData, source: TitleBlockData) -> None:
    """Copy non-empty fields from source into target (target fields win)."""
    for fname in (
        "sheet_number", "sheet_title", "revision", "date",
        "drawn_by", "checked_by", "project_title", "client",
        "scale_raw", "scale_result", "status",
    ):
        if not getattr(target, fname) and getattr(source, fname):
            setattr(target, fname, getattr(source, fname))
    for k, v in source.raw_fields.items():
        target.raw_fields.setdefault(k, v)


def _resolve_scale(data: TitleBlockData) -> None:
    """If scale_raw is set but scale_result is None, parse scale_raw."""
    if data.scale_raw and data.scale_result is None:
        result = scale_from_text([data.scale_raw])
        if result:
            data.scale_result = result
