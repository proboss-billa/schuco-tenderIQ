import fitz  # PyMuPDF
import pdfplumber
from typing import List, Dict


class PDFParser:

    def parse(self, pdf_path: str) -> List[Dict]:
        """
        Returns list of content blocks with metadata
        Includes:
        - text blocks (PyMuPDF)
        - table blocks (pdfplumber)
        """

        blocks = []
        current_section = None
        current_subsection = None

        # ---------------------------
        # 1. TEXT EXTRACTION (PyMuPDF)
        # ---------------------------
        doc = fitz.open(pdf_path)

        for page_num, page in enumerate(doc, start=1):
            text_blocks = page.get_text("dict")["blocks"]

            for block in text_blocks:
                if "lines" not in block:
                    continue

                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text:
                            continue

                        font_size = span["size"]
                        is_bold = "bold" in span["font"].lower()

                        # Heuristic: heading detection
                        is_heading = is_bold and font_size > 11

                        if is_heading:
                            if font_size > 14:
                                current_section = text
                                current_subsection = None
                            elif font_size > 11:
                                current_subsection = text

                        blocks.append({
                            "type": "text",
                            "text": text,
                            "page": page_num,
                            "section": current_section,
                            "subsection": current_subsection,
                            "font_size": font_size,
                            "is_heading": is_heading
                        })

        doc.close()

        # ---------------------------
        # 2. TABLE EXTRACTION (pdfplumber)
        # ---------------------------
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables()

                for table_idx, table in enumerate(tables):
                    if not table:
                        continue

                    # Convert table to readable text (row-wise)
                    table_text_rows = []
                    for row in table:
                        cleaned_row = [
                            (cell.strip() if cell else "")
                            for cell in row
                        ]
                        table_text_rows.append(" | ".join(cleaned_row))

                    table_text = "\n".join(table_text_rows)

                    blocks.append({
                        "type": "table",
                        "text": table_text,
                        "page": page_num,
                        "section": None,          # optional: map later
                        "subsection": None,
                        "table_index": table_idx,
                        "is_heading": False
                    })

        return blocks