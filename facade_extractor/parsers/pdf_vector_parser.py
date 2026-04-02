"""
pdf_vector_parser.py
────────────────────
Priority-2a parser.  Uses pdfplumber to extract text and vector geometry
from PDF files.

Triggers raster fallback (PDFRasterParser) automatically when
the page has fewer than 20 line objects (scanned drawing).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional, Any

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

from parsers.base_parser import (
    BaseParser, DrawingSheet, LineSegment, TextEntity,
    DimensionEntity, Point2D,
)
from classifiers.scale_extractor import detect_scale
from classifiers.sheet_classifier import classify_sheet
from classifiers.titleblock_parser import parse_from_text
from extractors.text_extractor import extract_text_matches
from matchers.unit_normaliser import normalise_to_mm

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_RASTER_LINE_THRESHOLD = 20    # fewer lines → trigger raster parser
_TITLE_BLOCK_FRACTION  = 0.25  # bottom-right 25% = title block zone

# PDF coordinates: origin bottom-left, y increases upward.
# pdfplumber returns top-left origin (y increases downward) — we normalise.


class PDFVectorParser(BaseParser):

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def parse(self, file_path: Path) -> list[DrawingSheet]:
        if not PDFPLUMBER_AVAILABLE:
            sheet = DrawingSheet(source_file=str(file_path))
            sheet.errors.append("pdfplumber not installed")
            return [sheet]

        self._reset_logs()
        sheets: list[DrawingSheet] = []

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    sheet = self._parse_page(page, file_path, page_num)
                    sheets.append(sheet)
        except Exception as exc:
            sheet = DrawingSheet(source_file=str(file_path))
            sheet.errors.append(f"pdfplumber error: {exc}")
            return [sheet]

        return sheets

    # ── Page parser ───────────────────────────────────────────────────────────

    def _parse_page(
        self, page: Any, file_path: Path, page_num: int
    ) -> DrawingSheet:
        sheet = DrawingSheet(
            source_file=str(file_path),
            page_number=page_num,
        )

        pw = float(page.width  or 1)
        ph = float(page.height or 1)

        # ── 1. Text extraction ────────────────────────────────────────────
        words = page.extract_words(
            x_tolerance=3, y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=False,
        ) or []

        all_text_strings: list[str] = []
        for w in words:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            te = TextEntity(
                text=text,
                x=float(w.get("x0", 0)),
                y=ph - float(w.get("top", 0)),   # flip Y
                height=float(w.get("height", 0)),
                page=page_num,
            )
            sheet.texts.append(te)
            all_text_strings.append(text)

        # ── 2. Vector line extraction ─────────────────────────────────────
        lines_raw = page.lines or []
        rects_raw = page.rects or []

        all_lines_raw = list(lines_raw)
        # Decompose rects into 4 edge lines
        for rect in rects_raw:
            x0, y0, x1, y1 = (
                float(rect.get("x0", 0)), float(rect.get("top", 0)),
                float(rect.get("x1", 0)), float(rect.get("bottom", 0)),
            )
            all_lines_raw.extend([
                {"x0": x0, "y0": y0, "x1": x1, "y1": y0},
                {"x0": x1, "y0": y0, "x1": x1, "y1": y1},
                {"x0": x1, "y0": y1, "x1": x0, "y1": y1},
                {"x0": x0, "y0": y1, "x1": x0, "y1": y0},
            ])

        for ln in all_lines_raw:
            x0 = float(ln.get("x0", 0))
            y0 = ph - float(ln.get("y0", ln.get("top",    0)))
            x1 = float(ln.get("x1", 0))
            y1 = ph - float(ln.get("y1", ln.get("bottom", 0)))
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length < 1:
                continue
            seg = LineSegment(
                start=Point2D(x0, y0),
                end=Point2D(x1, y1),
                layer="",
                length=length,
            )
            sheet.lines.append(seg)

        # ── 3. Raster fallback check ──────────────────────────────────────
        if len(sheet.lines) < _RASTER_LINE_THRESHOLD:
            sheet.warnings.append(
                f"Page {page_num}: only {len(sheet.lines)} vector lines — "
                "raster pipeline recommended."
            )

        # ── 4. Scale detection ────────────────────────────────────────────
        # Title block = bottom-right 25%
        tb_texts = [
            te.text for te in sheet.texts
            if te.x >= pw * (1 - _TITLE_BLOCK_FRACTION)
            and te.y <= ph * _TITLE_BLOCK_FRACTION
        ]
        scale_result = detect_scale(
            text_blocks=all_text_strings,
            drawing_unit="pt",   # PDF points; mm_per_unit corrected below
        )

        # PDF points → mm  (1 pt = 0.3528 mm)
        if scale_result.mm_per_unit == 1.0:
            scale_result.mm_per_unit = 0.3528 / max(scale_result.scale_denominator, 1)

        sheet.scale_result = scale_result

        # ── 5. Title block ────────────────────────────────────────────────
        tb = parse_from_text(
            tb_texts or all_text_strings[:40],
            drawing_unit="pt",
        )
        sheet.titleblock = tb

        # ── 6. Dimension detection from geometry + text proximity ─────────
        sheet.dimensions.extend(
            self._detect_dimensions(sheet, ph)
        )

        # ── 7. Sheet classification ───────────────────────────────────────
        classification = classify_sheet(
            all_text_strings, sheet_title=tb.sheet_title
        )
        sheet.sheet_type = classification.sheet_type

        return sheet

    # ── Dimension detection ────────────────────────────────────────────────────

    def _detect_dimensions(
        self, sheet: DrawingSheet, page_height: float
    ) -> list[DimensionEntity]:
        """
        For each numeric text match, find the nearest parallel line pair
        whose geometric separation matches the text value.
        High confidence if geometry confirms; lower if text-only.
        """
        dims: list[DimensionEntity] = []
        mm_per_pt = sheet.scale_result.mm_per_unit if sheet.scale_result else 0.3528

        for te in sheet.texts:
            matches = extract_text_matches(te.text)
            for tm in matches:
                if not tm.mm_values:
                    continue
                value_mm = tm.primary_mm
                if not value_mm or value_mm <= 0:
                    continue

                # Check geometry confirmation
                geo_conf = self._confirm_with_geometry(
                    te.x, te.y, value_mm, sheet.lines, mm_per_pt
                )

                base_conf = 0.80 if geo_conf else 0.65

                dims.append(DimensionEntity(
                    value_mm=value_mm,
                    raw_text=te.text,
                    dim_type="LINEAR",
                    x=te.x,
                    y=te.y,
                    page=te.page,
                    layer="",
                ))

        return dims

    def _confirm_with_geometry(
        self,
        tx: float, ty: float,
        value_mm: float,
        lines: list[LineSegment],
        mm_per_pt: float,
        radius: float = 200.0,
        tolerance: float = 0.05,
    ) -> bool:
        """Return True if a nearby line pair matches value_mm."""
        nearby = [
            ln for ln in lines
            if math.hypot(
                (ln.start.x + ln.end.x) / 2 - tx,
                (ln.start.y + ln.end.y) / 2 - ty,
            ) <= radius
        ]
        for i, a in enumerate(nearby):
            for b in nearby[i + 1:]:
                sep = self._separation(a, b)
                if sep is None:
                    continue
                sep_mm = sep * mm_per_pt
                if abs(sep_mm - value_mm) / max(value_mm, 1) <= tolerance:
                    return True
        return False

    @staticmethod
    def _separation(a: LineSegment, b: LineSegment) -> Optional[float]:
        if a.orientation == "HORIZONTAL" and b.orientation == "HORIZONTAL":
            return abs((a.start.y + a.end.y) / 2 - (b.start.y + b.end.y) / 2)
        if a.orientation == "VERTICAL" and b.orientation == "VERTICAL":
            return abs((a.start.x + a.end.x) / 2 - (b.start.x + b.end.x) / 2)
        return None
