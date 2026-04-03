"""
dxf_parser.py
─────────────
Parser for AutoCAD DXF files (and limited DWG support) using ezdxf.

Extracts: TEXT, MTEXT, DIMENSION, MLEADER entities from modelspace
and all paper-space layouts, plus block attributes (title blocks).

Output blocks are compatible with the standard pipeline block schema.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

# Layers whose text belongs to the title block
_TITLE_LAYERS = {"title", "titleblock", "title block", "title-block",
                  "border", "sheet", "sheetinfo", "stamp", "info", "tb"}
# Layers to ignore entirely
_SKIP_LAYERS  = {"defpoints", "0", "_anno_hatching", "ref", "reference",
                  "vport", "viewport"}
# Min text height (drawing units) — filters sub-millimetre tolerance annotations
MIN_HEIGHT = 0.5


class DXFParser:
    """Extract text content from DXF (and some DWG) files."""

    def parse(self, file_path: str) -> List[Dict]:
        try:
            import ezdxf
            from ezdxf import recover as _recover
        except ImportError:
            raise RuntimeError(
                "ezdxf is required for DXF/DWG support — install with: pip install ezdxf"
            )

        ext = Path(file_path).suffix.lower()
        try:
            doc = ezdxf.readfile(file_path)
        except Exception as primary_err:
            logger.warning(f"[DXF] Primary open failed ({primary_err}), trying recover…")
            try:
                doc, auditor = _recover.readfile(file_path)
                if auditor.has_errors:
                    logger.warning(f"[DXF] Recovered with {len(auditor.errors)} errors — some content may be missing")
            except Exception as recover_err:
                raise RuntimeError(
                    f"Cannot open {'DWG' if ext == '.dwg' else 'DXF'} file. "
                    f"Primary: {primary_err} | Recovery: {recover_err}. "
                    f"For DWG files, convert to DXF with AutoCAD or ODA File Converter first."
                )

        blocks: List[Dict] = []

        # Title block attributes first (gives pipeline good section context)
        blocks.extend(self._title_block_attribs(doc))

        # Modelspace entities
        for entity in doc.modelspace():
            b = self._to_block(entity)
            if b:
                blocks.append(b)

        # Paper-space layouts (each sheet)
        for layout in doc.layouts:
            if layout.name.lower() in ("model", "*model_space"):
                continue
            sheet = layout.name
            for entity in layout:
                b = self._to_block(entity, sheet_override=f"Sheet: {sheet}")
                if b:
                    blocks.append(b)

        logger.info(f"[DXF] {file_path}: {len(blocks)} content blocks extracted")
        return blocks

    # ── Entity → block ─────────────────────────────────────────────────────

    def _to_block(self, entity, sheet_override: str | None = None) -> Dict | None:
        etype = entity.dxftype()
        layer = getattr(entity.dxf, "layer", "0").lower().strip()
        if layer in _SKIP_LAYERS:
            return None

        is_title = layer in _TITLE_LAYERS
        section  = sheet_override or ("Title Block" if is_title else f"Layer: {entity.dxf.layer}")

        if etype == "TEXT":
            text   = (entity.dxf.text or "").strip()
            height = getattr(entity.dxf, "height", 0)
            if not text or height < MIN_HEIGHT:
                return None
            return {"type": "text", "text": text, "page": None,
                    "section": section, "subsection": None,
                    "font_size": height, "is_heading": is_title or height > 5}

        if etype == "MTEXT":
            text   = (entity.plain_mtext() if hasattr(entity, "plain_mtext") else entity.text or "").strip()
            height = getattr(entity.dxf, "char_height", 0)
            if not text:
                return None
            return {"type": "text", "text": text, "page": None,
                    "section": section, "subsection": None,
                    "font_size": height, "is_heading": False}

        if etype == "DIMENSION":
            dim_txt     = getattr(entity.dxf, "text", "") or ""
            measurement = getattr(entity.dxf, "actual_measurement", None)
            if measurement is not None:
                val  = f"{measurement:.2f}"
                text = f"Dimension: {dim_txt or val} (measured: {val})"
            elif dim_txt:
                text = f"Dimension: {dim_txt}"
            else:
                return None
            return {"type": "text", "text": text, "page": None,
                    "section": section, "subsection": "Dimensions",
                    "font_size": 0, "is_heading": False}

        if etype == "MLEADER":
            try:
                mtext = ""
                if hasattr(entity, "context") and hasattr(entity.context, "mtext"):
                    mtext = (entity.context.mtext.default_content or "").strip()
                if not mtext:
                    return None
                return {"type": "text", "text": f"Callout: {mtext}", "page": None,
                        "section": section, "subsection": "Callouts",
                        "font_size": 0, "is_heading": False}
            except Exception:
                return None

        return None

    def _title_block_attribs(self, doc) -> List[Dict]:
        """Extract ATTRIB values from INSERT blocks whose name suggests a title block."""
        lines: List[str] = []
        try:
            for insert in doc.modelspace().query("INSERT"):
                name = insert.dxf.name.lower()
                if any(k in name for k in ("title", "border", "sheet", "stamp", "tb")):
                    for att in insert.attribs:
                        tag = att.dxf.tag.strip()
                        val = (att.dxf.text or "").strip()
                        if tag and val:
                            lines.append(f"{tag}: {val}")
        except Exception as e:
            logger.debug(f"[DXF] Title-block attrib extraction: {e}")

        if not lines:
            return []
        return [{"type": "text", "text": "\n".join(lines), "page": None,
                 "section": "Title Block", "subsection": None,
                 "font_size": 10, "is_heading": True}]
