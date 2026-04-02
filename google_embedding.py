import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

BATCH_SIZE = 64        # Google batchEmbedContents supports up to 100; 64 is safe
REQUEST_TIMEOUT = 120  # seconds — generous for large batches over slow connections
MAX_RETRIES = 4        # retry on 429 / 5xx before giving up
RETRY_BACKOFF = [2, 5, 15, 30]  # seconds between attempts


class GoogleEmbedding:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = "gemini-embedding-2-preview"
        self.batch_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models"
            f"/{self.model}:batchEmbedContents"
        )

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]

        all_embeddings = []

        # Process in batches of BATCH_SIZE to stay within API limits
        for batch_start in range(0, len(texts), BATCH_SIZE):
            batch = texts[batch_start: batch_start + BATCH_SIZE]
            all_embeddings.extend(self._embed_batch_with_retry(batch))

        return all_embeddings

    def _embed_batch_with_retry(self, texts):
        """Call batchEmbedContents with exponential backoff retry."""
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
                else:
                    raise  # non-retryable (4xx auth / bad request)
            except requests.exceptions.RequestException as e:
                last_exc = e
                logger.warning(f"[EMBED] Network error on attempt {attempt + 1}: {e}")

            if attempt >= MAX_RETRIES:
                break

        raise RuntimeError(
            f"[EMBED] All {MAX_RETRIES + 1} attempts failed. Last error: {last_exc}"
        )

    def _embed_batch(self, texts):
        """Call batchEmbedContents — one HTTP request for the entire batch."""
        requests_payload = [
            {
                "model": f"models/{self.model}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": 1536,
            }
            for t in texts
        ]

        resp = requests.post(
            self.batch_url,
            params={"key": self.api_key},
            json={"requests": requests_payload},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return [item["values"] for item in resp.json()["embeddings"]]
