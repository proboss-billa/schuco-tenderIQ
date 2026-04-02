"""
pdf_parser.py
─────────────
Single-pass page-by-page PDF extractor.

Uses PyMuPDF for text spans (preserves font-size heading heuristic)
and pdfplumber for table extraction — both opened once and iterated
page-by-page so memory stays bounded even for very large PDFs.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Tuple

import fitz         # PyMuPDF
import pdfplumber

logger = logging.getLogger(__name__)


class PDFParser:

    def parse(self, pdf_path: str) -> List[Dict]:
        """
        Returns list of content blocks with metadata.

        Each block:
            {type, text, page, section, subsection, font_size, is_heading}
        """
        blocks, _ = self.parse_with_page_count(pdf_path)
        return blocks

    def parse_with_page_count(self, pdf_path: str) -> Tuple[List[Dict], int]:
        """
        Same as parse() but also returns the total page count.
        Used by the pipeline to record Document.page_count.
        """
        blocks: List[Dict] = []
        current_section: str | None = None
        current_subsection: str | None = None

        fitz_doc = fitz.open(pdf_path)
        total_pages = len(fitz_doc)

        try:
            with pdfplumber.open(pdf_path) as plumb_pdf:
                for page_idx in range(total_pages):
                    page_num = page_idx + 1  # 1-based for display

                    # ── Text spans via PyMuPDF ────────────────────────────────
                    fitz_page = fitz_doc[page_idx]
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

                                    font_size = span.get("size", 0)
                                    is_bold   = "bold" in span.get("font", "").lower()
                                    is_heading = is_bold and font_size > 11

                                    if is_heading:
                                        if font_size > 14:
                                            current_section    = text
                                            current_subsection = None
                                        else:
                                            current_subsection = text

                                    blocks.append({
                                        "type":       "text",
                                        "text":       text,
                                        "page":       page_num,
                                        "section":    current_section,
                                        "subsection": current_subsection,
                                        "font_size":  font_size,
                                        "is_heading": is_heading,
                                    })
                    finally:
                        # Free the fitz page object to release memory
                        fitz_page = None

                    # ── Tables via pdfplumber ─────────────────────────────────
                    if page_idx < len(plumb_pdf.pages):
                        plumb_page = plumb_pdf.pages[page_idx]
                        try:
                            tables = plumb_page.extract_tables() or []
                            for table_idx, table in enumerate(tables):
                                if not table:
                                    continue
                                rows_text = [
                                    " | ".join(
                                        cell.strip() if cell else ""
                                        for cell in row
                                    )
                                    for row in table
                                ]
                                table_text = "\n".join(rows_text).strip()
                                if table_text:
                                    blocks.append({
                                        "type":        "table",
                                        "text":        table_text,
                                        "page":        page_num,
                                        "section":     current_section,
                                        "subsection":  current_subsection,
                                        "font_size":   None,
                                        "is_heading":  False,
                                        "table_index": table_idx,
                                    })
                        except Exception as e:
                            logger.warning(f"[PDF] Table extraction failed on page {page_num}: {e}")
        finally:
            fitz_doc.close()

        logger.info(f"[PDF] Parsed {pdf_path}: {total_pages} pages, {len(blocks)} blocks")
        return blocks, total_pages
