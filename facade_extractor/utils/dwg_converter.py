"""
dwg_converter.py
────────────────
Wraps the ODA File Converter CLI to convert .dwg → .dxf.

ODA File Converter (free):
  https://www.opendesign.com/guestfiles/oda_file_converter

Install:
  Linux/macOS: download and extract ODAFileConverter binary
  Windows:     install ODAFileConverter_*.exe, default path used

Usage:
  converter = DWGConverter()
  dxf_path = converter.convert(Path("drawing.dwg"))
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Default ODA CLI paths per platform
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_ODA_PATHS: dict[str, list[str]] = {
    "Linux": [
        "/usr/bin/ODAFileConverter",
        "/opt/ODAFileConverter/ODAFileConverter",
        "ODAFileConverter",   # PATH
    ],
    "Darwin": [
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "ODAFileConverter",
    ],
    "Windows": [
        r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        "ODAFileConverter.exe",
    ],
}


class DWGConverter:

    def __init__(self, oda_path: Optional[str] = None):
        """
        Parameters
        ----------
        oda_path : Optional explicit path to ODAFileConverter binary.
                   If None, auto-detected from platform defaults.
        """
        self.oda_path = oda_path or self._find_oda()

    # ── Public API ────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if ODA CLI is accessible."""
        return self.oda_path is not None and self._check_binary()

    def convert(
        self,
        dwg_path: Path,
        output_dir: Optional[Path] = None,
        dxf_version: str = "ACAD2018",
    ) -> Optional[Path]:
        """
        Convert a .dwg file to .dxf.

        Parameters
        ----------
        dwg_path   : path to the .dwg file
        output_dir : directory to write the .dxf; defaults to same dir as input
        dxf_version: ODA format string e.g. "ACAD2018", "ACAD2013", "ACAD2010"

        Returns
        -------
        Path to the produced .dxf file, or None on failure.
        """
        if not self.is_available():
            return None

        dwg_path = Path(dwg_path).resolve()
        if not dwg_path.exists():
            return None

        if output_dir is None:
            output_dir = dwg_path.parent
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # ODA CLI syntax:
        # ODAFileConverter <input_dir> <output_dir> <in_ver> <out_ver> <recursive> <audit> [filter]
        cmd = [
            self.oda_path,
            str(dwg_path.parent),    # input folder
            str(output_dir),         # output folder
            "ACAD",                  # input version (auto-detect)
            dxf_version,             # output version
            "0",                     # recurse: 0=no
            "1",                     # audit: 1=yes
            dwg_path.name,           # filter: only convert this file
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return None

        if result.returncode != 0:
            return None

        # Find output .dxf
        dxf_name = dwg_path.stem + ".dxf"
        dxf_path = output_dir / dxf_name
        return dxf_path if dxf_path.exists() else None

    def convert_to_temp(self, dwg_path: Path, dxf_version: str = "ACAD2018") -> Optional[Path]:
        """Convert to a temporary directory and return the .dxf path."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self.convert(dwg_path, Path(tmp), dxf_version)
            if result and result.exists():
                # Copy to a stable temp location outside the context manager
                import shutil
                stable = Path(tempfile.mkdtemp()) / result.name
                shutil.copy2(result, stable)
                return stable
        return None

    # ── Binary detection ──────────────────────────────────────────────────────

    def _find_oda(self) -> Optional[str]:
        system = platform.system()
        candidates = _DEFAULT_ODA_PATHS.get(system, ["ODAFileConverter"])
        for path in candidates:
            if Path(path).exists() or _which(path):
                return path
        return None

    def _check_binary(self) -> bool:
        """Verify the binary exists and is executable."""
        if not self.oda_path:
            return False
        p = Path(self.oda_path)
        if p.exists():
            return True
        return bool(_which(self.oda_path))


def _which(name: str) -> Optional[str]:
    """Cross-platform shutil.which wrapper."""
    import shutil
    return shutil.which(name)
