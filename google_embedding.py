import os
import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

BATCH_SIZE = 100       # Google batchEmbedContents max — matched to EMBED_BATCH_SIZE
REQUEST_TIMEOUT = 120  # seconds — generous for large batches over slow connections
MAX_RETRIES = 4        # retry on 429 / 5xx before giving up
RETRY_BACKOFF = [2, 5, 15, 30]  # seconds between attempts
MAX_TEXT_BYTES = 9500   # Conservative limit per text (API allows ~10K tokens)
EMBED_DIM = 1536        # output dimensionality


class GoogleEmbedding:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = "gemini-embedding-2-preview"
        self.batch_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models"
            f"/{self.model}:batchEmbedContents"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]

        texts = self._sanitize_texts(texts)

        all_embeddings = []
        for batch_start in range(0, len(texts), BATCH_SIZE):
            batch = texts[batch_start: batch_start + BATCH_SIZE]
            all_embeddings.extend(self._embed_batch_with_retry(batch))

        return all_embeddings

    # ── Input sanitization ────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_texts(texts):
        """Clean texts before sending to embedding API.

        - Strip whitespace
        - Remove null bytes, replacement chars, and control characters
        - Replace empty strings with a placeholder (API rejects empty text)
        - Truncate texts that exceed the byte limit
        """
        sanitized = []
        for i, t in enumerate(texts):
            # Remove null bytes, Unicode replacement char, and ASCII control chars
            t = t.replace('\x00', '').replace('\ufffd', '')
            t = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f]', '', t)
            t = t.strip()

            if not t:
                t = "[empty section]"

            # Truncate if over byte limit
            encoded = t.encode('utf-8', errors='replace')
            if len(encoded) > MAX_TEXT_BYTES:
                t = encoded[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore').strip()
                logger.warning(f"[EMBED] Text at index {i} truncated to {len(t)} chars")

            sanitized.append(t)
        return sanitized

    # ── Retry logic ───────────────────────────────────────────────────────────

    def _embed_batch_with_retry(self, texts):
        """Call batchEmbedContents with exponential backoff retry.

        400 errors trigger a binary-split fallback: the batch is halved and each
        half retried independently, recursing down to individual texts.  A single
        text that still returns 400 gets a zero-vector placeholder so one bad
        chunk never kills an entire document.
        """
        last_exc = None
        for attempt, wait in enumerate([0] + RETRY_BACKOFF):
            if wait:
                logger.info(f"[EMBED] Retry {attempt}/{MAX_RETRIES} — waiting {wait}s…")
                time.sleep(wait)
            try:
                return self._embed_batch(texts)
            except requests.exceptions.Timeout:
                last_exc = TimeoutError(f"Embedding API timed out after {REQUEST_TIMEOUT}s")
                logger.warning(f"[EMBED] Timeout on attempt {attempt + 1}")
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 429 or status >= 500:
                    last_exc = e
                    logger.warning(f"[EMBED] HTTP {status} on attempt {attempt + 1}")
                elif status == 400:
                    # ── Split-retry: isolate the bad text(s) ──────────────
                    return self._split_retry_on_400(texts, e)
                else:
                    # Log body for unexpected 4xx before raising
                    self._log_error_body(e)
                    raise
            except requests.exceptions.RequestException as e:
                last_exc = e
                logger.warning(f"[EMBED] Network error on attempt {attempt + 1}: {e}")

            if attempt >= MAX_RETRIES:
                break

        raise RuntimeError(
            f"[EMBED] All {MAX_RETRIES + 1} attempts failed. Last error: {last_exc}"
        )

    def _split_retry_on_400(self, texts, original_error):
        """Binary-split a batch that returned 400 to isolate bad text(s)."""
        if len(texts) == 1:
            # Single text still causes 400 — return zero vector placeholder
            logger.error(
                f"[EMBED] 400 on single text (len={len(texts[0])}): "
                f"{texts[0][:200]!r} — returning zero vector"
            )
            self._log_error_body(original_error)
            return [[0.0] * EMBED_DIM]

        logger.warning(
            f"[EMBED] 400 on batch of {len(texts)} — splitting to isolate bad text"
        )
        mid = len(texts) // 2
        left = self._embed_batch_with_retry(texts[:mid])
        right = self._embed_batch_with_retry(texts[mid:])
        return left + right

    # ── Raw API call ──────────────────────────────────────────────────────────

    def _embed_batch(self, texts):
        """Call batchEmbedContents — one HTTP request for the entire batch."""
        requests_payload = [
            {
                "model": f"models/{self.model}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": EMBED_DIM,
            }
            for t in texts
        ]

        resp = requests.post(
            self.batch_url,
            params={"key": self.api_key},
            json={"requests": requests_payload},
            timeout=REQUEST_TIMEOUT,
        )

        # Log error body before raising so we can debug 400s
        if resp.status_code != 200:
            try:
                error_body = resp.json()
            except Exception:
                error_body = resp.text[:500]
            logger.error(
                f"[EMBED] HTTP {resp.status_code} | batch={len(texts)} | "
                f"text_lengths={[len(t) for t in texts[:5]]}… | "
                f"response: {error_body}"
            )

        resp.raise_for_status()
        return [item["values"] for item in resp.json()["embeddings"]]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _log_error_body(http_error):
        """Extract and log the response body from an HTTPError."""
        try:
            body = http_error.response.json()
        except Exception:
            try:
                body = http_error.response.text[:500]
            except Exception:
                body = str(http_error)
        logger.error(f"[EMBED] Error detail: {body}")
