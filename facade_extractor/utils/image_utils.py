"""
image_utils.py
──────────────
DPI conversion helpers, binarisation, and sharpening utilities
shared by pdf_raster_parser.py.
"""
from __future__ import annotations

MM_PER_INCH = 25.4


def dpi_to_mm_per_pixel(dpi: int) -> float:
    """Return mm per pixel at a given DPI."""
    return MM_PER_INCH / dpi


def pixels_to_mm(pixels: float, dpi: int) -> float:
    return pixels * dpi_to_mm_per_pixel(dpi)


def mm_to_pixels(mm: float, dpi: int) -> float:
    return mm / dpi_to_mm_per_pixel(dpi)


def crop_with_padding(
    image: "np.ndarray",
    x: int, y: int,
    width: int, height: int,
    pad: int = 30,
) -> "np.ndarray":
    """Crop a region with padding, clamped to image bounds."""
    import numpy as np
    h, w = image.shape[:2]
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(w, x + width  + pad)
    y1 = min(h, y + height + pad)
    return image[y0:y1, x0:x1]


def upscale_2x(image: "np.ndarray") -> "np.ndarray":
    import cv2
    return cv2.resize(
        image,
        (image.shape[1] * 2, image.shape[0] * 2),
        interpolation=cv2.INTER_CUBIC,
    )


def unsharp_mask(image: "np.ndarray", sigma: float = 1.0, strength: float = 1.5) -> "np.ndarray":
    import cv2
    blurred = cv2.GaussianBlur(image, (0, 0), sigma)
    return cv2.addWeighted(image, 1 + strength, blurred, -strength, 0)


def binarise_otsu(image: "np.ndarray") -> "np.ndarray":
    import cv2
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary
