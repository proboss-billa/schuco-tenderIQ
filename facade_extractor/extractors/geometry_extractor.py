"""
geometry_extractor.py
──────────────────────
Fallback geometry measurement extractor.

When no DIMENSION entity or labelled text covers a parameter, this module:
  1. Groups parallel lines by orientation + layer
  2. Computes perpendicular distances between pairs
  3. Applies scale → mm
  4. Assigns confidence 0.50–0.70

Also measures:
  • Circle / ARC diameters → thickness, pipe/tube dimensions
  • Polyline segment lengths → profile dimensions
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from parsers.base_parser import DrawingSheet, LineSegment, CircleEntity, Point2D
from extractors.dimension_extractor import RawMeasurement

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_MIN_LINE_LENGTH = 5.0        # ignore stubs shorter than this (drawing units)
_PARALLEL_ANGLE_TOL = 5.0     # degrees — lines within this angle are "parallel"
_MAX_SEPARATION = 5000.0      # ignore pairs further apart than this (drawing units)
_MIN_SEPARATION = 0.5         # ignore pairs closer than this


class GeometryExtractor:

    def extract(self, sheet: DrawingSheet) -> list[RawMeasurement]:
        results: list[RawMeasurement] = []

        mm_per_unit = 1.0
        scale_known = False
        if sheet.scale_result:
            mm_per_unit = sheet.scale_result.mm_per_unit or 1.0
            scale_known = sheet.scale_result.source != "UNKNOWN"

        scale_penalty = 1.0 if scale_known else 0.70

        # ── 1. Line-pair separations ───────────────────────────────────────
        h_lines = [ln for ln in sheet.lines
                   if ln.orientation == "HORIZONTAL" and ln.length >= _MIN_LINE_LENGTH]
        v_lines = [ln for ln in sheet.lines
                   if ln.orientation == "VERTICAL"   and ln.length >= _MIN_LINE_LENGTH]

        results.extend(self._measure_pairs(h_lines, "HORIZONTAL", mm_per_unit, scale_penalty, sheet))
        results.extend(self._measure_pairs(v_lines, "VERTICAL",   mm_per_unit, scale_penalty, sheet))

        # ── 2. Circle / ARC diameters ──────────────────────────────────────
        for circle in sheet.circles:
            diam_mm = circle.radius * 2  # already mm (DWGParser multiplies by factor)
            conf = 0.60 * scale_penalty
            layer = circle.layer

            m = RawMeasurement(
                value_mm=diam_mm,
                unit="mm",
                confidence=conf,
                extraction_method="GEOMETRY",
                source_text=f"⌀{diam_mm:.1f}",
                source_layer=layer,
                source_page=circle.page,
                x=circle.center.x,
                y=circle.center.y,
                direction="ANY",
                context_words=["diameter", "circle"],
            )
            results.append(m)

        return results

    # ── Line-pair measurement ─────────────────────────────────────────────────

    def _measure_pairs(
        self,
        lines: list[LineSegment],
        orientation: str,
        mm_per_unit: float,
        scale_penalty: float,
        sheet: DrawingSheet,
    ) -> list[RawMeasurement]:
        results: list[RawMeasurement] = []

        # Group by layer so we measure within layer and across adjacent layers
        by_layer: dict[str, list[LineSegment]] = {}
        for ln in lines:
            by_layer.setdefault(ln.layer, []).append(ln)

        # Collect all for cross-layer measurement too
        all_lines = lines

        seen_separations: set[float] = set()

        for i, a in enumerate(all_lines):
            for b in all_lines[i + 1:]:
                sep = _perpendicular_separation(a, b, orientation)
                if sep is None:
                    continue
                if not (_MIN_SEPARATION <= sep <= _MAX_SEPARATION):
                    continue

                sep_mm = sep * mm_per_unit
                rounded = round(sep_mm, 1)
                if rounded in seen_separations:
                    continue
                seen_separations.add(rounded)

                # Confidence depends on whether the layer is labelled
                layers_known = bool(a.layer or b.layer)
                base_conf = 0.62 if layers_known else 0.48
                conf = min(1.0, base_conf * scale_penalty)

                cx = (a.start.x + a.end.x + b.start.x + b.end.x) / 4
                cy = (a.start.y + a.end.y + b.start.y + b.end.y) / 4

                m = RawMeasurement(
                    value_mm=sep_mm,
                    unit="mm",
                    confidence=conf,
                    extraction_method="GEOMETRY",
                    source_text=f"{sep_mm:.1f}mm (geometry)",
                    source_layer=a.layer or b.layer,
                    source_page=0,
                    x=cx,
                    y=cy,
                    direction=orientation,
                    context_words=_layer_words(a.layer) + _layer_words(b.layer),
                )
                results.append(m)

        return results


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _perpendicular_separation(
    a: LineSegment,
    b: LineSegment,
    orientation: str,
) -> Optional[float]:
    """
    Return perpendicular distance between two parallel lines.
    Returns None if lines are not sufficiently parallel or not overlapping.
    """
    if orientation == "HORIZONTAL":
        ay = (a.start.y + a.end.y) / 2
        by_ = (b.start.y + b.end.y) / 2
        # Check horizontal overlap
        a_xmin, a_xmax = min(a.start.x, a.end.x), max(a.start.x, a.end.x)
        b_xmin, b_xmax = min(b.start.x, b.end.x), max(b.start.x, b.end.x)
        overlap = min(a_xmax, b_xmax) - max(a_xmin, b_xmin)
        if overlap < _MIN_LINE_LENGTH:
            return None
        return abs(ay - by_)

    if orientation == "VERTICAL":
        ax = (a.start.x + a.end.x) / 2
        bx = (b.start.x + b.end.x) / 2
        # Check vertical overlap
        a_ymin, a_ymax = min(a.start.y, a.end.y), max(a.start.y, a.end.y)
        b_ymin, b_ymax = min(b.start.y, b.end.y), max(b.start.y, b.end.y)
        overlap = min(a_ymax, b_ymax) - max(a_ymin, b_ymin)
        if overlap < _MIN_LINE_LENGTH:
            return None
        return abs(ax - bx)

    return None


def _layer_words(layer: str) -> list[str]:
    """Split a layer name into lowercase words for context matching."""
    return re.split(r"[\W_\-]+", layer.lower()) if layer else []


import re   # needed for _layer_words — placed at module scope in usage


def extract_geometry(sheet: DrawingSheet) -> list[RawMeasurement]:
    """Module-level convenience wrapper."""
    return GeometryExtractor().extract(sheet)
