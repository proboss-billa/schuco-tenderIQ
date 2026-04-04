"""
schedule_extractor.py
─────────────────────
Parses tabular schedules, legends, and key tables found in drawing sheets.
Detects rows of text that form a grid and extracts parameter-value pairs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from parsers.base_parser import TextEntity
from extractors.text_extractor import extract_text_matches
from extractors.dimension_extractor import RawMeasurement


@dataclass
class ScheduleRow:
    label: str
    value: str
    value_mm: Optional[float] = None
    unit: str = ""
    row_index: int = 0


def extract_schedule(texts: list[TextEntity]) -> list[RawMeasurement]:
    """
    Detect schedule/table rows from a list of text entities.
    Groups text entities by approximate Y coordinate (row clustering).
    Returns RawMeasurement list for matched numeric cells.
    """
    if not texts:
        return []

    # Cluster by Y coordinate (within ±5 drawing units)
    rows: dict[int, list[TextEntity]] = {}
    for te in texts:
        key = int(round(te.y / 5.0)) * 5
        rows.setdefault(key, []).append(te)

    results: list[RawMeasurement] = []

    for y_key, row_texts in sorted(rows.items(), reverse=True):
        row_texts_sorted = sorted(row_texts, key=lambda t: t.x)
        if len(row_texts_sorted) < 2:
            continue

        # Treat first cell as label, subsequent as values
        label = row_texts_sorted[0].text.strip()
        for cell in row_texts_sorted[1:]:
            matches = extract_text_matches(cell.text)
            for tm in matches:
                if tm.mm_values:
                    m = RawMeasurement(
                        value_mm=tm.primary_mm,
                        unit=tm.unit or "mm",
                        confidence=0.65,
                        extraction_method="SCHEDULE",
                        source_text=cell.text,
                        source_layer=cell.layer,
                        source_page=cell.page,
                        x=cell.x,
                        y=cell.y,
                        context_words=label.lower().split()[:10],
                    )
                    results.append(m)

    return results
