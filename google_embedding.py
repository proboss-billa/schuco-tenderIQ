import os
import requests


class GoogleEmbedding:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = "gemini-embedding-001"
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent"

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]

        embeddings = []
        for text in texts:
            resp = requests.post(
                self.url,
                params={"key": self.api_key},
                json={
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": 768,
                },
            )
            resp.raise_for_status()
            embeddings.append(resp.json()["embedding"]["values"])
        return embeddings
