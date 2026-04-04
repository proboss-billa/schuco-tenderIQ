import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("tenderiq.clients")

# ── Guarded client initialization ────────────────────────────────────────────
# Each client is initialized in a try/except so one failure doesn't crash the
# entire app. Clients that fail to initialize are set to None; downstream code
# should check or use validate_clients() at startup.

_initialization_errors: list[str] = []


def initialize_pinecone():
    from pinecone import Pinecone
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise RuntimeError("PINECONE_API_KEY environment variable not set")

    index_name = os.getenv("PINECONE_INDEX", "tender-poc")
    target_dim = 1536

    pc = Pinecone(api_key=api_key)
    existing = [i.name for i in pc.list_indexes()]

    if index_name in existing:
        info = pc.describe_index(index_name)
        if info.dimension != target_dim:
            # SAFETY: Do NOT auto-delete — this causes unrecoverable data loss.
            raise RuntimeError(
                f"Pinecone index '{index_name}' has dimension {info.dimension}, "
                f"expected {target_dim}. Delete the index manually or create a "
                f"new one with the correct dimension."
            )

    if index_name not in existing:
        from pinecone import ServerlessSpec
        pc.create_index(
            name=index_name,
            dimension=target_dim,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    return pc.Index(index_name)


# ── Initialize each client independently ─────────────────────────────────────

pinecone_index = None
try:
    pinecone_index = initialize_pinecone()
except Exception as e:
    _initialization_errors.append(f"Pinecone: {e}")
    logger.error(f"[STARTUP] Pinecone initialization failed: {e}")

embedding_client = None
try:
    from google_embedding import GoogleEmbedding
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY environment variable not set")
    embedding_client = GoogleEmbedding()
except Exception as e:
    _initialization_errors.append(f"Embedding: {e}")
    logger.error(f"[STARTUP] Embedding client initialization failed: {e}")

gemini_client = None
try:
    from google import genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable not set")
    gemini_client = genai.Client(api_key=api_key)
except Exception as e:
    _initialization_errors.append(f"Gemini: {e}")
    logger.error(f"[STARTUP] Gemini client initialization failed: {e}")

anthropic_client = None
try:
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    anthropic_client = anthropic.Anthropic(api_key=api_key)
except Exception as e:
    _initialization_errors.append(f"Anthropic: {e}")
    logger.error(f"[STARTUP] Anthropic client initialization failed: {e}")


def validate_clients() -> list[str]:
    """Return list of initialization errors. Empty list = all clients OK."""
    return list(_initialization_errors)


def get_client_status() -> dict[str, str]:
    """Return initialization status of each client for health checks."""
    return {
        "pinecone": "ok" if pinecone_index is not None else "not initialized",
        "embedding": "ok" if embedding_client is not None else "not initialized",
        "gemini": "ok" if gemini_client is not None else "not initialized",
        "anthropic": "ok" if anthropic_client is not None else "not initialized",
    }
