"""
parameter_matcher.py
────────────────────
Maps RawMeasurement objects → catalog parameters.

Algorithm per measurement:
  1. Run fuzzy_matcher against all catalog entries
  2. Accept best match above min_score
  3. Apply confidence modifiers (multiple-source agreement, TYP flag)
  4. Build MatchedParameter output

Also:
  • Runs spec cross-check if spec references are loaded
  • Collects unmatched measurements
  • Reports NOT_FOUND parameters
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from extractors.dimension_extractor import RawMeasurement
from matchers.fuzzy_matcher import find_best_match, MatchScore

# ─────────────────────────────────────────────────────────────────────────────
# Output model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SpecCheck:
    spec_value: Optional[float] = None
    tolerance: Optional[float] = None
    direction: str = "EXACT"        # MIN | MAX | EXACT
    result: str = "NO_SPEC"         # NO_SPEC | MATCH | CONFLICT
    delta: Optional[float] = None
    source: str = ""


@dataclass
class MatchedParameter:
    id: int
    name: str
    value: float
    unit: str = "mm"
    confidence: float = 0.0
    extraction_method: str = ""
    source_text: str = ""
    source_layer: str = ""
    source_page: int = 0
    source_coords: list[float] = field(default_factory=list)
    direction: str = "ANY"
    qualifier: Optional[str] = None
    notes: str = ""
    spec_check: SpecCheck = field(default_factory=SpecCheck)
    values_mm: list[float] = field(default_factory=list)   # compound dims

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "name":             self.name,
            "value":            round(self.value, 3),
            "unit":             self.unit,
            "confidence":       round(self.confidence, 3),
            "extraction_method": self.extraction_method,
            "source_text":      self.source_text,
            "source_layer":     self.source_layer,
            "source_page":      self.source_page,
            "source_coords":    [round(c, 2) for c in self.source_coords],
            "spec_check": {
                "spec_value":  self.spec_check.spec_value,
                "tolerance":   self.spec_check.tolerance,
                "result":      self.spec_check.result,
                "delta":       round(self.spec_check.delta, 3)
                               if self.spec_check.delta is not None else None,
                "source":      self.spec_check.source,
            },
            "notes": self.notes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Matcher
# ─────────────────────────────────────────────────────────────────────────────

class ParameterMatcher:

    def __init__(
        self,
        catalog: list[dict],
        spec_refs: list[dict] | None = None,
        min_match_score: float = 0.30,
        min_confidence: float = 0.40,
    ):
        self.catalog         = catalog
        self.spec_refs       = {
            s["parameter_name"]: s for s in (spec_refs or [])
        }
        self.min_match_score = min_match_score
        self.min_confidence  = min_confidence

    def match(
        self,
        measurements: list[RawMeasurement],
        sheet_type: str = "UNKNOWN",
    ) -> tuple[list[MatchedParameter], list[RawMeasurement]]:
        """
        Match all measurements to catalog entries.

        Returns
        -------
        matched   : list[MatchedParameter] — one per catalog entry (best value)
        unmatched : list[RawMeasurement]   — measurements not matched to anything
        """
        # candidate_pool[param_id] = list of (measurement, match_score)
        candidate_pool: dict[int, list[tuple[RawMeasurement, MatchScore]]] = {
            p["id"]: [] for p in self.catalog
        }
        unmatched: list[RawMeasurement] = []

        for meas in measurements:
            best = find_best_match(
                context_words=meas.context_words,
                source_text=meas.source_text,
                catalog=self.catalog,
                measurement_direction=meas.direction,
                sheet_type=sheet_type,
                min_score=self.min_match_score,
            )
            if best is None:
                unmatched.append(meas)
            else:
                candidate_pool[best.parameter_id].append((meas, best))

        # Resolve each parameter: pick highest-confidence candidate
        matched: list[MatchedParameter] = []
        for param in self.catalog:
            pid = param["id"]
            candidates = candidate_pool.get(pid, [])

            if not candidates:
                continue   # NOT_FOUND — will be added in result_builder

            # Sort by measurement confidence × match score
            candidates.sort(
                key=lambda x: x[0].confidence * x[1].score,
                reverse=True,
            )
            best_meas, best_score = candidates[0]

            # Multiple-source agreement bonus
            conf = best_meas.confidence
            if len(candidates) > 1:
                # Check if other sources agree within 5%
                agreeing = sum(
                    1 for m, _ in candidates[1:]
                    if abs(m.value_mm - best_meas.value_mm) / max(best_meas.value_mm, 1) <= 0.05
                )
                if agreeing >= 1:
                    conf = min(1.0, conf * 1.10)

            # TYP qualifier — no change to confidence but note it
            notes = ""
            if best_meas.qualifier == "TYP":
                notes = "TYPICAL"
            elif best_meas.qualifier == "MIN":
                notes = "MINIMUM"
            elif best_meas.qualifier == "MAX":
                notes = "MAXIMUM"

            # Apply catalog confidence threshold
            threshold = param.get("confidence_threshold", self.min_confidence)
            if conf < threshold:
                continue

            # Spec cross-check
            spec_check = self._check_spec(pid, param["name"], best_meas.value_mm)

            mp = MatchedParameter(
                id=pid,
                name=param["name"],
                value=best_meas.value_mm,
                unit=param.get("unit", "mm"),
                confidence=round(conf, 4),
                extraction_method=best_meas.extraction_method,
                source_text=best_meas.source_text,
                source_layer=best_meas.source_layer,
                source_page=best_meas.source_page,
                source_coords=[best_meas.x, best_meas.y],
                direction=best_meas.direction,
                qualifier=best_meas.qualifier,
                notes=notes,
                spec_check=spec_check,
                values_mm=best_meas.values_mm,
            )
            matched.append(mp)

        return matched, unmatched

    # ── Spec cross-check ──────────────────────────────────────────────────────

    def _check_spec(
        self,
        param_id: int,
        param_name: str,
        extracted_mm: float,
    ) -> SpecCheck:
        ref = self.spec_refs.get(param_name)
        if not ref:
            return SpecCheck(result="NO_SPEC")

        spec_val  = float(ref.get("spec_value", 0.0))
        tolerance = float(ref.get("tolerance", 0.0))
        direction = (ref.get("direction") or "EXACT").upper()
        source    = ref.get("source", "")

        delta = extracted_mm - spec_val

        if direction == "MIN":
            result = "MATCH" if extracted_mm >= spec_val - tolerance else "CONFLICT"
        elif direction == "MAX":
            result = "MATCH" if extracted_mm <= spec_val + tolerance else "CONFLICT"
        else:   # EXACT
            result = "MATCH" if abs(delta) <= tolerance else "CONFLICT"

        return SpecCheck(
            spec_value=spec_val,
            tolerance=tolerance,
            direction=direction,
            result=result,
            delta=round(delta, 3),
            source=source,
        )
