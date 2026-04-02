import os
import requests


BATCH_SIZE = 64   # Google batchEmbedContents supports up to 100; 64 is safe


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
            all_embeddings.extend(self._embed_batch(batch))

        return all_embeddings

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
        )
        resp.raise_for_status()
        return [item["values"] for item in resp.json()["embeddings"]]
