"""
json_exporter.py
────────────────
Writes ExtractionResult objects to JSON files.
One file per drawing.  Optional pretty-print.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from output.result_builder import ExtractionResult


def export_json(
    result: ExtractionResult,
    output_dir: Union[str, Path],
    pretty: bool = True,
) -> Path:
    """
    Write one ExtractionResult to a JSON file.

    Filename: <input_file_stem>_extracted.json

    Returns the path to the written file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(result.input_file).stem
    out_path = output_dir / f"{stem}_extracted.json"

    data = result.to_dict()

    with out_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
        else:
            json.dump(data, f, ensure_ascii=False, default=_json_default)

    return out_path


def export_json_batch(
    results: list[ExtractionResult],
    output_dir: Union[str, Path],
    combined_filename: str = "batch_extracted.json",
    pretty: bool = True,
) -> Path:
    """
    Write multiple ExtractionResults to a single combined JSON file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / combined_filename
    data = [r.to_dict() for r in results]

    with out_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
        else:
            json.dump(data, f, ensure_ascii=False, default=_json_default)

    return out_path


def _json_default(obj):
    """Fallback serialiser for non-standard types."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)
