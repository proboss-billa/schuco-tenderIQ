from pathlib import Path

_DRAWING_FILENAME_KEYWORDS = [
    'drawing', 'drawings', 'tender drg', 'drg', 'elevation', 'elevations',
    'facade detail', 'curtain wall detail', 'cw detail', 'layout',
    'floor plan', 'site plan', 'detail sheet', 'detail drg',
]


def classify_file_type(filename: str) -> str:
    ext  = Path(filename).suffix.lower()
    stem = Path(filename).stem.lower()
    if ext == ".pdf":
        # Detect drawing PDFs by filename keywords before falling back to pdf_spec.
        # Content-based auto-detection (char-count sampling) still runs at parse
        # time inside _choose_pdf_parser and will catch drawing PDFs not matched
        # here. Together the two heuristics cover virtually all real tender sets.
        if any(kw in stem for kw in _DRAWING_FILENAME_KEYWORDS):
            return "pdf_drawing"
        return "pdf_spec"
    elif ext in [".docx", ".doc"]:
        return "docx_spec"
    elif ext in [".xlsx", ".xls", ".csv", ".ods"]:
        return "excel_boq"
    elif ext == ".dxf":
        return "dxf_drawing"
    elif ext == ".dwg":
        return "dwg_drawing"
    else:
        raise ValueError(f"Unsupported file type: {filename}. Supported: PDF, DOCX, XLSX, XLS, CSV, ODS, DXF, DWG")
