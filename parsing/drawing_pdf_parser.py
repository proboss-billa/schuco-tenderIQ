"""
drawing_pdf_parser.py
─────────────────────
Hybrid parser: text extraction for spec pages, Gemini Vision for drawing pages.
Auto-detects per page — pages with < MIN_TEXT_CHARS extractable chars are
rendered to PNG and sent to Gemini Vision for annotation extraction.
"""
from __future__ import annotations

import logging
import os
from typing import List, Dict, Tuple

import fitz  # PyMuPDF
from google import genai
from google.genai import types
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

logger = logging.getLogger(__name__)

# Pages with fewer extractable chars than this are treated as drawing images
MIN_TEXT_CHARS = 100
# DPI for rendering drawing pages — 150 gives legible annotations without huge memory
RENDER_DPI = 150
# Max drawing pages processed via vision per document (cost/time guard)
MAX_VISION_PAGES = 80

_DRAWING_PROMPT = """You are an expert facade/curtain wall engineer analysing a technical architectural drawing sheet.

Extract ALL technical information visible. Be thorough and precise.

Cover every category you find:
1. TITLE BLOCK — Drawing title, project name, drawing number, sheet number, scale, date, revision
2. DIMENSIONS — All annotated measurements: heights, widths, bay spacing, sill heights, mullion/transom spacing, depths
3. MATERIAL CALLOUTS — Glass type & thickness, aluminium alloy codes, finish specifications, sealant types
4. FACADE SYSTEM — System designation (curtain wall, stick, unitised, etc.), mullion/transom labels, series/product codes
5. PERFORMANCE SPECS — Wind load values, U-values, acoustic ratings, fire ratings, water tightness classes visible on drawing
6. NOTES & LEGENDS — General notes, abbreviation keys, material legends, specification references
7. DETAIL REFERENCES — Section marks (e.g. A/101), detail callouts, elevation markers, grid labels
8. OPENINGS — Door/window designations, opening types (fixed/vent/tilt-turn), hardware notes

Format as structured text with a heading for each category that has content. Skip empty categories.
If the page is blank, a cover sheet, or an index with no technical drawing content, respond with exactly:
PAGE TYPE: Cover/Index — no technical content
"""


class DrawingPDFParser:
    """Hybrid PDF parser: standard text extraction + Gemini Vision for image pages."""

    def __init__(self):
        self.gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    def parse(self, pdf_path: str) -> List[Dict]:
        blocks, _ = self.parse_with_page_count(pdf_path)
        return blocks

    def parse_with_page_count(self, pdf_path: str) -> Tuple[List[Dict], int]:
        blocks: List[Dict] = []
        current_section: str | None = None
        current_subsection: str | None = None
        vision_count = 0

        fitz_doc = fitz.open(pdf_path)
        total_pages = len(fitz_doc)
        logger.info(f"[DRAWING_PDF] {pdf_path}: {total_pages} pages")

        try:
            for page_idx in range(total_pages):
                page_num = page_idx + 1
                fitz_page = fitz_doc[page_idx]
                try:
                    page_text = fitz_page.get_text("text").strip()
                    if len(page_text) >= MIN_TEXT_CHARS:
                        # ── Text page: standard span extraction ──────────────
                        # source_type = 'pdf_spec' so parameter search routing
                        # treats these chunks the same as a text specification.
                        blocks, current_section, current_subsection = _extract_text_spans(
                            fitz_page, page_num, blocks, current_section, current_subsection,
                            source_type="pdf_spec",
                        )
                    elif vision_count < MAX_VISION_PAGES:
                        # ── Drawing page: render + vision ─────────────────────
                        # source_type = 'pdf_drawing' so parameter search routing
                        # prioritises these chunks for Tender Drawing parameters.
                        vision_text = self._vision_extract(fitz_page, page_num)
                        vision_count += 1
                        if vision_text and "Cover/Index" not in vision_text:
                            blocks.append({
                                "type":        "text",
                                "text":        vision_text,
                                "page":        page_num,
                                "section":     f"Drawing Sheet {page_num}",
                                "subsection":  None,
                                "font_size":   None,
                                "is_heading":  False,
                                "source_type": "pdf_drawing",   # ← drawing chunk
                            })
                            logger.info(
                                f"[DRAWING_PDF] Page {page_num}: vision → {len(vision_text)} chars"
                            )
                        else:
                            logger.info(f"[DRAWING_PDF] Page {page_num}: cover/blank — skipped")
                    else:
                        logger.info(
                            f"[DRAWING_PDF] Page {page_num}: vision cap ({MAX_VISION_PAGES}) reached — skipped"
                        )
                finally:
                    fitz_page = None
        finally:
            fitz_doc.close()

        logger.info(
            f"[DRAWING_PDF] Done: {len(blocks)} blocks from {total_pages} pages "
            f"({vision_count} via vision)"
        )
        return blocks, total_pages

    def _vision_extract(self, fitz_page, page_num: int) -> str:
        """Render one page to PNG and call Gemini Vision (with retry)."""
        try:
            mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
            pix = fitz_page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img_bytes = pix.tobytes("png")
            del pix
            try:
                return self._call_vision_with_retry(img_bytes, page_num)
            finally:
                del img_bytes
        except Exception as e:
            logger.warning(f"[DRAWING_PDF] Vision failed page {page_num} after retries: {e}")
            return ""

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=5, max=30),
        stop=stop_after_attempt(3),
        before_sleep=lambda rs: logger.warning(
            f"[DRAWING_PDF] Vision retry {rs.attempt_number}: {rs.outcome.exception()}"
        ),
    )
    def _call_vision_with_retry(self, img_bytes: bytes, page_num: int) -> str:
        """Single Vision API call — retried up to 3× by tenacity."""
        response = self.gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                types.Part.from_text(_DRAWING_PROMPT),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
            ),
        )
        return response.text or ""


def _extract_text_spans(
    fitz_page, page_num: int,
    blocks: List[Dict],
    current_section: str | None,
    current_subsection: str | None,
    source_type: str = "pdf_spec",
) -> Tuple[List[Dict], str | None, str | None]:
    """Span-extraction logic shared with PDFParser.

    source_type is stored on every block so _store_chunks() can use it as the
    Pinecone 'file_type' metadata field, enabling per-parameter search routing.
    """
    try:
        text_dict = fitz_page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    font_size  = span.get("size", 0)
                    is_bold    = "bold" in span.get("font", "").lower()
                    is_heading = is_bold and font_size > 11
                    if is_heading:
                        if font_size > 14:
                            current_section    = text
                            current_subsection = None
                        else:
                            current_subsection = text
                    blocks.append({
                        "type":        "text",
                        "text":        text,
                        "page":        page_num,
                        "section":     current_section,
                        "subsection":  current_subsection,
                        "font_size":   font_size,
                        "is_heading":  is_heading,
                        "source_type": source_type,   # ← routing metadata
                    })
    except Exception as e:
        logger.warning(f"[DRAWING_PDF] Span extraction failed page {page_num}: {e}")
    return blocks, current_section, current_subsection
