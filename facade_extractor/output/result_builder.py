"""
result_builder.py
─────────────────
Assembles the final structured result from all pipeline outputs.

Inputs:
  • DrawingSheet        (from parser)
  • list[MatchedParameter] (from parameter_matcher)
  • list[RawMeasurement]   (unmatched)
  • list[dict] catalog  (for NOT_FOUND tracking)
  • tool metadata

Output: ExtractionResult dataclass with .to_dict() → JSON-ready structure.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from parsers.base_parser import DrawingSheet
from matchers.parameter_matcher import MatchedParameter, SpecCheck
from extractors.dimension_extractor import RawMeasurement

TOOL_VERSION = "1.0.0"


@dataclass
class ExtractionResult:
    tool_version: str = TOOL_VERSION
    input_file: str = ""
    input_format: str = ""          # DWG | DXF | PDF_VECTOR | PDF_RASTER
    processing_pipeline: str = ""   # EZDXF | PDFPLUMBER | OPENCV_OCR

    sheet_metadata: dict = field(default_factory=dict)
    parameters: list[dict] = field(default_factory=list)
    unmatched_dimensions: list[dict] = field(default_factory=list)
    extraction_summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tool_version":        self.tool_version,
            "input_file":          self.input_file,
            "input_format":        self.input_format,
            "processing_pipeline": self.processing_pipeline,
            "sheet_metadata":      self.sheet_metadata,
            "parameters":          self.parameters,
            "unmatched_dimensions": self.unmatched_dimensions,
            "extraction_summary":  self.extraction_summary,
            "warnings":            self.warnings,
            "errors":              self.errors,
        }


def build_result(
    input_path: str | Path,
    input_format: str,
    processing_pipeline: str,
    sheet: DrawingSheet,
    matched: list[MatchedParameter],
    unmatched: list[RawMeasurement],
    catalog: list[dict],
) -> ExtractionResult:
    """
    Assemble one ExtractionResult from all pipeline outputs.
    """
    result = ExtractionResult(
        input_file=str(Path(input_path).name),
        input_format=input_format,
        processing_pipeline=processing_pipeline,
    )

    # ── Sheet metadata ────────────────────────────────────────────────────
    tb = sheet.titleblock
    sr = sheet.scale_result

    result.sheet_metadata = {
        "sheet_number":  tb.sheet_number  if tb else "",
        "sheet_title":   tb.sheet_title   if tb else "",
        "revision":      tb.revision      if tb else "",
        "scale":         sr.scale_string  if sr else "UNKNOWN",
        "scale_source":  sr.source        if sr else "UNKNOWN",
        "sheet_type":    sheet.sheet_type,
        "drawing_units": sr.drawing_unit  if sr else "mm",
    }

    # ── Parameters ────────────────────────────────────────────────────────
    matched_ids = {mp.id for mp in matched}
    catalog_ids = {p["id"] for p in catalog}
    not_found_ids = catalog_ids - matched_ids

    params_out: list[dict] = [mp.to_dict() for mp in matched]

    # Add NOT_FOUND entries for missing catalog items
    for pid in sorted(not_found_ids):
        param = next((p for p in catalog if p["id"] == pid), None)
        if not param:
            continue
        params_out.append({
            "id":               pid,
            "name":             param["name"],
            "value":            None,
            "unit":             param.get("unit", "mm"),
            "confidence":       0.0,
            "extraction_method": "NOT_FOUND",
            "source_text":      "",
            "source_layer":     "",
            "source_page":      0,
            "source_coords":    [],
            "spec_check": {
                "spec_value":  None,
                "tolerance":   None,
                "result":      "NOT_FOUND",
                "delta":       None,
                "source":      "",
            },
            "notes": "",
        })

    params_out.sort(key=lambda p: p["id"])
    result.parameters = params_out

    # ── Unmatched dimensions ───────────────────────────────────────────────
    result.unmatched_dimensions = [
        {
            "value":        round(m.value_mm, 3),
            "unit":         m.unit,
            "source_text":  m.source_text,
            "coords":       [round(m.x, 2), round(m.y, 2)],
            "layer":        m.source_layer,
            "confidence":   round(m.confidence, 3),
        }
        for m in unmatched
        if m.value_mm > 0
    ]

    # ── Summary ───────────────────────────────────────────────────────────
    total_in_catalog  = len(catalog)
    extracted_count   = len(matched)
    not_found_count   = len(not_found_ids)
    conflicts         = sum(
        1 for mp in matched if mp.spec_check.result == "CONFLICT"
    )
    confidences = [mp.confidence for mp in matched] if matched else [0.0]
    avg_conf = statistics.mean(confidences) if confidences else 0.0

    result.extraction_summary = {
        "total_parameters_in_catalog": total_in_catalog,
        "parameters_extracted":        extracted_count,
        "parameters_not_found":        not_found_count,
        "conflicts_with_spec":         conflicts,
        "average_confidence":          round(avg_conf, 3),
    }

    result.warnings = list(sheet.warnings)
    result.errors   = list(sheet.errors)

    return result
