"""
base_parser.py
──────────────
Abstract base class that every concrete parser must implement.

Concrete parsers:
  DWGParser         → parsers/dwg_parser.py
  PDFVectorParser   → parsers/pdf_vector_parser.py
  PDFRasterParser   → parsers/pdf_raster_parser.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Shared geometry primitives
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Point2D:
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass
class LineSegment:
    start: Point2D
    end: Point2D
    layer: str = ""
    length: float = 0.0           # pre-computed in drawing units
    orientation: str = "ANY"      # HORIZONTAL | VERTICAL | DIAGONAL

    def __post_init__(self):
        if self.length == 0.0:
            dx = self.end.x - self.start.x
            dy = self.end.y - self.start.y
            self.length = (dx**2 + dy**2) ** 0.5
        if self.orientation == "ANY":
            dx = abs(self.end.x - self.start.x)
            dy = abs(self.end.y - self.start.y)
            angle_threshold = 0.052  # ~3°
            if self.length > 0:
                if dy / self.length < angle_threshold:
                    self.orientation = "HORIZONTAL"
                elif dx / self.length < angle_threshold:
                    self.orientation = "VERTICAL"
                else:
                    self.orientation = "DIAGONAL"


@dataclass
class TextEntity:
    text: str
    x: float
    y: float
    height: float = 0.0           # character height in drawing units
    layer: str = ""
    page: int = 0
    width: float = 0.0            # bounding box width (if known)


@dataclass
class DimensionEntity:
    value_mm: float               # annotated value, normalised to mm
    raw_text: str                 # original dimension string
    dim_type: str = "LINEAR"      # LINEAR | ALIGNED | ANGULAR | RADIAL | ORDINATE
    x: float = 0.0                # text midpoint X
    y: float = 0.0                # text midpoint Y
    defpoint_x: float = 0.0
    defpoint_y: float = 0.0
    geometry_length: float = 0.0  # measured geometry span (drawing units)
    layer: str = ""
    page: int = 0
    override_text: str = ""


@dataclass
class CircleEntity:
    center: Point2D
    radius: float
    layer: str = ""
    page: int = 0

    @property
    def diameter_mm(self) -> float:
        """Diameter in drawing units — caller must apply scale."""
        return self.radius * 2


@dataclass
class DrawingSheet:
    """
    Normalised intermediate representation of one drawing sheet.
    All coordinates are in drawing units (mm for DXF; PDF points for PDF).
    The scale_result carries the conversion factor to real mm.
    """
    source_file: str = ""
    page_number: int = 0
    sheet_type: str = "UNKNOWN"        # from sheet_classifier
    scale_result: Any = None           # ScaleResult from scale_extractor
    titleblock: Any = None             # TitleBlockData

    lines: list[LineSegment] = field(default_factory=list)
    texts: list[TextEntity] = field(default_factory=list)
    dimensions: list[DimensionEntity] = field(default_factory=list)
    circles: list[CircleEntity] = field(default_factory=list)

    layer_classification: dict[str, list[str]] = field(default_factory=dict)
    # ^ {group_name: [layer_name, ...]}

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────

class BaseParser(ABC):
    """
    All parsers must implement `parse()` which takes a file path and returns
    a list of DrawingSheet objects (one per page/sheet).
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._warnings: list[str] = []
        self._errors: list[str] = []

    @abstractmethod
    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser can handle the given file."""
        ...

    @abstractmethod
    def parse(self, file_path: Path) -> list[DrawingSheet]:
        """
        Parse the file and return a list of DrawingSheet objects.
        Must not raise for recoverable errors — log to sheet.warnings/errors.
        """
        ...

    # ── Convenience helpers ────────────────────────────────────────────────

    def _warn(self, msg: str) -> None:
        self._warnings.append(msg)

    def _error(self, msg: str) -> None:
        self._errors.append(msg)

    def _reset_logs(self) -> None:
        self._warnings = []
        self._errors = []
