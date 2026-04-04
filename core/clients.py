import os
from dotenv import load_dotenv

from pinecone import Pinecone
from google import genai
from google_embedding import GoogleEmbedding
import anthropic

load_dotenv()


def initialize_pinecone():
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "tender-poc")
    target_dim = 1536

    pc = Pinecone(api_key=api_key)
    existing = [i.name for i in pc.list_indexes()]

    if index_name in existing:
        info = pc.describe_index(index_name)
        if info.dimension != target_dim:
            pc.delete_index(index_name)
            existing = []

    if index_name not in existing:
        from pinecone import ServerlessSpec
        pc.create_index(
            name=index_name,
            dimension=target_dim,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    return pc.Index(index_name)


# ── Shared service clients (stateless, safe to share) ────────────────────────
pinecone_index = initialize_pinecone()
embedding_client = GoogleEmbedding()
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
