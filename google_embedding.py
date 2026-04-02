import os
import voyageai


class GoogleEmbedding:
    def __init__(self):
        self.client = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        self.model = "voyage-3"

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]

        result = self.client.embed(texts, model=self.model)
        return result.embeddings
