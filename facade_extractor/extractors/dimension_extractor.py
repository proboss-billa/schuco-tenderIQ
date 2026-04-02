"""
dimension_extractor.py
──────────────────────
Turns raw DrawingSheet entity lists into a flat list of RawMeasurement
objects ready for parameter matching.

Two extraction passes:

  PASS A — DIMENSION entities (highest confidence)
    • Read annotated value
    • Find nearest TEXT label within search radius
    • Classify direction (HORIZONTAL / VERTICAL / ANGULAR)
    • Record confidence

  PASS B — TEXT / MTEXT without associated DIMENSION
    • Run universal regex suite
    • Associate with nearest geometry pair
    • Confirm with scale-corrected geometry distance
    • Lower confidence than PASS A
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from parsers.base_parser import (
    DrawingSheet, DimensionEntity, TextEntity, LineSegment,
)
from extractors.text_extractor import extract_text_matches, TextMatch
from matchers.unit_normaliser import normalise_to_mm

# ─────────────────────────────────────────────────────────────────────────────
# Output model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawMeasurement:
    """One candidate extracted measurement before parameter matching."""
    value_mm: float
    unit: str = "mm"
    confidence: float = 0.50
    extraction_method: str = "DIMENSION_ENTITY"
    # DIMENSION_ENTITY | MTEXT | TEXT | GEOMETRY | PATTERN | OCR

    source_text: str = ""
    source_layer: str = ""
    source_page: int = 0
    x: float = 0.0
    y: float = 0.0

    direction: str = "ANY"        # HORIZONTAL | VERTICAL | ANGULAR | ANY
    qualifier: Optional[str] = None  # MIN | MAX | TYP | NTS
    annotation_value: Optional[str] = None
    context_words: list[str] = field(default_factory=list)
    # ^ nearby text words used for fuzzy parameter matching

    # compound dimensions
    values_mm: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "value_mm":          self.value_mm,
            "unit":              self.unit,
            "confidence":        round(self.confidence, 3),
            "extraction_method": self.extraction_method,
            "source_text":       self.source_text,
            "source_layer":      self.source_layer,
            "source_page":       self.source_page,
            "coords":            [round(self.x, 2), round(self.y, 2)],
            "direction":         self.direction,
            "qualifier":         self.qualifier,
            "context_words":     self.context_words,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Configuration constants
# ─────────────────────────────────────────────────────────────────────────────

# Search radius to associate a TEXT label with a DIMENSION entity (drawing units)
_LABEL_SEARCH_RADIUS = 200.0   # mm — generous for detail drawings

# Angle threshold (degrees) for classifying a dimension as H or V
_ANGLE_THRESHOLD = 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Main extractor class
# ─────────────────────────────────────────────────────────────────────────────

class DimensionExtractor:

    def __init__(self, scale_unknown_penalty: float = 0.70):
        self.scale_unknown_penalty = scale_unknown_penalty

    def extract(self, sheet: DrawingSheet) -> list[RawMeasurement]:
        """
        Run both extraction passes on a DrawingSheet.
        Returns a combined, deduplicated list of RawMeasurement objects.
        """
        results: list[RawMeasurement] = []

        scale_known = (
            sheet.scale_result is not None
            and sheet.scale_result.source != "UNKNOWN"
        )
        scale_penalty = 1.0 if scale_known else self.scale_unknown_penalty

        # PASS A — DIMENSION entities
        results.extend(
            self._pass_a(sheet, scale_penalty)
        )

        # PASS B — TEXT / MTEXT
        results.extend(
            self._pass_b(sheet, scale_penalty)
        )

        return results

    # ── PASS A ────────────────────────────────────────────────────────────────

    def _pass_a(
        self, sheet: DrawingSheet, scale_penalty: float
    ) -> list[RawMeasurement]:
        measurements: list[RawMeasurement] = []

        for dim in sheet.dimensions:
            if dim.value_mm <= 0:
                continue

            direction = self._dim_direction(dim)
            context   = self._collect_context_words(dim.x, dim.y, sheet.texts)

            base_conf = 0.95
            conf = min(1.0, base_conf * scale_penalty)

            # Check if TYP / MIN / MAX appears nearby
            qualifier = _qualifier_from_override(dim.override_text)

            m = RawMeasurement(
                value_mm=dim.value_mm,
                unit="mm",
                confidence=conf,
                extraction_method="DIMENSION_ENTITY",
                source_text=dim.raw_text,
                source_layer=dim.layer,
                source_page=dim.page,
                x=dim.x,
                y=dim.y,
                direction=direction,
                qualifier=qualifier,
                context_words=context,
            )
            measurements.append(m)

        return measurements

    # ── PASS B ────────────────────────────────────────────────────────────────

    def _pass_b(
        self, sheet: DrawingSheet, scale_penalty: float
    ) -> list[RawMeasurement]:
        """Extract dimensions from TEXT/MTEXT entities not already matched
        by a DIMENSION entity."""

        # Build a set of positions already covered by PASS A
        covered_positions: set[tuple] = set()
        for dim in sheet.dimensions:
            covered_positions.add((round(dim.x, 1), round(dim.y, 1)))

        measurements: list[RawMeasurement] = []

        for te in sheet.texts:
            pos_key = (round(te.x, 1), round(te.y, 1))
            if pos_key in covered_positions:
                continue

            matches = extract_text_matches(te.text)
            if not matches:
                continue

            for tm in matches:
                # Annotation-only (no mm value)
                if not tm.mm_values:
                    # Still record it for parameter matcher (alloy, finish etc.)
                    if tm.annotation_value:
                        m = RawMeasurement(
                            value_mm=0.0,
                            unit="",
                            confidence=0.70 * scale_penalty,
                            extraction_method="MTEXT",
                            source_text=te.text,
                            source_layer=te.layer,
                            source_page=te.page,
                            x=te.x,
                            y=te.y,
                            direction="ANY",
                            qualifier=tm.qualifier,
                            annotation_value=tm.annotation_value,
                            context_words=te.text.split()[:10],
                        )
                        measurements.append(m)
                    continue

                primary_mm = tm.primary_mm
                if primary_mm is None or primary_mm <= 0:
                    continue

                # Geometry association: find nearest line pair that matches
                geo_conf_bonus = self._geometry_association_bonus(
                    te, primary_mm, sheet.lines, sheet.scale_result
                )

                # Unit source confidence modifier
                unit_bonus = 1.05 if tm.unit else 0.90

                base_conf = 0.72  # TEXT with unit but no DIMENSION entity
                conf = min(1.0, base_conf * scale_penalty * unit_bonus * geo_conf_bonus)

                # Direction from nearest geometry
                direction = self._direction_from_nearest_line(te, sheet.lines)

                # Compound dimensions
                vals_mm = tm.mm_values if len(tm.mm_values) > 1 else []

                m = RawMeasurement(
                    value_mm=primary_mm,
                    unit=tm.unit or "mm",
                    confidence=conf,
                    extraction_method="MTEXT" if "MTEXT" in te.layer.upper() else "TEXT",
                    source_text=te.text,
                    source_layer=te.layer,
                    source_page=te.page,
                    x=te.x,
                    y=te.y,
                    direction=direction,
                    qualifier=tm.qualifier,
                    context_words=te.text.split()[:10],
                    values_mm=vals_mm,
                )
                measurements.append(m)

        return measurements

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _dim_direction(dim: DimensionEntity) -> str:
        """Infer direction from DIMENSION entity geometry or type."""
        if dim.dim_type == "ANGULAR":
            return "ANGULAR"
        if dim.geometry_length > 0 and dim.defpoint_x and dim.defpoint_y:
            dx = abs(dim.x - dim.defpoint_x)
            dy = abs(dim.y - dim.defpoint_y)
            if dx + dy == 0:
                return "ANY"
            angle = math.degrees(math.atan2(dy, dx))
            if angle < _ANGLE_THRESHOLD or angle > (180 - _ANGLE_THRESHOLD):
                return "HORIZONTAL"
            if abs(angle - 90) < _ANGLE_THRESHOLD:
                return "VERTICAL"
        return "ANY"

    @staticmethod
    def _collect_context_words(
        x: float, y: float,
        texts: list[TextEntity],
        radius: float = _LABEL_SEARCH_RADIUS,
    ) -> list[str]:
        """Gather words from nearby text entities (for fuzzy matching)."""
        words: list[str] = []
        for te in texts:
            dist = math.hypot(te.x - x, te.y - y)
            if dist <= radius:
                words.extend(te.text.lower().split())
        return list(dict.fromkeys(words))[:30]   # deduplicate, cap at 30

    @staticmethod
    def _geometry_association_bonus(
        text_entity: TextEntity,
        value_mm: float,
        lines: list[LineSegment],
        scale_result: Any,
        radius: float = _LABEL_SEARCH_RADIUS,
        tolerance: float = 0.05,
    ) -> float:
        """
        Check whether a nearby line pair has a separation matching value_mm.
        Returns 1.10 if confirmed, 1.0 if not found (neutral).
        """
        if not scale_result or scale_result.source == "UNKNOWN":
            return 1.0
        mm_per_unit = scale_result.mm_per_unit
        if mm_per_unit == 0:
            return 1.0

        tx, ty = text_entity.x, text_entity.y

        nearby = [
            ln for ln in lines
            if (
                math.hypot((ln.start.x + ln.end.x) / 2 - tx,
                           (ln.start.y + ln.end.y) / 2 - ty)
                <= radius * 2
            )
        ]

        # Check pairs for matching separation
        for i, a in enumerate(nearby):
            for b in nearby[i + 1:]:
                sep = _line_separation(a, b)
                if sep is None:
                    continue
                sep_mm = sep * mm_per_unit
                if abs(sep_mm - value_mm) / max(value_mm, 1) < tolerance:
                    return 1.10

        return 1.0

    @staticmethod
    def _direction_from_nearest_line(
        text_entity: TextEntity,
        lines: list[LineSegment],
        radius: float = _LABEL_SEARCH_RADIUS,
    ) -> str:
        """Infer dimension direction from the nearest line's orientation."""
        tx, ty = text_entity.x, text_entity.y
        best_dist = float("inf")
        best_orient = "ANY"
        for ln in lines:
            mx = (ln.start.x + ln.end.x) / 2
            my = (ln.start.y + ln.end.y) / 2
            dist = math.hypot(mx - tx, my - ty)
            if dist < best_dist and dist <= radius:
                best_dist = dist
                best_orient = ln.orientation
        return best_orient if best_orient != "DIAGONAL" else "ANY"


# ─────────────────────────────────────────────────────────────────────────────
# Utility: perpendicular separation between two parallel line segments
# ─────────────────────────────────────────────────────────────────────────────

def _line_separation(a: LineSegment, b: LineSegment) -> Optional[float]:
    """
    Return perpendicular separation if two lines are parallel (H or V),
    else None.
    """
    if a.orientation != b.orientation:
        return None
    if a.orientation == "HORIZONTAL":
        return abs((a.start.y + a.end.y) / 2 - (b.start.y + b.end.y) / 2)
    if a.orientation == "VERTICAL":
        return abs((a.start.x + a.end.x) / 2 - (b.start.x + b.end.x) / 2)
    return None


def _qualifier_from_override(override: str) -> Optional[str]:
    lower = override.lower()
    if "typ" in lower or "typical" in lower:
        return "TYP"
    if "min" in lower:
        return "MIN"
    if "max" in lower:
        return "MAX"
    if "nts" in lower:
        return "NTS"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def extract_dimensions(sheet: DrawingSheet) -> list[RawMeasurement]:
    """Module-level convenience wrapper."""
    return DimensionExtractor().extract(sheet)
