"""
mistral_ocr.py
──────────────
Mistral OCR client for fast PDF text extraction.

Mistral's `mistral-ocr-latest` model is a dedicated OCR model that processes
an entire PDF in a single API call and returns per-page markdown. It's ~5-10x
faster than Gemini Vision for scanned text, tables, and forms, and supports
page-range filtering server-side (only pays for pages we care about).

Used as the primary OCR path in DrawingPDFParser — with Gemini Vision as a
fallback for pages that return low-yield results (typically technical drawings
where dense dimension annotations benefit from Gemini's richer prompt).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from mistralai import Mistral
from mistralai.models import DocumentURLChunk
from tenacity import retry, wait_exponential, stop_after_attempt

logger = logging.getLogger(__name__)

MISTRAL_OCR_MODEL = "mistral-ocr-latest"
# Pages returning fewer than this many characters from Mistral are considered
# "low-yield" and re-processed with Gemini Vision (likely drawings).
LOW_YIELD_THRESHOLD = 200


class MistralOCRClient:
    """Thin wrapper around Mistral OCR with file upload + page-range extraction."""

    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY not set — cannot use Mistral OCR")
        self.client = Mistral(api_key=api_key)

    @retry(
        wait=wait_exponential(multiplier=1, min=5, max=30),
        stop=stop_after_attempt(3),
        before_sleep=lambda rs: logger.warning(
            f"[MISTRAL_OCR] Retry {rs.attempt_number}: {rs.outcome.exception()}"
        ),
        reraise=True,
    )
    def extract_pages(
        self,
        pdf_path: str,
        page_indices: Optional[List[int]] = None,
    ) -> Dict[int, str]:
        """Run Mistral OCR on a PDF, returning {0-indexed page → markdown text}.

        If `page_indices` is provided, only those pages are processed (server-side
        filtering — significantly cheaper for docs where only some pages need OCR).

        Raises on persistent API failure so the caller can fall back to Gemini.
        """
        if not page_indices:
            logger.info(f"[MISTRAL_OCR] No pages to extract for {pdf_path}")
            return {}

        logger.info(
            f"[MISTRAL_OCR] Uploading {pdf_path} for OCR "
            f"({len(page_indices)} pages requested)"
        )

        # 1. Upload the PDF file
        with open(pdf_path, "rb") as f:
            uploaded = self.client.files.upload(
                file={
                    "file_name": os.path.basename(pdf_path),
                    "content": f.read(),
                },
                purpose="ocr",
            )

        # 2. Get a signed URL for the uploaded file
        signed = self.client.files.get_signed_url(file_id=uploaded.id, expiry=1)

        # 3. Call OCR with page-range filter (Mistral uses 0-indexed pages)
        try:
            response = self.client.ocr.process(
                model=MISTRAL_OCR_MODEL,
                document=DocumentURLChunk(document_url=signed.url),
                pages=page_indices,
            )
        finally:
            # Best-effort cleanup of the uploaded file
            try:
                self.client.files.delete(file_id=uploaded.id)
            except Exception as cleanup_err:
                logger.debug(f"[MISTRAL_OCR] File cleanup failed (non-fatal): {cleanup_err}")

        # 4. Build the page → markdown map
        results: Dict[int, str] = {}
        for page in response.pages:
            # page.index is 0-indexed (matches our page_indices)
            results[page.index] = page.markdown or ""

        total_chars = sum(len(v) for v in results.values())
        logger.info(
            f"[MISTRAL_OCR] Done: {len(results)} pages, {total_chars} total chars"
        )
        return results
