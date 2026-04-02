"""
pdf_splitter.py
───────────────
Splits a multi-page PDF into individual single-page PDFs.
Useful when each drawing is on a separate page.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

try:
    import fitz   # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False


def split_pdf(
    pdf_path: Path,
    output_dir: Optional[Path] = None,
) -> list[Path]:
    """
    Split a multi-page PDF into individual page PDFs.
    Returns list of output file paths.
    """
    if not FITZ_AVAILABLE:
        return []

    pdf_path   = Path(pdf_path)
    output_dir = Path(output_dir) if output_dir else pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    paths: list[Path] = []

    for i, page in enumerate(doc):
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=i, to_page=i)
        out_name = f"{pdf_path.stem}_page{i+1:03d}.pdf"
        out_path = output_dir / out_name
        new_doc.save(str(out_path))
        new_doc.close()
        paths.append(out_path)

    doc.close()
    return paths
