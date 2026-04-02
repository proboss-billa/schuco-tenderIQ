"""
pdf_raster_parser.py
────────────────────
Priority-2b parser.  Triggered when pdfplumber finds < 20 vector lines.

Pipeline:
  1. Rasterise PDF pages at 300 DPI (pdf2image / PyMuPDF)
  2. Preprocess: grayscale → CLAHE → bilateral filter → adaptive threshold
  3. Line detection: HoughLinesP → cluster → merge
  4. Dimension line detection: tick-mark pairs + arrowhead contours
  5. OCR: pytesseract on cropped text regions
  6. Scale detection from title block region
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional, Any

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

try:
    import fitz   # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False

try:
    from sklearn.cluster import DBSCAN
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

from parsers.base_parser import (
    BaseParser, DrawingSheet, LineSegment, TextEntity,
    DimensionEntity, Point2D,
)
from classifiers.scale_extractor import detect_scale
from classifiers.sheet_classifier import classify_sheet
from classifiers.titleblock_parser import parse_from_text
from extractors.text_extractor import extract_text_matches, correct_ocr_text

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DPI              = 300
_ANGLE_TOL        = 3.0       # degrees
_HOUGH_THRESHOLD  = 80
_MIN_LINE_LEN     = 50        # pixels
_MAX_LINE_GAP     = 10        # pixels
_OCR_PAD          = 30        # pixels around dimension line for OCR crop
_TITLE_BLOCK_FRAC = 0.25      # bottom-right 25% of image


class PDFRasterParser(BaseParser):

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def parse(self, file_path: Path) -> list[DrawingSheet]:
        self._reset_logs()

        images = self._rasterise(file_path)
        if not images:
            sheet = DrawingSheet(source_file=str(file_path))
            sheet.errors.append("Could not rasterise PDF (pdf2image and PyMuPDF unavailable)")
            return [sheet]

        sheets: list[DrawingSheet] = []
        for page_num, img_pil in enumerate(images, start=1):
            sheet = self._process_image(img_pil, file_path, page_num)
            sheets.append(sheet)

        return sheets

    # ── Rasterisation ─────────────────────────────────────────────────────────

    def _rasterise(self, file_path: Path) -> list[Any]:
        """Return list of PIL Images at 300 DPI."""
        if PDF2IMAGE_AVAILABLE:
            try:
                return convert_from_path(str(file_path), dpi=_DPI)
            except Exception as e:
                self._warn(f"pdf2image failed: {e}")

        if FITZ_AVAILABLE:
            try:
                doc = fitz.open(str(file_path))
                images = []
                from PIL import Image
                import io
                for page in doc:
                    mat = fitz.Matrix(_DPI / 72, _DPI / 72)
                    pix = page.get_pixmap(matrix=mat)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    images.append(img)
                return images
            except Exception as e:
                self._warn(f"PyMuPDF rasterise failed: {e}")

        return []

    # ── Page processing ────────────────────────────────────────────────────────

    def _process_image(
        self, img_pil: Any, file_path: Path, page_num: int
    ) -> DrawingSheet:
        sheet = DrawingSheet(
            source_file=str(file_path),
            page_number=page_num,
        )

        if not CV2_AVAILABLE:
            sheet.errors.append("OpenCV not available — raster pipeline disabled")
            return sheet

        # PIL → OpenCV
        import numpy as np
        from PIL import Image
        img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        h, w = img_cv.shape[:2]

        # ── 1. Preprocess ─────────────────────────────────────────────────
        gray      = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        clahe     = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced  = clahe.apply(gray)
        filtered  = cv2.bilateralFilter(enhanced, 9, 75, 75)
        binary    = cv2.adaptiveThreshold(
            filtered, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=35, C=10,
        )

        # ── 2. Line detection ─────────────────────────────────────────────
        raw_lines = cv2.HoughLinesP(
            binary,
            rho=1, theta=math.pi / 180,
            threshold=_HOUGH_THRESHOLD,
            minLineLength=_MIN_LINE_LEN,
            maxLineGap=_MAX_LINE_GAP,
        )

        if raw_lines is not None:
            lines = self._process_hough_lines(raw_lines, h)
            sheet.lines.extend(lines)

        # ── 3. OCR — full page for text entities ─────────────────────────
        all_text_strings: list[str] = []
        if TESSERACT_AVAILABLE:
            ocr_texts = self._ocr_page(gray)
            all_text_strings = [t.text for t in ocr_texts]
            sheet.texts.extend(ocr_texts)

        # ── 4. OCR — title block region ───────────────────────────────────
        tb_crop = gray[
            int(h * (1 - _TITLE_BLOCK_FRAC)):h,
            int(w * (1 - _TITLE_BLOCK_FRAC)):w,
        ]
        tb_texts: list[str] = []
        if TESSERACT_AVAILABLE:
            try:
                tb_raw = pytesseract.image_to_string(
                    tb_crop,
                    config="--oem 3 --psm 6",
                )
                tb_texts = [line.strip() for line in tb_raw.splitlines() if line.strip()]
            except Exception:
                pass

        # ── 5. Scale detection ────────────────────────────────────────────
        # mm per pixel at 300 DPI: 1 inch = 25.4 mm, 300 px/inch → 25.4/300
        mm_per_px = 25.4 / _DPI
        scale_result = detect_scale(
            text_blocks=tb_texts or all_text_strings[:50],
            drawing_unit="px",
        )
        scale_result.mm_per_unit = mm_per_px / max(scale_result.scale_denominator, 1)
        sheet.scale_result = scale_result

        # ── 6. Title block ────────────────────────────────────────────────
        tb = parse_from_text(tb_texts or all_text_strings[:40])
        sheet.titleblock = tb

        # ── 7. Dimension detection ────────────────────────────────────────
        dim_regions = self._find_dimension_regions(sheet.lines, h, w)
        for region_lines, cx, cy in dim_regions:
            if not TESSERACT_AVAILABLE:
                break
            crop = gray[
                max(0, cy - _OCR_PAD): min(h, cy + _OCR_PAD),
                max(0, cx - _OCR_PAD): min(w, cx + _OCR_PAD),
            ]
            crop_up = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2))
            # Sharpen
            blur    = cv2.GaussianBlur(crop_up, (0, 0), 3)
            sharp   = cv2.addWeighted(crop_up, 1.5, blur, -0.5, 0)
            _, bin_crop = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            ocr_raw = pytesseract.image_to_string(
                bin_crop,
                config="--oem 3 --psm 7",
            ).strip()
            ocr_corrected = correct_ocr_text(ocr_raw)

            matches = extract_text_matches(ocr_corrected, is_ocr=True)
            for tm in matches:
                if tm.mm_values:
                    de = DimensionEntity(
                        value_mm=tm.primary_mm,
                        raw_text=ocr_corrected,
                        dim_type="LINEAR",
                        x=float(cx) * mm_per_px,
                        y=float(cy) * mm_per_px,
                        page=page_num,
                    )
                    sheet.dimensions.append(de)

        # ── 8. Sheet classification ───────────────────────────────────────
        clf = classify_sheet(all_text_strings, sheet_title=tb.sheet_title)
        sheet.sheet_type = clf.sheet_type

        sheet.warnings.extend(self._warnings)
        sheet.errors.extend(self._errors)
        return sheet

    # ── HoughLines processing ─────────────────────────────────────────────────

    def _process_hough_lines(
        self, raw: Any, img_height: int
    ) -> list[LineSegment]:
        segments: list[LineSegment] = []
        for ln in raw:
            x0, y0, x1, y1 = int(ln[0][0]), int(ln[0][1]), int(ln[0][2]), int(ln[0][3])
            # Flip Y (OpenCV origin top-left → bottom-left)
            y0f, y1f = img_height - y0, img_height - y1
            dx, dy = x1 - x0, y1f - y0f
            length = math.hypot(dx, dy)
            if length < 1:
                continue
            seg = LineSegment(
                start=Point2D(float(x0), float(y0f)),
                end=Point2D(float(x1), float(y1f)),
                length=length,
            )
            segments.append(seg)

        # Optional DBSCAN clustering to merge collinear segments
        if SKLEARN_AVAILABLE and len(segments) > 10:
            segments = self._cluster_merge(segments)

        return segments

    @staticmethod
    def _cluster_merge(segs: list[LineSegment]) -> list[LineSegment]:
        """Cluster nearby collinear segments and merge them."""
        if not segs:
            return segs
        import numpy as np

        # Feature: midpoint + angle
        features = []
        for s in segs:
            mx = (s.start.x + s.end.x) / 2
            my = (s.start.y + s.end.y) / 2
            angle = math.degrees(math.atan2(s.end.y - s.start.y, s.end.x - s.start.x)) % 180
            features.append([mx, my, angle * 5])   # scale angle

        arr = np.array(features)
        db = DBSCAN(eps=20, min_samples=2).fit(arr)
        labels = db.labels_

        merged: list[LineSegment] = []
        for label in set(labels):
            if label == -1:
                # Noise — keep as-is
                for i, seg in enumerate(segs):
                    if labels[i] == -1:
                        merged.append(seg)
                continue
            cluster = [segs[i] for i in range(len(segs)) if labels[i] == label]
            if not cluster:
                continue
            # Merge: take bounding extremes
            xs = [s.start.x for s in cluster] + [s.end.x for s in cluster]
            ys = [s.start.y for s in cluster] + [s.end.y for s in cluster]
            seg = LineSegment(
                start=Point2D(min(xs), min(ys)),
                end=Point2D(max(xs), max(ys)),
            )
            merged.append(seg)

        return merged

    # ── OCR page ──────────────────────────────────────────────────────────────

    @staticmethod
    def _ocr_page(gray: Any) -> list[TextEntity]:
        """Run full-page OCR and return TextEntity list."""
        if not TESSERACT_AVAILABLE:
            return []
        try:
            data = pytesseract.image_to_data(
                gray,
                config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return []

        texts: list[TextEntity] = []
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            conf_raw = data.get("conf", [-1] * len(data["text"]))[i]
            try:
                ocr_conf = int(conf_raw) / 100.0
            except (TypeError, ValueError):
                ocr_conf = 0.5
            if ocr_conf < 0.2:
                continue

            te = TextEntity(
                text=correct_ocr_text(text),
                x=float(data["left"][i]),
                y=float(data["top"][i]),
                height=float(data["height"][i]),
            )
            texts.append(te)

        return texts

    # ── Dimension region detection ────────────────────────────────────────────

    @staticmethod
    def _find_dimension_regions(
        lines: list[LineSegment],
        img_h: int,
        img_w: int,
        tick_max_len: int = 30,
        search_radius: int = 50,
    ) -> list[tuple[list[LineSegment], int, int]]:
        """
        Detect likely dimension lines (long line with short tick marks at ends).
        Returns list of (constituent_lines, center_x, center_y).
        """
        short_lines = [ln for ln in lines if ln.length <= tick_max_len]
        long_lines  = [ln for ln in lines if ln.length > tick_max_len]

        regions: list[tuple[list[LineSegment], int, int]] = []

        for ll in long_lines:
            # Check if there are short lines near the endpoints (tick marks)
            lx0, ly0 = ll.start.x, ll.start.y
            lx1, ly1 = ll.end.x, ll.end.y

            near_start = any(
                math.hypot(sl.start.x - lx0, sl.start.y - ly0) <= search_radius
                or math.hypot(sl.end.x - lx0, sl.end.y - ly0) <= search_radius
                for sl in short_lines
            )
            near_end = any(
                math.hypot(sl.start.x - lx1, sl.start.y - ly1) <= search_radius
                or math.hypot(sl.end.x - lx1, sl.end.y - ly1) <= search_radius
                for sl in short_lines
            )

            if near_start and near_end:
                cx = int((lx0 + lx1) / 2)
                cy = int(img_h - (ly0 + ly1) / 2)   # flip back for cv2 coords
                regions.append(([ll], cx, cy))

        return regions
