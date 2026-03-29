import os

import voyageai


class VoyageEmbedding:
    def __init__(self):
        self.client = voyageai.Client(
            api_key=os.getenv("VOYAGE_API_KEY")
        )
        self.model = "voyage-2"

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]

        response = self.client.embed(
            texts=texts,
            model=self.model
        )
        return response.embeddings