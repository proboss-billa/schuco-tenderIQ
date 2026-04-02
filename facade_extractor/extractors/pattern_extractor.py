"""
pattern_extractor.py
────────────────────
Detects repeat / modulation patterns in drawing geometry.

Algorithm:
  1. Collect centroids of all geometry per orientation (H/V)
  2. Compute all pairwise differences along each axis
  3. Build a histogram of those differences
  4. Use scipy.signal.find_peaks to identify dominant spacings
  5. Return top-3 candidate spacings with occurrence frequency

Produces RawMeasurement objects with extraction_method="PATTERN".
Confidence range: 0.45–0.65 per spec.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    from scipy.signal import find_peaks
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

from parsers.base_parser import DrawingSheet, LineSegment
from extractors.dimension_extractor import RawMeasurement

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_MIN_OCCURRENCES = 2        # ignore spacings that appear fewer than this
_BIN_SIZE_MM     = 10.0     # histogram bin width in mm
_MAX_SPACING_MM  = 10_000.0 # ignore spacings larger than 10 m
_MIN_SPACING_MM  = 50.0     # ignore spacings smaller than 50 mm (noise)
_TOP_N           = 3        # return top N spacings


@dataclass
class SpacingCandidate:
    spacing_mm: float
    occurrences: int
    axis: str          # X | Y
    confidence: float


class PatternExtractor:

    def extract(self, sheet: DrawingSheet) -> list[RawMeasurement]:
        results: list[RawMeasurement] = []

        mm_per_unit = 1.0
        scale_known = False
        if sheet.scale_result:
            mm_per_unit = sheet.scale_result.mm_per_unit or 1.0
            scale_known = sheet.scale_result.source != "UNKNOWN"

        scale_penalty = 1.0 if scale_known else 0.70

        # Collect geometry midpoints
        x_coords: list[float] = []
        y_coords: list[float] = []

        for ln in sheet.lines:
            mx = (ln.start.x + ln.end.x) / 2 * mm_per_unit
            my = (ln.start.y + ln.end.y) / 2 * mm_per_unit
            x_coords.append(mx)
            y_coords.append(my)

        for circle in sheet.circles:
            x_coords.append(circle.center.x * mm_per_unit)
            y_coords.append(circle.center.y * mm_per_unit)

        # Detect spacings on each axis
        x_candidates = self._detect_spacings(x_coords, axis="X")
        y_candidates = self._detect_spacings(y_coords, axis="Y")

        for cand in x_candidates + y_candidates:
            base_conf = 0.45 + 0.20 * min(1.0, cand.occurrences / 10)
            conf = min(0.65, base_conf * scale_penalty)

            direction = "HORIZONTAL" if cand.axis == "X" else "VERTICAL"
            context_words = (
                ["horizontal", "modulation", "bay", "spacing"]
                if cand.axis == "X"
                else ["vertical", "floor", "storey", "spacing"]
            )

            m = RawMeasurement(
                value_mm=cand.spacing_mm,
                unit="mm",
                confidence=conf,
                extraction_method="PATTERN",
                source_text=f"@{cand.spacing_mm:.0f}mm ({cand.axis}-axis, n={cand.occurrences})",
                source_layer="",
                source_page=0,
                x=0.0,
                y=0.0,
                direction=direction,
                qualifier=None,
                context_words=context_words,
            )
            results.append(m)

        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _detect_spacings(
        self, coords: list[float], axis: str
    ) -> list[SpacingCandidate]:
        if len(coords) < 3:
            return []

        arr = np.array(sorted(set(round(c, 1) for c in coords)))

        # All pairwise differences
        diffs = []
        for i in range(len(arr)):
            for j in range(i + 1, len(arr)):
                d = arr[j] - arr[i]
                if _MIN_SPACING_MM <= d <= _MAX_SPACING_MM:
                    diffs.append(d)

        if not diffs:
            return []

        # Histogram — always start from 0 so base spacing lands in correct bin
        max_d = max(diffs)
        bins = max(1, int(max_d / _BIN_SIZE_MM) + 1)
        counts, edges = np.histogram(diffs, bins=bins, range=(0.0, max_d + _BIN_SIZE_MM))

        if not SCIPY_AVAILABLE:
            # Simple top-N without scipy
            candidates = sorted(
                zip(edges[:-1], counts), key=lambda x: -x[1]
            )[:_TOP_N]
            return [
                SpacingCandidate(
                    spacing_mm=round(float(e + _BIN_SIZE_MM / 2), 1),
                    occurrences=int(c),
                    axis=axis,
                    confidence=0.50,
                )
                for e, c in candidates if c >= _MIN_OCCURRENCES
            ]

        # scipy peak detection
        peaks, props = find_peaks(
            counts,
            height=_MIN_OCCURRENCES,
            distance=max(1, int(50 / _BIN_SIZE_MM)),  # at least 50 mm apart
        )

        peak_spacings = [
            SpacingCandidate(
                spacing_mm=round(float(edges[p] + _BIN_SIZE_MM / 2), 1),
                occurrences=int(counts[p]),
                axis=axis,
                confidence=0.50,
            )
            for p in peaks
        ]
        # Sort by occurrence count descending, return top N
        peak_spacings.sort(key=lambda c: -c.occurrences)
        return peak_spacings[:_TOP_N]


def extract_patterns(sheet: DrawingSheet) -> list[RawMeasurement]:
    """Module-level convenience wrapper."""
    return PatternExtractor().extract(sheet)
