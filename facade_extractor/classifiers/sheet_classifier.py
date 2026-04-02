"""
sheet_classifier.py
───────────────────
Classifies a drawing sheet as one of:
  PLAN | ELEVATION | SECTION | DETAIL | SCHEDULE | ASSEMBLY | UNKNOWN

Method: keyword density scoring on ALL text present on the sheet.
The top-scoring type wins. Ties → highest-priority type in the order above.

Returns a SheetClassification dataclass with scores for audit/debug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Keyword dictionaries — fully configurable, no project-specifics
# ─────────────────────────────────────────────────────────────────────────────

SHEET_KEYWORDS: dict[str, list[str]] = {
    "PLAN": [
        "plan", "floor plan", "layout", "levels", "level plan",
        "reflected ceiling", "rcp", "roof plan", "site plan",
        "ground floor", "first floor", "typical floor",
    ],
    "ELEVATION": [
        "elevation", "elev", "facade elevation", "external elevation",
        "north elevation", "south elevation", "east elevation",
        "west elevation", "front elevation", "rear elevation",
        "side elevation", "facade",
    ],
    "SECTION": [
        "section", "cross section", "cross-section", "longitudinal",
        "s-s", "cut section", "building section", "wall section",
        "horizontal section", "vertical section", "sectional",
    ],
    "DETAIL": [
        "detail", "dtl", "enlarged", "typ detail", "typical detail",
        "connection detail", "junction", "assembly detail",
        "head detail", "sill detail", "jamb detail",
        "mullion detail", "transom detail", "bracket detail",
        "fixing detail", "corner detail",
    ],
    "SCHEDULE": [
        "schedule", "table", "list", "legend", "key",
        "door schedule", "window schedule", "panel schedule",
        "finish schedule", "material schedule", "hardware schedule",
        "revision schedule", "drawing list",
    ],
    "ASSEMBLY": [
        "assembly", "exploded", "sequence", "installation",
        "installation sequence", "erection", "assembly sequence",
        "set out",
    ],
}

# Priority order for tie-breaking (first = highest priority)
_PRIORITY_ORDER = ["DETAIL", "SECTION", "ELEVATION", "PLAN", "ASSEMBLY", "SCHEDULE"]


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SheetClassification:
    sheet_type: str                      # winning type
    scores: dict[str, int] = field(default_factory=dict)
    confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    title_hint: str = ""                 # if sheet title was found, put it here


# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────

def classify_sheet(
    text_corpus: list[str],
    sheet_title: str = "",
) -> SheetClassification:
    """
    Classify a drawing sheet.

    Parameters
    ----------
    text_corpus  : All text strings found on the sheet (titles, labels,
                   annotations, schedules, title-block fields).
    sheet_title  : If the title-block title is known, pass it here for
                   priority weighting (counts 3× in scoring).

    Returns
    -------
    SheetClassification
    """
    # Combine into one normalised string
    full_text = " ".join(text_corpus).lower()
    if sheet_title:
        # Title text counts 3×
        full_text += (" " + sheet_title.lower()) * 3

    scores: dict[str, int] = {k: 0 for k in SHEET_KEYWORDS}
    matched: list[str] = []

    for sheet_type, keywords in SHEET_KEYWORDS.items():
        for kw in keywords:
            # Whole-word / phrase match
            pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
            hits = pattern.findall(full_text)
            if hits:
                scores[sheet_type] += len(hits)
                matched.extend(hits)

    # Find winner
    if all(v == 0 for v in scores.values()):
        return SheetClassification(
            sheet_type="UNKNOWN",
            scores=scores,
            confidence=0.0,
            matched_keywords=[],
            title_hint=sheet_title,
        )

    max_score = max(scores.values())
    # Among tied types, pick highest priority
    winners = [t for t, s in scores.items() if s == max_score]
    winner = _pick_by_priority(winners)

    # Confidence: how dominant is the winner?
    total = sum(scores.values()) or 1
    confidence = min(1.0, max_score / total * len(scores))

    return SheetClassification(
        sheet_type=winner,
        scores=scores,
        confidence=round(confidence, 3),
        matched_keywords=list(set(matched)),
        title_hint=sheet_title,
    )


def _pick_by_priority(candidates: list[str]) -> str:
    for t in _PRIORITY_ORDER:
        if t in candidates:
            return t
    return candidates[0] if candidates else "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: batch classify
# ─────────────────────────────────────────────────────────────────────────────

def classify_sheets(
    pages: list[dict],
) -> list[SheetClassification]:
    """
    Batch-classify a list of page dicts.

    Each dict must contain:
        "texts"  : list[str]  — all text strings on the page
        "title"  : str        — sheet title (optional, can be "")
    """
    return [
        classify_sheet(page.get("texts", []), page.get("title", ""))
        for page in pages
    ]
