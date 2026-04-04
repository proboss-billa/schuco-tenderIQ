"""
fuzzy_matcher.py
────────────────
Scores a candidate text string (from context words or source text) against
a parameter catalog entry using:

  1. Token-set ratio (rapidfuzz) on name + aliases
  2. Keyword proximity boost (any alias keyword found in context)
  3. Direction compatibility check
  4. Sheet type compatibility check

Returns a MatchScore dataclass with a combined score 0.0–1.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# Score model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchScore:
    parameter_id: int
    parameter_name: str
    score: float               # 0.0 – 1.0
    fuzzy_score: float         # raw rapidfuzz score (0–1)
    keyword_hit: bool          # True if an alias keyword was found verbatim
    direction_ok: bool         # True if directions are compatible
    sheet_type_ok: bool        # True if sheet types are compatible
    best_matched_alias: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Fuzzy scorer
# ─────────────────────────────────────────────────────────────────────────────

def score_against_parameter(
    context_words: list[str],
    source_text: str,
    param: dict,
    measurement_direction: str = "ANY",
    sheet_type: str = "UNKNOWN",
    fuzzy_threshold: float = 40.0,
) -> MatchScore:
    """
    Score one candidate measurement against one catalog parameter entry.

    Parameters
    ----------
    context_words         : words near the measurement (from nearby text)
    source_text           : raw text of the measurement entity
    param                 : dict from parameter_catalog.yaml
    measurement_direction : HORIZONTAL | VERTICAL | ANGULAR | ANY
    sheet_type            : PLAN | ELEVATION | SECTION | DETAIL | ...
    fuzzy_threshold       : rapidfuzz score below this is ignored

    Returns
    -------
    MatchScore
    """
    param_id   = param.get("id", 0)
    param_name = param.get("name", "")
    aliases    = [param_name] + list(param.get("aliases", []))

    # ── Build query string ────────────────────────────────────────────────
    query = " ".join(context_words) + " " + source_text
    query_lower = query.lower()

    # ── 1. Fuzzy score ────────────────────────────────────────────────────
    best_fuzzy = 0.0
    best_alias = ""

    for alias in aliases:
        if RAPIDFUZZ_AVAILABLE:
            s = fuzz.token_set_ratio(query_lower, alias.lower())
        else:
            s = _simple_ratio(query_lower, alias.lower())
        if s > best_fuzzy:
            best_fuzzy = s
            best_alias = alias

    if best_fuzzy < fuzzy_threshold:
        return MatchScore(
            parameter_id=param_id,
            parameter_name=param_name,
            score=0.0,
            fuzzy_score=best_fuzzy / 100,
            keyword_hit=False,
            direction_ok=True,
            sheet_type_ok=True,
            best_matched_alias=best_alias,
        )

    # ── 2. Keyword hit (verbatim alias words in context) ──────────────────
    keyword_hit = False
    for alias in aliases:
        alias_words = alias.lower().split()
        if all(w in query_lower for w in alias_words):
            keyword_hit = True
            best_alias = alias
            break

    # ── 3. Direction compatibility ────────────────────────────────────────
    param_direction = param.get("dimension_direction", "ANY")
    direction_ok = (
        param_direction == "ANY"
        or measurement_direction == "ANY"
        or param_direction == measurement_direction
    )

    # ── 4. Sheet type compatibility ───────────────────────────────────────
    relevant_types = param.get("relevant_sheet_types", [])
    sheet_type_ok = (
        not relevant_types
        or sheet_type == "UNKNOWN"
        or sheet_type in relevant_types
    )

    # ── 5. Combined score ─────────────────────────────────────────────────
    score = best_fuzzy / 100.0   # 0–1

    if keyword_hit:
        score = min(1.0, score * 1.20)
    if not direction_ok:
        score *= 0.50
    if not sheet_type_ok:
        score *= 0.70

    return MatchScore(
        parameter_id=param_id,
        parameter_name=param_name,
        score=round(score, 4),
        fuzzy_score=round(best_fuzzy / 100, 4),
        keyword_hit=keyword_hit,
        direction_ok=direction_ok,
        sheet_type_ok=sheet_type_ok,
        best_matched_alias=best_alias,
    )


def find_best_match(
    context_words: list[str],
    source_text: str,
    catalog: list[dict],
    measurement_direction: str = "ANY",
    sheet_type: str = "UNKNOWN",
    min_score: float = 0.30,
) -> Optional[MatchScore]:
    """
    Score against all catalog entries and return the best match above
    min_score, or None.
    """
    scores = [
        score_against_parameter(
            context_words, source_text, param,
            measurement_direction, sheet_type,
        )
        for param in catalog
    ]
    best = max(scores, key=lambda s: s.score) if scores else None
    if best and best.score >= min_score:
        return best
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Simple fallback ratio (no rapidfuzz)
# ─────────────────────────────────────────────────────────────────────────────

def _simple_ratio(a: str, b: str) -> float:
    """Jaccard-based token overlap ratio, range 0–100."""
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a and not set_b:
        return 100.0
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union        = set_a | set_b
    return 100.0 * len(intersection) / len(union)
