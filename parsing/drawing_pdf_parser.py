"""
drawing_pdf_parser.py
─────────────────────
Universal hybrid PDF parser for ALL document types.

Auto-detects per page:
  • Text pages (≥100 extractable chars) → PyMuPDF span extraction + pdfplumber tables
  • Image pages (<100 chars) → Gemini Vision with adaptive prompt (drawings OR scanned text)

The adaptive vision prompt first identifies the page type (technical drawing,
scanned text, table, form, etc.) then extracts content accordingly — so
scanned specs aren't misinterpreted as drawings and vice versa.

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

# Pages with fewer extractable chars than this are treated as image pages
MIN_TEXT_CHARS = 100
# DPI for rendering image pages — 200 gives clear annotations for dimensions/notes
RENDER_DPI = 200
# Max image pages processed via vision per document (cost/time guard)
MAX_VISION_PAGES = 150
# Parallel vision processing
VISION_BATCH_SIZE = 10   # pages rendered at once (memory guard: ~10 PNGs × 3MB = ~30MB)
VISION_WORKERS = 10      # concurrent Vision API calls within a batch
# Parallel table extraction (pdfplumber is slow per page, ~200-500ms)
TABLE_WORKERS = 4        # concurrent pdfplumber extract_tables calls
# Vision model — must be kept in sync with available Gemini models
VISION_MODEL = "gemini-2.5-flash"
# Skip pdfplumber table extraction on very large PDFs (too slow)
SKIP_TABLES_ABOVE_PAGES = 300

# ── Adaptive Vision Prompt ───────────────────────────────────────────────────
# This prompt handles ANY page type: drawings, scanned text, tables, forms, etc.
# The model first identifies what it's looking at, then extracts accordingly.

_VISION_PROMPT = """You are an expert document analyst specializing in construction, facade, and curtain wall engineering documents.

STEP 1 — IDENTIFY the page type. This page could be ANY of:
  A) Technical/architectural DRAWING (plans, elevations, sections, details)
  B) SCANNED TEXT document (printed/typed specifications, contracts, conditions)
  C) TABLE or SCHEDULE (tabular data — BOQ, quantities, material schedules, price lists)
  D) FORM or DATASHEET (structured form with fields and values)
  E) COVER PAGE, INDEX, or TABLE OF CONTENTS
  F) BLANK or nearly blank page

STEP 2 — EXTRACT based on what you see:

═══ If DRAWING (type A): ═══
Extract ALL technical information. Be extremely thorough — read EVERY annotation, dimension, note, label, callout:
1. TITLE BLOCK — Drawing title, project name, drawing number, sheet number, scale, date, revision, consultant
2. DIMENSIONS — ALL measurements: heights, widths, floor-to-floor, sill heights, bay spacing, mullion/transom spacing, profile depths, glass sizes, panel sizes, opening sizes. Include number and unit (e.g. "3200 mm")
3. MATERIAL CALLOUTS — Glass type & thickness (e.g. "10mm+16mm gap+10mm DGU Low-E"), aluminium alloys (6063-T6), finishes (anodized, PVDF), sealant types
4. FACADE SYSTEM — System designation, mullion/transom labels, Schuco/Reynaers/Aluprof series, profile references
5. PERFORMANCE DATA — Wind loads, U-values, acoustic ratings (STC/Rw), fire ratings, water/air tightness class
6. PROFILE DETAILS — Face widths, sight lines, profile depths, structural member sizes, stack/expansion joints
7. NOTES & LEGENDS — ALL general notes, abbreviations, material legends, spec references, standards (IS, EN, BS, ASTM)
8. DETAIL REFERENCES — Section marks (A/101), detail callouts, elevation markers, grid lines
9. OPENINGS — Door/window types, opening mechanisms (fixed/casement/awning/tilt-turn/sliding), hardware notes, louvers
10. SEALING & DRAINAGE — Sealant positions, weep holes, drainage, gasket types, back-up rod, sealant bite dimensions

═══ If SCANNED TEXT (type B): ═══
Transcribe ALL text on the page accurately. Preserve:
- Section/clause numbers and headings
- All technical specifications, requirements, and performance criteria
- Material specifications, standards references, and test methods
- Any numerical values, ranges, tolerances, and units
- Bullet points, numbered lists, and paragraph structure
Format with clear headings and preserve the document's logical structure.

═══ If TABLE/SCHEDULE (type C): ═══
Extract the complete table data:
- Column headers first
- Then each row with all cell values
- Preserve item numbers, descriptions, quantities, units, rates, amounts
- Note any subtotals, totals, or summary rows
Format as structured text with "|" separators between columns.

═══ If FORM/DATASHEET (type D): ═══
Extract all field labels and their values as "Field: Value" pairs.
Include every filled field, checkbox state, and any notes.

═══ If COVER/INDEX/BLANK (type E or F): ═══
Respond with exactly: PAGE TYPE: Cover/Index/Blank — no technical content

Begin your response with "PAGE TYPE: [A/B/C/D/E/F]" on the first line, then the extracted content.
Include ALL numbers, dimensions, codes, and text — do not summarize or skip values.
"""


class DrawingPDFParser:
    """Universal hybrid PDF parser: text extraction + tables + Gemini Vision."""

    def __init__(self):
        self.gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    def parse(self, pdf_path: str) -> List[Dict]:
        blocks, _, _ = self.parse_with_page_count(pdf_path)
        return blocks

    def parse_with_page_count(self, pdf_path: str) -> Tuple[List[Dict], int, Dict]:
        blocks: List[Dict] = []
        current_section: str | None = None
        current_subsection: str | None = None
        vision_count = 0
        vision_skipped = 0

        # ── Open PDF with password/corruption handling ───────────────────
        try:
            fitz_doc = fitz.open(pdf_path)
        except Exception as open_err:
            err_msg = str(open_err).lower()
            if "password" in err_msg or "encrypted" in err_msg:
                logger.error(
                    f"[HYBRID_PDF] Cannot open '{pdf_path}': PDF is password-protected. "
                    f"Please provide an unprotected version."
                )
                return [], 0, {"text_pages": 0, "vision_pages": 0, "skipped_pages": 0,
                               "error": "password_protected"}
            logger.error(f"[HYBRID_PDF] Cannot open '{pdf_path}': {open_err}")
            return [], 0, {"text_pages": 0, "vision_pages": 0, "skipped_pages": 0,
                           "error": str(open_err)}

        if fitz_doc.is_encrypted:
            # Try empty password (some PDFs have owner-password but no user-password)
            if not fitz_doc.authenticate(""):
                logger.error(
                    f"[HYBRID_PDF] '{pdf_path}' is encrypted and requires a password. "
                    f"Please provide an unprotected version."
                )
                fitz_doc.close()
                return [], 0, {"text_pages": 0, "vision_pages": 0, "skipped_pages": 0,
                               "error": "password_protected"}
            logger.info(f"[HYBRID_PDF] '{pdf_path}': encrypted but no user password — opened OK")

        total_pages = len(fitz_doc)
        skip_tables = total_pages > SKIP_TABLES_ABOVE_PAGES
        logger.info(
            f"[HYBRID_PDF] {pdf_path}: {total_pages} pages "
            f"(tables={'skip' if skip_tables else 'extract'})"
        )

        # Track vision page types for stats
        vision_drawing_count = 0
        vision_scanned_count = 0
        vision_table_count = 0

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
                f"[HYBRID_PDF] Classification: {len(text_page_indices)} text pages, "
                f"{len(vision_page_indices)} vision pages, {vision_skipped} skipped"
            )

            # ── Phase 2: Process text pages (spans + parallel tables) ───────
            # Text span extraction via fitz is fast (~1ms/page) and must be
            # sequential (tracks current_section/subsection state).
            # Table extraction via pdfplumber is SLOW (~200-500ms/page) and
            # stateless — parallelized with ThreadPoolExecutor.

            for page_idx in text_page_indices:
                fitz_page = fitz_doc[page_idx]
                blocks, current_section, current_subsection = _extract_text_spans(
                    fitz_page, page_idx + 1, blocks, current_section, current_subsection,
                    source_type="pdf_spec",
                )

            # Record section context per page for table blocks
            _page_sections = {}
            _cur_sec, _cur_sub = None, None
            for b in blocks:
                pg = b.get("page")
                if b.get("is_heading"):
                    if b.get("font_size", 0) > 14:
                        _cur_sec = b["text"]
                        _cur_sub = None
                    else:
                        _cur_sub = b["text"]
                if pg:
                    _page_sections[pg] = (_cur_sec, _cur_sub)

            # Parallel table extraction
            if not skip_tables and text_page_indices:
                table_blocks = _extract_tables_parallel(
                    pdf_path, text_page_indices, _page_sections
                )
                blocks.extend(table_blocks)
                if table_blocks:
                    logger.info(
                        f"[HYBRID_PDF] Extracted {len(table_blocks)} table blocks "
                        f"from {len(text_page_indices)} text pages"
                    )

            # ── Phase 3: Process vision pages in parallel batches ────────────
            vision_results: Dict[int, Tuple[str, str]] = {}  # idx → (page_type, text)

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
                            raw_text = future.result()
                            page_type, content = _parse_vision_response(raw_text)
                            vision_results[idx] = (page_type, content)
                        except Exception as e:
                            logger.warning(f"[HYBRID_PDF] Vision failed page {idx + 1}: {e}")
                            vision_results[idx] = ("error", "")

                # 3c: Free rendered PNGs for this batch
                del rendered

                done_so_far = min(batch_start + VISION_BATCH_SIZE, len(vision_page_indices))
                logger.info(
                    f"[HYBRID_PDF] Vision batch done: {done_so_far}/{len(vision_page_indices)} pages"
                )

            # ── Phase 4: Build blocks from vision results in page order ──────
            for page_idx in sorted(vision_results.keys()):
                page_type, text = vision_results[page_idx]
                page_num = page_idx + 1

                if page_type in ("E", "F", "error") or not text:
                    logger.info(f"[HYBRID_PDF] Page {page_num}: {page_type} — skipped")
                    continue

                # Determine source_type based on what Vision detected
                if page_type == "A":
                    source_type = "pdf_drawing"
                    section_label = f"Drawing Sheet {page_num}"
                    vision_drawing_count += 1
                elif page_type == "B":
                    source_type = "pdf_spec"
                    section_label = f"Scanned Text Page {page_num}"
                    vision_scanned_count += 1
                elif page_type in ("C", "D"):
                    source_type = "pdf_spec"
                    section_label = f"Table/Schedule Page {page_num}"
                    vision_table_count += 1
                else:
                    source_type = "pdf_spec"
                    section_label = f"Page {page_num}"

                blocks.append({
                    "type":        "text",
                    "text":        text,
                    "page":        page_num,
                    "section":     section_label,
                    "subsection":  None,
                    "font_size":   None,
                    "is_heading":  False,
                    "source_type": source_type,
                })
                logger.info(
                    f"[HYBRID_PDF] Page {page_num}: vision type={page_type} → {len(text)} chars"
                )

        finally:
            fitz_doc.close()

        if vision_skipped > 0:
            logger.warning(
                f"[HYBRID_PDF] {vision_skipped} page(s) skipped — cap of {MAX_VISION_PAGES} reached. "
                f"Raise MAX_VISION_PAGES in drawing_pdf_parser.py to process all pages."
            )

        stats = {
            "text_pages": len(text_page_indices),
            "vision_pages": len(vision_page_indices),
            "skipped_pages": vision_skipped,
            "vision_drawings": vision_drawing_count,
            "vision_scanned": vision_scanned_count,
            "vision_tables": vision_table_count,
        }
        logger.info(
            f"[HYBRID_PDF] Done: {len(blocks)} blocks from {total_pages} pages "
            f"(text:{stats['text_pages']} vision:{stats['vision_pages']} "
            f"[drawings:{vision_drawing_count} scanned:{vision_scanned_count} "
            f"tables:{vision_table_count}] skipped:{vision_skipped})"
        )
        return blocks, total_pages, stats

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=5, max=30),
        stop=stop_after_attempt(3),
        before_sleep=lambda rs: logger.warning(
            f"[HYBRID_PDF] Vision retry {rs.attempt_number}: {rs.outcome.exception()}"
        ),
    )
    def _call_vision_with_retry(self, img_bytes: bytes, page_num: int) -> str:
        """Single Vision API call — retried up to 3x by tenacity. Thread-safe."""
        response = self.gemini.models.generate_content(
            model=VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                types.Part.from_text(_VISION_PROMPT),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
            ),
        )
        return response.text or ""


def _extract_tables_parallel(
    pdf_path: str,
    page_indices: List[int],
    page_sections: Dict[int, tuple],
) -> List[Dict]:
    """Extract tables from multiple pages in parallel using pdfplumber.

    pdfplumber's find_tables() + extract_tables() is the slowest per-page
    operation (~200-500ms/page). Since each page is independent, we
    parallelize across TABLE_WORKERS threads.

    Returns table blocks sorted by page number.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("[HYBRID_PDF] pdfplumber not installed — tables skipped")
        return []

    def _extract_page_tables(page_idx: int) -> List[Dict]:
        """Extract tables from a single page. Thread-safe (each opens its own PDF)."""
        page_num = page_idx + 1
        try:
            # Each thread opens its own pdfplumber instance (thread-safe)
            with pdfplumber.open(pdf_path) as plumb:
                if page_idx >= len(plumb.pages):
                    return []
                plumb_page = plumb.pages[page_idx]
                found = plumb_page.find_tables()
                if not found:
                    return []

                tables = plumb_page.extract_tables() or []
                section, subsection = page_sections.get(page_num, (None, None))
                results = []
                for table_idx, table in enumerate(tables):
                    if not table:
                        continue
                    rows_text = [
                        " | ".join(cell.strip() if cell else "" for cell in row)
                        for row in table
                    ]
                    table_text = "\n".join(rows_text).strip()
                    if table_text:
                        results.append({
                            "type":        "table",
                            "text":        table_text,
                            "page":        page_num,
                            "section":     section,
                            "subsection":  subsection,
                            "font_size":   None,
                            "is_heading":  False,
                            "table_index": table_idx,
                            "source_type": "pdf_spec",
                        })
                return results
        except Exception as e:
            logger.warning(f"[HYBRID_PDF] Table extraction failed page {page_num}: {e}")
            return []

    all_table_blocks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=TABLE_WORKERS) as executor:
        futures = {
            executor.submit(_extract_page_tables, idx): idx
            for idx in page_indices
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                all_table_blocks.extend(result)

    # Sort by page number for consistent ordering
    all_table_blocks.sort(key=lambda b: (b["page"], b.get("table_index", 0)))
    return all_table_blocks


def _parse_vision_response(raw_text: str) -> Tuple[str, str]:
    """Parse the Vision response to extract page type and content.

    Expected format: "PAGE TYPE: X\n<content>"
    Returns (page_type_letter, content_text).
    """
    if not raw_text:
        return ("F", "")

    text = raw_text.strip()

    # Check for cover/blank indicators
    text_lower = text.lower()
    if "cover/index/blank" in text_lower or "no technical content" in text_lower:
        return ("E", "")

    # Extract page type from first line
    page_type = "A"  # default to drawing for backward compatibility
    content = text

    first_line = text.split("\n", 1)[0].strip()
    if first_line.upper().startswith("PAGE TYPE:"):
        type_part = first_line.split(":", 1)[1].strip()
        # Extract the letter (A/B/C/D/E/F)
        if type_part and type_part[0].upper() in "ABCDEF":
            page_type = type_part[0].upper()

        # Content is everything after the first line
        if "\n" in text:
            content = text.split("\n", 1)[1].strip()
        else:
            content = ""

    # If detected as cover/blank, return empty
    if page_type in ("E", "F"):
        return (page_type, "")

    return (page_type, content)


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
        logger.warning(f"[HYBRID_PDF] Span extraction failed page {page_num}: {e}")
    return blocks, current_section, current_subsection
