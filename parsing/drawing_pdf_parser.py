"""
drawing_pdf_parser.py
─────────────────────
Hybrid parser: text extraction for spec pages, Gemini Vision for drawing pages.
Auto-detects per page — pages with < MIN_TEXT_CHARS extractable chars are
rendered to PNG and sent to Gemini Vision for annotation extraction.

Vision pages are processed in parallel batches for speed (~4x faster than
sequential processing on a 50-page drawing set).
"""
from __future__ import annotations

import concurrent.futures
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
# DPI for rendering drawing pages — 200 gives clear annotations for dimensions/notes
RENDER_DPI = 200
# Max drawing pages processed via vision per document (cost/time guard)
MAX_VISION_PAGES = 150
# Parallel vision processing
VISION_BATCH_SIZE = 10   # pages rendered at once (memory guard: ~10 PNGs × 3MB = ~30MB)
VISION_WORKERS = 8       # concurrent Vision API calls within a batch
# Vision model — must be kept in sync with available Gemini models
VISION_MODEL = "gemini-2.5-flash"

_DRAWING_PROMPT = """You are an expert facade/curtain wall engineer analysing a technical architectural drawing sheet.

Extract ALL technical information visible on this drawing. Be extremely thorough — read EVERY annotation, dimension line, note, label, and callout. Even small text matters.

Cover every category you find:

1. TITLE BLOCK — Drawing title, project name, drawing number, sheet number, scale, date, revision, consultant name
2. DIMENSIONS — ALL annotated measurements: overall heights/widths, floor-to-floor heights, sill heights, bay spacing, mullion/transom spacing, profile depths, glass sizes, panel sizes, opening sizes. Include the number and unit (e.g. "3200 mm", "1500 x 2400 mm")
3. MATERIAL CALLOUTS — Glass type & thickness (e.g. "10mm+16mm gap+10mm DGU Low-E"), aluminium alloy codes (e.g. 6063-T6), finish specs (anodized, PVDF, powder coated), sealant types (structural, weather)
4. FACADE SYSTEM — System designation (curtain wall, stick, unitised, window wall), mullion/transom labels, Schuco/Reynaers/Aluprof series codes, profile references
5. PERFORMANCE DATA — Wind load values, U-values, acoustic ratings (STC/Rw), fire ratings, water tightness class, air permeability class visible on drawing or in notes
6. PROFILE DETAILS — Face widths of mullions/transoms, sight lines, profile depths, structural member sizes, stack joint details, expansion joint details
7. NOTES & LEGENDS — ALL general notes, abbreviation keys, material legends, specification references, standard references (IS, EN, BS, ASTM)
8. DETAIL REFERENCES — Section marks (e.g. A/101), detail callouts, elevation markers, grid lines and labels
9. OPENINGS — Door/window types & designations, opening types (fixed/casement/awning/tilt-turn/sliding), hardware notes, louver details
10. SEALING & DRAINAGE — Sealant positions, weep holes, drainage paths, gasket types, back-up rod details, sealant bite dimensions

Format as structured text with a clear heading for each category. Include ALL numbers, dimensions, and codes — do not summarize or skip values.
If the page is blank, a cover sheet, or an index/table of contents with no technical drawing content, respond with exactly:
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
        vision_skipped = 0

        fitz_doc = fitz.open(pdf_path)
        total_pages = len(fitz_doc)
        logger.info(f"[DRAWING_PDF] {pdf_path}: {total_pages} pages")

        try:
            # ── Phase 1: Classify pages into text vs vision ──────────────────
            text_page_indices = []
            vision_page_indices = []

            for page_idx in range(total_pages):
                fitz_page = fitz_doc[page_idx]
                page_text = fitz_page.get_text("text").strip()
                if len(page_text) >= MIN_TEXT_CHARS:
                    text_page_indices.append(page_idx)
                elif vision_count < MAX_VISION_PAGES:
                    vision_page_indices.append(page_idx)
                    vision_count += 1
                else:
                    vision_skipped += 1

            logger.info(
                f"[DRAWING_PDF] Classification: {len(text_page_indices)} text pages, "
                f"{len(vision_page_indices)} vision pages, {vision_skipped} skipped"
            )

            # ── Phase 2: Process text pages sequentially (fast, CPU-only) ────
            for page_idx in text_page_indices:
                fitz_page = fitz_doc[page_idx]
                blocks, current_section, current_subsection = _extract_text_spans(
                    fitz_page, page_idx + 1, blocks, current_section, current_subsection,
                    source_type="pdf_spec",
                )

            # ── Phase 3: Process vision pages in parallel batches ────────────
            vision_results: Dict[int, str] = {}

            for batch_start in range(0, len(vision_page_indices), VISION_BATCH_SIZE):
                batch_indices = vision_page_indices[batch_start:batch_start + VISION_BATCH_SIZE]

                # 3a: Render batch to PNG (main thread, fast ~50ms per page)
                rendered: Dict[int, bytes] = {}
                for page_idx in batch_indices:
                    fitz_page = fitz_doc[page_idx]
                    mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
                    pix = fitz_page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                    rendered[page_idx] = pix.tobytes("png")
                    del pix

                # 3b: Submit Vision API calls in parallel
                with concurrent.futures.ThreadPoolExecutor(max_workers=VISION_WORKERS) as executor:
                    future_to_idx = {
                        executor.submit(
                            self._call_vision_with_retry, rendered[idx], idx + 1
                        ): idx
                        for idx in batch_indices
                    }
                    for future in concurrent.futures.as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        try:
                            vision_results[idx] = future.result()
                        except Exception as e:
                            logger.warning(f"[DRAWING_PDF] Vision failed page {idx + 1}: {e}")
                            vision_results[idx] = ""

                # 3c: Free rendered PNGs for this batch
                del rendered

                done_so_far = min(batch_start + VISION_BATCH_SIZE, len(vision_page_indices))
                logger.info(
                    f"[DRAWING_PDF] Vision batch done: {done_so_far}/{len(vision_page_indices)} pages"
                )

            # ── Phase 4: Build blocks from vision results in page order ──────
            for page_idx in sorted(vision_results.keys()):
                text = vision_results[page_idx]
                page_num = page_idx + 1
                if text and "Cover/Index" not in text:
                    blocks.append({
                        "type":        "text",
                        "text":        text,
                        "page":        page_num,
                        "section":     f"Drawing Sheet {page_num}",
                        "subsection":  None,
                        "font_size":   None,
                        "is_heading":  False,
                        "source_type": "pdf_drawing",
                    })
                    logger.info(
                        f"[DRAWING_PDF] Page {page_num}: vision -> {len(text)} chars"
                    )
                else:
                    logger.info(f"[DRAWING_PDF] Page {page_num}: cover/blank — skipped")

        finally:
            fitz_doc.close()

        if vision_skipped > 0:
            logger.warning(
                f"[DRAWING_PDF] {vision_skipped} drawing page(s) skipped — cap of {MAX_VISION_PAGES} reached. "
                f"Raise MAX_VISION_PAGES in drawing_pdf_parser.py to process all pages."
            )
        logger.info(
            f"[DRAWING_PDF] Done: {len(blocks)} blocks from {total_pages} pages "
            f"({vision_count} via vision, {vision_skipped} skipped)"
        )
        return blocks, total_pages

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=5, max=30),
        stop=stop_after_attempt(3),
        before_sleep=lambda rs: logger.warning(
            f"[DRAWING_PDF] Vision retry {rs.attempt_number}: {rs.outcome.exception()}"
        ),
    )
    def _call_vision_with_retry(self, img_bytes: bytes, page_num: int) -> str:
        """Single Vision API call — retried up to 3x by tenacity. Thread-safe."""
        response = self.gemini.models.generate_content(
            model=VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                types.Part.from_text(_DRAWING_PROMPT),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
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
    """Span-extraction logic shared with PDFParser."""
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
                        "source_type": source_type,
                    })
    except Exception as e:
        logger.warning(f"[DRAWING_PDF] Span extraction failed page {page_num}: {e}")
    return blocks, current_section, current_subsection
