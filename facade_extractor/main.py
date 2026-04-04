"""
main.py
───────
Facade Drawing Parameter Extraction Engine — CLI entry point.

Usage:
  python main.py INPUT [OPTIONS]

Examples:
  python main.py drawing.dxf
  python main.py drawings/
  python main.py drawing.pdf --spec config/spec_reference.yaml --format both
  python main.py drawings/ --output results/ --confidence 0.60 --verbose
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

import click
from loguru import logger
import yaml

# ── Add parent to sys.path so relative imports work when run directly ─────────
sys.path.insert(0, str(Path(__file__).parent))

from parsers.dwg_parser import DWGParser
from parsers.pdf_vector_parser import PDFVectorParser
from parsers.pdf_raster_parser import PDFRasterParser
from parsers.base_parser import DrawingSheet

from extractors.dimension_extractor import extract_dimensions
from extractors.geometry_extractor import extract_geometry
from extractors.pattern_extractor import extract_patterns
from extractors.schedule_extractor import extract_schedule
from extractors.dimension_extractor import RawMeasurement

from matchers.parameter_matcher import ParameterMatcher

from output.result_builder import build_result, ExtractionResult
from output.json_exporter import export_json, export_json_batch
from output.excel_exporter import export_excel

from utils.dwg_converter import DWGConverter

# ─────────────────────────────────────────────────────────────────────────────
# YAML loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_catalog(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("parameters", [])


def _load_spec(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("spec_references", [])


def _load_layer_keywords(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    groups = data.get("layer_groups", {})
    return {g: v.get("keywords", []) for g, v in groups.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline selector
# ─────────────────────────────────────────────────────────────────────────────

def _choose_parser(file_path: Path, pipeline: str, layer_keywords: dict):
    """Return the appropriate parser instance and format/pipeline strings."""
    ext = file_path.suffix.lower()

    if pipeline == "dwg" or (pipeline == "auto" and ext in (".dwg", ".dxf")):
        return DWGParser(config={"layer_keywords": layer_keywords}), \
               ("DWG" if ext == ".dwg" else "DXF"), "EZDXF"

    if pipeline == "pdf_vector" or (pipeline == "auto" and ext == ".pdf"):
        return PDFVectorParser(), "PDF_VECTOR", "PDFPLUMBER"

    if pipeline == "pdf_raster":
        return PDFRasterParser(), "PDF_RASTER", "OPENCV_OCR"

    # Auto fallback
    return PDFVectorParser(), "PDF_VECTOR", "PDFPLUMBER"


# ─────────────────────────────────────────────────────────────────────────────
# Process one file
# ─────────────────────────────────────────────────────────────────────────────

def process_file(
    file_path: Path,
    catalog: list[dict],
    spec_refs: list[dict],
    layer_keywords: dict,
    output_dir: Path,
    fmt: str,
    pipeline: str,
    min_confidence: float,
    verbose: bool,
) -> ExtractionResult | None:

    logger.info(f"Processing: {file_path.name}")

    # ── Convert DWG → DXF if needed ───────────────────────────────────────
    actual_path = file_path
    input_format = file_path.suffix.upper().lstrip(".")

    if file_path.suffix.lower() == ".dwg":
        converter = DWGConverter()
        if converter.is_available():
            dxf_path = converter.convert(file_path, output_dir / "_dxf_cache")
            if dxf_path:
                actual_path  = dxf_path
                input_format = "DWG"
                if verbose:
                    logger.info(f"  DWG → DXF: {dxf_path}")
            else:
                logger.warning("  ODA conversion failed — skipping DWG file")
                return None
        else:
            logger.warning("  ODA File Converter not found — skipping .dwg file")
            return None

    # ── Select parser ─────────────────────────────────────────────────────
    parser, det_format, det_pipeline = _choose_parser(actual_path, pipeline, layer_keywords)
    if input_format == "DWG":
        det_format = "DWG"

    # ── Parse ─────────────────────────────────────────────────────────────
    sheets: list[DrawingSheet] = parser.parse(actual_path)

    if not sheets:
        logger.warning(f"  No sheets extracted from {file_path.name}")
        return None

    # For single-sheet files (most cases), use the first sheet.
    # Multi-page PDFs will return multiple sheets — process each separately.
    all_results: list[ExtractionResult] = []

    for sheet in sheets:
        if sheet.errors:
            for e in sheet.errors:
                logger.error(f"  Error: {e}")

        if verbose:
            logger.info(
                f"  Sheet {sheet.page_number}: "
                f"type={sheet.sheet_type}, "
                f"scale={sheet.scale_result.scale_string if sheet.scale_result else 'UNKNOWN'}, "
                f"lines={len(sheet.lines)}, "
                f"texts={len(sheet.texts)}, "
                f"dims={len(sheet.dimensions)}"
            )

        # ── Extract measurements ───────────────────────────────────────────
        measurements: list[RawMeasurement] = []
        measurements.extend(extract_dimensions(sheet))
        measurements.extend(extract_geometry(sheet))
        measurements.extend(extract_patterns(sheet))

        # Schedule extraction (text tables)
        if sheet.sheet_type in ("SCHEDULE", "DETAIL"):
            measurements.extend(extract_schedule(sheet.texts))

        # Filter by minimum confidence
        measurements = [m for m in measurements if m.confidence >= min_confidence]

        if verbose:
            logger.info(f"  Raw measurements: {len(measurements)}")

        # ── Match to catalog ──────────────────────────────────────────────
        matcher = ParameterMatcher(
            catalog=catalog,
            spec_refs=spec_refs,
            min_confidence=min_confidence,
        )
        matched, unmatched = matcher.match(
            measurements,
            sheet_type=sheet.sheet_type,
        )

        if verbose:
            logger.info(
                f"  Matched: {len(matched)} / {len(catalog)} catalog params"
            )
            conflicts = [m for m in matched if m.spec_check.result == "CONFLICT"]
            if conflicts:
                logger.warning(f"  ⚠ CONFLICTS: {[c.name for c in conflicts]}")

        # ── Build result ──────────────────────────────────────────────────
        result = build_result(
            input_path=file_path,
            input_format=det_format,
            processing_pipeline=det_pipeline,
            sheet=sheet,
            matched=matched,
            unmatched=unmatched,
            catalog=catalog,
        )
        all_results.append(result)

    # Return first result for single-file output; caller handles batch
    primary = all_results[0] if all_results else None

    # ── Export ────────────────────────────────────────────────────────────
    if primary:
        if fmt in ("json", "both"):
            out = export_json(primary, output_dir)
            logger.success(f"  JSON → {out}")
        # Excel is written by the batch runner in CLI main

    return primary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("input", type=click.Path(exists=True))
@click.option(
    "--catalog", "catalog_path",
    default=None,
    type=click.Path(),
    help="Path to parameter_catalog.yaml  [default: config/parameter_catalog.yaml]",
)
@click.option(
    "--spec", "spec_path",
    default=None,
    type=click.Path(),
    help="Path to spec_reference.yaml for cross-checking  [optional]",
)
@click.option(
    "--output", "output_dir",
    default="output",
    show_default=True,
    help="Output directory",
)
@click.option(
    "--format", "fmt",
    default="both",
    show_default=True,
    type=click.Choice(["json", "excel", "both"], case_sensitive=False),
    help="Output format",
)
@click.option(
    "--pipeline",
    default="auto",
    show_default=True,
    type=click.Choice(["auto", "dwg", "pdf_vector", "pdf_raster"], case_sensitive=False),
    help="Force a specific processing pipeline",
)
@click.option(
    "--confidence",
    default=0.40,
    show_default=True,
    type=float,
    help="Minimum confidence threshold to include in output",
)
@click.option("--verbose", is_flag=True, default=False, help="Detailed extraction log")
def cli(
    input: str,
    catalog_path: str | None,
    spec_path: str | None,
    output_dir: str,
    fmt: str,
    pipeline: str,
    confidence: float,
    verbose: bool,
):
    """
    \b
    FACADE DRAWING PARAMETER EXTRACTION ENGINE
    ─────────────────────────────────────────────────────────────────────────
    Extracts dimensional, material, and performance parameters from facade
    drawings in DWG, DXF, or PDF format.

    INPUT can be a single file or a directory (batch mode).
    """
    # ── Configure logger ──────────────────────────────────────────────────
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, colorize=True)

    # ── Resolve paths ─────────────────────────────────────────────────────
    script_dir   = Path(__file__).parent
    catalog_path = Path(catalog_path) if catalog_path else script_dir / "config" / "parameter_catalog.yaml"
    spec_path    = Path(spec_path)    if spec_path    else None
    layer_kw_path = script_dir / "config" / "layer_keywords.yaml"
    output_dir   = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not catalog_path.exists():
        logger.error(f"Catalog not found: {catalog_path}")
        sys.exit(1)

    catalog       = _load_catalog(catalog_path)
    spec_refs     = _load_spec(spec_path)
    layer_keywords = _load_layer_keywords(layer_kw_path)

    logger.info(
        f"Catalog: {len(catalog)} parameters | "
        f"Spec refs: {len(spec_refs)} | "
        f"Min confidence: {confidence}"
    )

    # ── Collect input files ───────────────────────────────────────────────
    input_path = Path(input)
    if input_path.is_dir():
        files = sorted(
            f for f in input_path.rglob("*")
            if f.suffix.lower() in (".dwg", ".dxf", ".pdf")
        )
        logger.info(f"Batch mode: {len(files)} files found in {input_path}")
    else:
        files = [input_path]

    if not files:
        logger.warning("No supported files found.")
        sys.exit(0)

    # ── Process ───────────────────────────────────────────────────────────
    all_results: list[ExtractionResult] = []

    for file_path in files:
        result = process_file(
            file_path=file_path,
            catalog=catalog,
            spec_refs=spec_refs,
            layer_keywords=layer_keywords,
            output_dir=output_dir,
            fmt=fmt,
            pipeline=pipeline,
            min_confidence=confidence,
            verbose=verbose,
        )
        if result:
            all_results.append(result)

    # ── Excel batch export ────────────────────────────────────────────────
    if all_results and fmt in ("excel", "both"):
        xl_path = export_excel(all_results, output_dir)
        logger.success(f"Excel → {xl_path}")

    # ── Summary ───────────────────────────────────────────────────────────
    total_extracted = sum(
        r.extraction_summary.get("parameters_extracted", 0) for r in all_results
    )
    total_conflicts = sum(
        r.extraction_summary.get("conflicts_with_spec", 0) for r in all_results
    )
    avg_conf = (
        sum(r.extraction_summary.get("average_confidence", 0) for r in all_results)
        / len(all_results)
        if all_results else 0.0
    )

    logger.info(
        f"\n{'─' * 60}\n"
        f"  Files processed : {len(all_results)}\n"
        f"  Params extracted: {total_extracted}\n"
        f"  Spec conflicts  : {total_conflicts}\n"
        f"  Avg confidence  : {avg_conf:.0%}\n"
        f"{'─' * 60}"
    )


if __name__ == "__main__":
    cli()
