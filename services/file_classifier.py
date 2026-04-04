"""
Smart file classifier: combines filename heuristics, file extension, and
content sampling to determine what each uploaded document actually is.

Classification is a two-pass system:
1. Extension + filename keywords (instant, no I/O)
2. Content sampling (optional, for ambiguous PDFs — reads first 5 pages)

This allows the pipeline to treat each document type appropriately.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tenderiq.classifier")

# ── Filename keyword banks ──────────────────────────────────────────────────

_DRAWING_KEYWORDS = [
    'drawing', 'drawings', 'tender drg', 'drg', 'elevation', 'elevations',
    'facade detail', 'curtain wall detail', 'cw detail', 'layout', 'plan',
    'floor plan', 'site plan', 'detail sheet', 'detail drg', 'section detail',
    'architectural drawing', 'shop drawing', 'ga drawing', 'general arrangement',
    'facade drawing', 'glazing layout', 'panel layout', 'wall section',
    'exhibit', 'dwg', 'cad',
]

_BOQ_KEYWORDS = [
    'boq', 'bill of quantities', 'bill of quantity', 'tender boq',
    'price schedule', 'pricing', 'cost estimate', 'rate analysis',
    'schedule of rates', 'sor', 'schedule of quantities', 'soq',
    'commercial bid', 'price list', 'quotation', 'bid price',
]

_SPEC_KEYWORDS = [
    'specification', 'specifications', 'spec', 'technical spec',
    'technical specification', 'performance spec', 'material spec',
    'product spec', 'system spec',
]

_GCC_KEYWORDS = [
    'gcc', 'general conditions', 'general condition', 'conditions of contract',
    'contract conditions', 'terms and conditions', 'terms & conditions',
    'special conditions', 'scc', 'particular conditions',
    'scope of work', 'scope of supply', 'contractual',
]

_MATRIX_KEYWORDS = [
    'matrix', 'technical matrix', 'compliance matrix', 'requirement matrix',
    'comparison', 'checklist', 'evaluation', 'assessment',
    'data sheet', 'datasheet', 'schedule',
]


def classify_file_type(filename: str) -> str:
    """Classify a file by extension + filename keywords.

    Returns one of:
        pdf_drawing, pdf_spec, pdf_gcc, pdf_boq, pdf_matrix,
        docx_spec, excel_boq, dxf_drawing, dwg_drawing

    The classification determines which parser is used and how chunks
    are weighted during extraction.
    """
    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem.lower()

    if ext == ".pdf":
        return _classify_pdf(stem)
    elif ext in [".docx", ".doc"]:
        return "docx_spec"
    elif ext in [".xlsx", ".xls", ".csv", ".ods"]:
        return "excel_boq"
    elif ext == ".dxf":
        return "dxf_drawing"
    elif ext == ".dwg":
        return "dwg_drawing"
    else:
        raise ValueError(
            f"Unsupported file type: {filename}. "
            f"Supported: PDF, DOCX, XLSX, XLS, CSV, ODS, DXF, DWG"
        )


def _classify_pdf(stem: str) -> str:
    """Classify a PDF by filename keywords with priority ordering."""
    # Priority 1: Drawings (most distinctive filenames)
    if any(kw in stem for kw in _DRAWING_KEYWORDS):
        return "pdf_drawing"

    # Priority 2: BOQ in PDF form (rare but happens)
    if any(kw in stem for kw in _BOQ_KEYWORDS):
        return "pdf_spec"  # Still pdf_spec parser, but logged

    # Priority 3: GCC / contract documents
    if any(kw in stem for kw in _GCC_KEYWORDS):
        return "pdf_spec"

    # Priority 4: Technical matrix / schedule
    if any(kw in stem for kw in _MATRIX_KEYWORDS):
        return "pdf_spec"

    # Default: treat as specification
    return "pdf_spec"


def classify_content_type(filename: str, file_path: str) -> str:
    """Enhanced classification using both filename AND content sampling.

    For PDFs: samples first 5 pages to detect drawing vs text content.
    Returns the same type strings as classify_file_type, but more accurate.
    """
    base_type = classify_file_type(filename)

    # Only PDFs benefit from content sampling
    if not base_type.startswith("pdf"):
        return base_type

    # If already classified as drawing by filename, trust it
    if base_type == "pdf_drawing":
        return base_type

    # Content-sample to catch drawings with non-standard filenames
    try:
        import fitz
        doc = fitz.open(file_path)
        sample_n = min(5, len(doc))
        if sample_n == 0:
            doc.close()
            return base_type

        total_chars = sum(
            len(doc[i].get_text("text").strip()) for i in range(sample_n)
        )
        doc.close()
        avg_chars = total_chars / sample_n

        if avg_chars < 100:
            logger.info(
                f"[CLASSIFY] '{filename}': avg {avg_chars:.0f} chars/page "
                f"→ reclassified as pdf_drawing (was {base_type})"
            )
            return "pdf_drawing"
    except Exception as e:
        logger.warning(f"[CLASSIFY] Content sampling failed for {filename}: {e}")

    return base_type


def get_document_role(filename: str, file_type: Optional[str] = None) -> str:
    """Infer the document's role/purpose from its filename and/or DB file_type.

    The *file_type* parameter (from the database) is the most reliable source
    because it reflects content-based auto-detection (e.g., a PDF named
    "1.pdf" that was reclassified to pdf_drawing after char-count sampling).

    Returns a human-readable role string used for logging and
    context-building during extraction.

    Roles: 'drawing', 'specification', 'boq', 'gcc', 'matrix', 'unknown'
    """
    # Priority 1: Trust the DB file_type (reflects content-based detection)
    if file_type:
        if file_type in ("pdf_drawing", "dxf_drawing", "dwg_drawing"):
            return "drawing"
        if file_type == "excel_boq":
            return "boq"

    stem = Path(filename).stem.lower() if filename else ""
    ext = Path(filename).suffix.lower() if filename else ""

    if ext in [".xlsx", ".xls", ".csv", ".ods"]:
        return "boq"
    if ext in [".dxf", ".dwg"]:
        return "drawing"

    # Filename keyword matching (for PDFs where file_type is generic "pdf_spec")
    if any(kw in stem for kw in _DRAWING_KEYWORDS):
        return "drawing"
    if any(kw in stem for kw in _BOQ_KEYWORDS):
        return "boq"
    if any(kw in stem for kw in _GCC_KEYWORDS):
        return "gcc"
    if any(kw in stem for kw in _MATRIX_KEYWORDS):
        return "matrix"
    if any(kw in stem for kw in _SPEC_KEYWORDS):
        return "specification"

    return "specification"  # default assumption
