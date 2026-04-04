"""
dwg_parser.py
─────────────
Priority-1 parser.  Loads DXF files via ezdxf and produces DrawingSheet
objects with fully populated entity lists.

DWG files are first converted to DXF by utils/dwg_converter.py before
this parser is invoked.

Entity types extracted:
  LINE, LWPOLYLINE, POLYLINE  → LineSegment
  TEXT, MTEXT                 → TextEntity
  DIMENSION                   → DimensionEntity
  CIRCLE, ARC                 → CircleEntity
  INSERT (blocks)             → expanded recursively

Layer classification uses layer_keywords.yaml (loaded via config dict).
Scale detection uses scale_extractor multi-method pipeline.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Optional

try:
    import ezdxf
    from ezdxf.entities import (
        DXFGraphic, Line, LWPolyline, Polyline, Text, MText,
        Dimension, Circle, Arc, Insert,
    )
    from ezdxf.math import Vec3
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False

from parsers.base_parser import (
    BaseParser, DrawingSheet, LineSegment, TextEntity,
    DimensionEntity, CircleEntity, Point2D,
)
from classifiers.scale_extractor import detect_scale, ScaleResult
from classifiers.sheet_classifier import classify_sheet
from classifiers.titleblock_parser import parse_from_dxf_attribs, TitleBlockData
from extractors.text_extractor import strip_mtext_codes
from matchers.unit_normaliser import insunits_to_mm_factor, normalise_to_mm

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_ANGLE_TOL = math.radians(3)   # ±3° for H/V classification


def _vec3_to_point(v: Any) -> Point2D:
    return Point2D(float(v[0]), float(v[1]))


def _classify_orientation(dx: float, dy: float, length: float) -> str:
    if length == 0:
        return "ANY"
    if abs(dy) / length < math.sin(_ANGLE_TOL):
        return "HORIZONTAL"
    if abs(dx) / length < math.sin(_ANGLE_TOL):
        return "VERTICAL"
    return "DIAGONAL"


def _score_layer(layer_name: str, keyword_groups: dict[str, list[str]]) -> dict[str, int]:
    """Return {group_name: score} for a layer name."""
    lower = layer_name.lower()
    scores: dict[str, int] = {}
    for group, keywords in keyword_groups.items():
        scores[group] = sum(1 for kw in keywords if kw in lower)
    return scores


def _classify_all_layers(
    layer_names: list[str],
    keyword_groups: dict[str, list[str]],
    threshold: int = 1,
) -> dict[str, list[str]]:
    """
    Returns {group_name: [candidate_layer_names]}.
    A layer can appear in multiple groups.
    """
    result: dict[str, list[str]] = {g: [] for g in keyword_groups}
    for layer in layer_names:
        scores = _score_layer(layer, keyword_groups)
        for group, score in scores.items():
            if score >= threshold:
                result[group].append(layer)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DWGParser
# ─────────────────────────────────────────────────────────────────────────────

class DWGParser(BaseParser):

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in (".dxf", ".dwg")

    def parse(self, file_path: Path) -> list[DrawingSheet]:
        if not EZDXF_AVAILABLE:
            return self._fail(file_path, "ezdxf not installed")

        path = Path(file_path)
        if path.suffix.lower() == ".dwg":
            return self._fail(
                file_path,
                ".dwg file passed to DWGParser — convert to .dxf first "
                "using utils/dwg_converter.py",
            )

        self._reset_logs()

        try:
            doc = ezdxf.readfile(str(path))
        except Exception as exc:
            return self._fail(file_path, f"ezdxf read error: {exc}")

        sheet = DrawingSheet(
            source_file=str(path),
            page_number=0,
        )

        # ── 1. HEADER variables ────────────────────────────────────────────
        header = doc.header
        insunits  = int(header.get("$INSUNITS", 4) or 4)
        dimscale  = float(header.get("$DIMSCALE", 1.0) or 1.0)
        ltscale   = float(header.get("$LTSCALE", 1.0) or 1.0)
        mm_factor = insunits_to_mm_factor(insunits)

        # ── 2. Layer inventory ─────────────────────────────────────────────
        keyword_groups: dict[str, list[str]] = (
            self.config.get("layer_keywords", {})
        )
        all_layers: list[str] = [layer.dxf.name for layer in doc.layers]
        layer_classification = _classify_all_layers(all_layers, keyword_groups)
        sheet.layer_classification = layer_classification

        # ── 3. Extract entities from modelspace ───────────────────────────
        msp = doc.modelspace()
        titleblock_attribs: list[dict[str, str]] = []
        all_texts: list[str] = []

        for entity in msp:
            etype = entity.dxftype()

            if etype == "LINE":
                seg = self._extract_line(entity, mm_factor)
                if seg:
                    sheet.lines.append(seg)

            elif etype in ("LWPOLYLINE", "POLYLINE"):
                segs = self._extract_polyline(entity, mm_factor)
                sheet.lines.extend(segs)

            elif etype == "TEXT":
                te = self._extract_text(entity, mm_factor)
                if te:
                    sheet.texts.append(te)
                    all_texts.append(te.text)

            elif etype == "MTEXT":
                te = self._extract_mtext(entity, mm_factor)
                if te:
                    sheet.texts.append(te)
                    all_texts.append(te.text)

            elif etype == "DIMENSION":
                de = self._extract_dimension(entity, mm_factor)
                if de:
                    sheet.dimensions.append(de)

            elif etype in ("CIRCLE", "ARC"):
                ce = self._extract_circle(entity, mm_factor)
                if ce:
                    sheet.circles.append(ce)

            elif etype == "INSERT":
                # Expand block — extract attribs for title block detection
                block_name = entity.dxf.name or ""
                attribs = self._extract_insert_attribs(entity)
                titleblock_attribs.extend(attribs)

                # Also recurse into block geometry
                sub_lines, sub_texts = self._expand_block(
                    entity, doc, mm_factor
                )
                sheet.lines.extend(sub_lines)
                sheet.texts.extend(sub_texts)
                all_texts.extend(t.text for t in sub_texts)

        # ── 4. Title block ─────────────────────────────────────────────────
        if titleblock_attribs:
            tb = parse_from_dxf_attribs(titleblock_attribs)
        else:
            from classifiers.titleblock_parser import parse_from_text
            tb = parse_from_text(all_texts)
        sheet.titleblock = tb

        # ── 5. Scale detection ─────────────────────────────────────────────
        # Use first DIMENSION entity for empirical calibration if available
        dim_geom = dim_ann = None
        if sheet.dimensions:
            d = sheet.dimensions[0]
            if d.geometry_length > 0 and d.value_mm > 0:
                dim_geom = d.geometry_length
                dim_ann  = d.value_mm

        tb_texts = [
            t.text for t in sheet.texts
            if "titleblock" in [
                g for g, layers in layer_classification.items()
                if t.layer in layers
            ]
        ] or all_texts[:50]   # fallback: first 50 text items

        scale_result = detect_scale(
            text_blocks=tb_texts,
            insunits=insunits,
            dimscale=dimscale,
            ltscale=ltscale,
            dimension_geometry_length=dim_geom,
            dimension_annotated_mm=dim_ann,
        )
        sheet.scale_result = scale_result
        if tb.scale_result is None:
            tb.scale_result = scale_result

        # ── 6. Sheet classification ────────────────────────────────────────
        classification = classify_sheet(all_texts, sheet_title=tb.sheet_title)
        sheet.sheet_type = classification.sheet_type

        sheet.warnings.extend(self._warnings)
        sheet.errors.extend(self._errors)
        return [sheet]

    # ── Entity extractors ──────────────────────────────────────────────────

    def _extract_line(self, entity: Any, mm_factor: float) -> Optional[LineSegment]:
        try:
            start = entity.dxf.start
            end   = entity.dxf.end
            layer = entity.dxf.layer or ""
            dx = float(end[0] - start[0])
            dy = float(end[1] - start[1])
            length = math.hypot(dx, dy)
            return LineSegment(
                start=Point2D(float(start[0]), float(start[1])),
                end=Point2D(float(end[0]), float(end[1])),
                layer=layer,
                length=length,
                orientation=_classify_orientation(dx, dy, length),
            )
        except Exception:
            return None

    def _extract_polyline(
        self, entity: Any, mm_factor: float
    ) -> list[LineSegment]:
        segs: list[LineSegment] = []
        layer = entity.dxf.layer or ""
        try:
            if entity.dxftype() == "LWPOLYLINE":
                points = list(entity.get_points("xy"))
            else:
                points = [
                    (v.dxf.location[0], v.dxf.location[1])
                    for v in entity.vertices
                ]
            for i in range(len(points) - 1):
                x0, y0 = points[i][0], points[i][1]
                x1, y1 = points[i + 1][0], points[i + 1][1]
                dx, dy = x1 - x0, y1 - y0
                length = math.hypot(dx, dy)
                segs.append(LineSegment(
                    start=Point2D(x0, y0),
                    end=Point2D(x1, y1),
                    layer=layer,
                    length=length,
                    orientation=_classify_orientation(dx, dy, length),
                ))
        except Exception:
            pass
        return segs

    def _extract_text(self, entity: Any, mm_factor: float) -> Optional[TextEntity]:
        try:
            text = (entity.dxf.text or "").strip()
            if not text:
                return None
            return TextEntity(
                text=text,
                x=float(entity.dxf.insert[0]),
                y=float(entity.dxf.insert[1]),
                height=float(entity.dxf.height or 0.0),
                layer=entity.dxf.layer or "",
            )
        except Exception:
            return None

    def _extract_mtext(self, entity: Any, mm_factor: float) -> Optional[TextEntity]:
        try:
            raw = entity.text or ""
            plain = strip_mtext_codes(raw).strip()
            if not plain:
                return None
            ins = entity.dxf.insert
            return TextEntity(
                text=plain,
                x=float(ins[0]),
                y=float(ins[1]),
                height=float(entity.dxf.char_height or 0.0),
                layer=entity.dxf.layer or "",
            )
        except Exception:
            return None

    def _extract_dimension(
        self, entity: Any, mm_factor: float
    ) -> Optional[DimensionEntity]:
        try:
            # Prefer actual_measurement (model-space geometry), fall back to
            # override text parsing
            raw_val = None
            raw_text = ""

            override = (entity.dxf.text or "").strip()
            # "<>" means "use actual measurement with possible suffix"
            if override and override != "<>":
                # May be a raw number or "<>suffix"
                numeric = re.sub(r"<>", "", override).strip()
                raw_text = override
                try:
                    raw_val = float(numeric)
                except ValueError:
                    # Try extracting first number
                    m = re.search(r"[\d.]+", numeric)
                    if m:
                        raw_val = float(m.group())

            # actual_measurement is geometry length in drawing units
            try:
                geom_len = float(entity.dxf.actual_measurement or 0.0)
            except Exception:
                geom_len = 0.0

            if raw_val is None and geom_len > 0:
                raw_val = geom_len * mm_factor
                raw_text = str(raw_val)

            if raw_val is None:
                return None

            value_mm = raw_val * mm_factor if geom_len == 0 else raw_val

            # Classify dimension type
            dtype_map = {
                32:  "LINEAR",
                33:  "ALIGNED",
                35:  "ANGULAR",
                36:  "DIAMETER",
                37:  "RADIUS",
                38:  "ORDINATE",
                160: "ARC_LENGTH",
            }
            dim_type = dtype_map.get(
                int(entity.dxf.dimtype or 0) & 0xFF,
                "LINEAR",
            )

            # Text midpoint
            try:
                tp = entity.dxf.text_midpoint
                tx, ty = float(tp[0]), float(tp[1])
            except Exception:
                tx = ty = 0.0

            # Defpoint
            try:
                dp = entity.dxf.defpoint
                dpx, dpy = float(dp[0]), float(dp[1])
            except Exception:
                dpx = dpy = 0.0

            return DimensionEntity(
                value_mm=value_mm,
                raw_text=raw_text or str(raw_val),
                dim_type=dim_type,
                x=tx,
                y=ty,
                defpoint_x=dpx,
                defpoint_y=dpy,
                geometry_length=geom_len,
                layer=entity.dxf.layer or "",
                override_text=override,
            )
        except Exception:
            return None

    def _extract_circle(
        self, entity: Any, mm_factor: float
    ) -> Optional[CircleEntity]:
        try:
            center = entity.dxf.center
            radius = float(entity.dxf.radius or 0.0)
            return CircleEntity(
                center=Point2D(float(center[0]), float(center[1])),
                radius=radius * mm_factor,
                layer=entity.dxf.layer or "",
            )
        except Exception:
            return None

    def _extract_insert_attribs(self, insert: Any) -> list[dict[str, str]]:
        attribs = []
        try:
            for attrib in insert.attribs:
                tag  = (attrib.dxf.tag or "").strip()
                text = (attrib.dxf.text or "").strip()
                if tag:
                    attribs.append({"tag": tag, "text": text})
        except Exception:
            pass
        return attribs

    def _expand_block(
        self, insert: Any, doc: Any, mm_factor: float
    ) -> tuple[list[LineSegment], list[TextEntity]]:
        """Recursively expand a block INSERT and return its geometry + text."""
        lines: list[LineSegment] = []
        texts: list[TextEntity] = []
        try:
            block_name = insert.dxf.name
            if block_name not in doc.blocks:
                return lines, texts
            block = doc.blocks[block_name]
            for ent in block:
                etype = ent.dxftype()
                if etype == "LINE":
                    seg = self._extract_line(ent, mm_factor)
                    if seg:
                        lines.append(seg)
                elif etype in ("LWPOLYLINE", "POLYLINE"):
                    lines.extend(self._extract_polyline(ent, mm_factor))
                elif etype == "TEXT":
                    te = self._extract_text(ent, mm_factor)
                    if te:
                        texts.append(te)
                elif etype == "MTEXT":
                    te = self._extract_mtext(ent, mm_factor)
                    if te:
                        texts.append(te)
        except Exception:
            pass
        return lines, texts

    def _fail(self, file_path: Any, msg: str) -> list[DrawingSheet]:
        sheet = DrawingSheet(source_file=str(file_path))
        sheet.errors.append(msg)
        return [sheet]
