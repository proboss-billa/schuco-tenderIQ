# parsing/excel_parser.py

import csv
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ── Column-detection keywords (order = priority) ────────────────────────────
FIELD_KEYWORDS = {
    "item_number": ["item no", "item number", "sl no", "sr no", "s.no", "sno", "#", "item"],
    "description": ["description", "item description", "particulars", "work description", "scope", "details"],
    "quantity": ["quantity", "qty", "qnty", "nos"],
    "unit": ["unit", "uom", "unit of measurement"],
    "rate": ["rate", "unit rate", "unit price", "price"],
    "amount": ["amount", "total", "value", "total amount"],
    "category": ["category", "trade", "section", "group"],
    "sub_category": ["sub category", "subcategory", "sub-category", "sub section"],
}

# Keywords that signal a header row (any cell containing these → likely header)
_HEADER_SIGNALS = {
    "description", "item", "s.no", "quantity", "qty", "unit", "rate",
    "amount", "total", "uom", "sl no", "sr no", "particulars", "scope",
}

# Maximum rows to scan when looking for the header
_HEADER_SCAN_ROWS = 20


class ExcelBOQParser:
    """Parse spreadsheet BOQ with flexible column / header / sheet detection.

    Supports: .xlsx, .xls, .csv, .ods
    """

    # ── Public API ───────────────────────────────────────────────────────────

    def parse(self, file_path: str) -> Tuple[List[Dict], List[str]]:
        """Return (boq_items, text_chunks).

        *boq_items*  — structured dicts when column mapping succeeds.
        *text_chunks* — raw text representations of every sheet (always
                        produced so the content is embeddable even when
                        structured parsing fails).
        """
        ext = Path(file_path).suffix.lower()

        sheets: Dict[str, pd.DataFrame] = {}
        try:
            if ext == ".csv":
                sheets = self._read_csv(file_path)
            else:
                sheets = self._read_spreadsheet(file_path)
        except Exception as e:
            logger.error(f"[BOQ] Failed to read {file_path}: {e}")
            return [], []

        all_items: List[Dict] = []
        all_text_chunks: List[str] = []

        for sheet_name, raw_df in sheets.items():
            if raw_df.empty:
                continue

            # ── Try structured BOQ extraction ────────────────────────────
            header_row = self._find_header_row(raw_df)
            items: List[Dict] = []

            if header_row is not None:
                df = self._promote_header(raw_df, header_row)
                mapping = self._detect_columns(df)
                if mapping:
                    items = self._extract_items(df, mapping)
                    if items:
                        logger.info(
                            f"[BOQ] Sheet '{sheet_name}': {len(items)} items "
                            f"(header row {header_row}, cols: {mapping})"
                        )
                        all_items.extend(items)

            # ── Always produce text chunks (fallback + supplement) ───────
            chunks = self._sheet_to_text_chunks(raw_df, sheet_name)
            all_text_chunks.extend(chunks)

        logger.info(
            f"[BOQ] Totals from {file_path}: "
            f"{len(all_items)} structured items, {len(all_text_chunks)} text chunks"
        )
        return all_items, all_text_chunks

    # ── File readers ─────────────────────────────────────────────────────────

    @staticmethod
    def _read_spreadsheet(file_path: str) -> Dict[str, pd.DataFrame]:
        """Read all sheets from .xlsx / .xls / .ods into raw DataFrames."""
        ext = Path(file_path).suffix.lower()

        engine = None
        if ext == ".xls":
            engine = "xlrd"
        elif ext == ".ods":
            engine = "odf"

        try:
            all_sheets = pd.read_excel(
                file_path,
                sheet_name=None,   # ← read ALL sheets
                header=None,       # ← don't auto-pick header
                engine=engine,
                dtype=str,         # keep everything as text initially
                na_filter=False,
            )
        except ImportError as e:
            # Missing engine (xlrd / odfpy) — fall back to openpyxl
            logger.warning(f"[BOQ] Engine not available ({e}); retrying with openpyxl")
            all_sheets = pd.read_excel(
                file_path,
                sheet_name=None,
                header=None,
                engine="openpyxl",
                dtype=str,
                na_filter=False,
            )
        return all_sheets

    @staticmethod
    def _read_csv(file_path: str) -> Dict[str, pd.DataFrame]:
        """Read a CSV file (try multiple encodings and delimiters)."""
        for encoding in ("utf-8", "latin-1", "cp1252"):
            for sep in (",", ";", "\t", "|"):
                try:
                    df = pd.read_csv(
                        file_path,
                        header=None,
                        sep=sep,
                        encoding=encoding,
                        dtype=str,
                        na_filter=False,
                        on_bad_lines="skip",
                    )
                    # Accept if we got more than 1 column (delimiter worked)
                    if df.shape[1] > 1 and df.shape[0] > 1:
                        return {"Sheet1": df}
                except Exception:
                    continue
        # Last resort: single-column read
        df = pd.read_csv(file_path, header=None, dtype=str, na_filter=False,
                         on_bad_lines="skip")
        return {"Sheet1": df}

    # ── Header detection ─────────────────────────────────────────────────────

    @staticmethod
    def _find_header_row(df: pd.DataFrame) -> Optional[int]:
        """Scan the first N rows for the one that looks most like a header.

        A row scores a point for each cell whose lowercase text contains
        a known header keyword.  The row with the highest score (≥ 2) wins.
        """
        best_row, best_score = None, 0

        scan_limit = min(len(df), _HEADER_SCAN_ROWS)
        for idx in range(scan_limit):
            score = 0
            for val in df.iloc[idx]:
                cell = str(val).lower().strip()
                if any(kw in cell for kw in _HEADER_SIGNALS):
                    score += 1
            if score > best_score:
                best_score = score
                best_row = idx

        return best_row if best_score >= 2 else None

    @staticmethod
    def _promote_header(raw_df: pd.DataFrame, header_row: int) -> pd.DataFrame:
        """Use *header_row* as column names and return the rows below it."""
        new_cols = [str(c).strip() for c in raw_df.iloc[header_row]]
        df = raw_df.iloc[header_row + 1:].copy()
        df.columns = new_cols
        df.reset_index(drop=True, inplace=True)
        return df

    # ── Column mapping ───────────────────────────────────────────────────────

    @staticmethod
    def _detect_columns(df: pd.DataFrame) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        used_cols: set = set()

        for field, keywords in FIELD_KEYWORDS.items():
            for col in df.columns:
                if col in used_cols:
                    continue
                col_lower = str(col).lower().strip()
                if any(kw in col_lower for kw in keywords):
                    mapping[field] = col
                    used_cols.add(col)
                    break

        return mapping

    # ── Structured item extraction ───────────────────────────────────────────

    def _extract_items(self, df: pd.DataFrame, mapping: Dict[str, str]) -> List[Dict]:
        items: List[Dict] = []
        desc_col = mapping.get("description")

        for _, row in df.iterrows():
            desc = self._safe_get(row, mapping, "description")
            if not desc:
                continue

            item = {
                "item_number": self._safe_get(row, mapping, "item_number"),
                "description": desc,
                "quantity": self._safe_float(row, mapping, "quantity"),
                "unit": self._safe_get(row, mapping, "unit"),
                "rate": self._safe_float(row, mapping, "rate"),
                "amount": self._safe_float(row, mapping, "amount"),
                "category": self._safe_get(row, mapping, "category"),
                "sub_category": self._safe_get(row, mapping, "sub_category"),
            }
            items.append(item)

        return items

    # ── Text chunk fallback ──────────────────────────────────────────────────

    @staticmethod
    def _sheet_to_text_chunks(
        df: pd.DataFrame,
        sheet_name: str,
        max_rows_per_chunk: int = 25,
    ) -> List[str]:
        """Convert a raw DataFrame to text chunks for embedding.

        Groups rows into chunks of *max_rows_per_chunk*.  Each chunk is a
        readable text block: ``Sheet: <name> | Row 5: val1 | val2 | val3``.
        Empty rows are skipped.
        """
        chunks: List[str] = []
        lines: List[str] = []

        for idx in range(len(df)):
            cells = []
            for val in df.iloc[idx]:
                s = str(val).strip()
                if s and s.lower() not in ("nan", "none", ""):
                    cells.append(s)
            if not cells:
                continue

            lines.append(f"Row {idx + 1}: {' | '.join(cells)}")

            if len(lines) >= max_rows_per_chunk:
                chunk_text = f"Sheet: {sheet_name}\n" + "\n".join(lines)
                chunks.append(chunk_text)
                lines = []

        # Flush remaining
        if lines:
            chunk_text = f"Sheet: {sheet_name}\n" + "\n".join(lines)
            chunks.append(chunk_text)

        return chunks

    # ── Value helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _safe_get(row, mapping: Dict, field: str) -> Optional[str]:
        col = mapping.get(field)
        if col and col in row.index:
            val = str(row[col]).strip()
            if val and val.lower() not in ("nan", "none", ""):
                return val
        return None

    @staticmethod
    def _safe_float(row, mapping: Dict, field: str) -> Optional[float]:
        col = mapping.get(field)
        if col and col in row.index:
            raw = str(row[col]).strip().replace(",", "")
            try:
                return float(raw)
            except (ValueError, TypeError):
                return None
        return None
