"""Available LLM models for parameter extraction."""

AVAILABLE_MODELS = {
    "claude-opus-4": {
        "provider": "anthropic",
        "model_id": "claude-opus-4-20250514",
        "display_name": "Claude Opus 4",
        "max_response_tokens": 32_000,
        "context_window_tokens": 120_000,
    },
    "claude-sonnet-4": {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-20250514",
        "display_name": "Claude Sonnet 4",
        "max_response_tokens": 16_000,
        "context_window_tokens": 120_000,
    },
    "claude-haiku-3.5": {
        "provider": "anthropic",
        "model_id": "claude-3-5-haiku-20241022",
        "display_name": "Claude Haiku 3.5",
        "max_response_tokens": 8_192,
        "context_window_tokens": 120_000,
    },
    "gemini-2.5-flash": {
        "provider": "google",
        "model_id": "gemini-2.5-flash",
        "display_name": "Gemini 2.5 Flash",
        "max_response_tokens": 16_384,
        "context_window_tokens": 120_000,
    },
    "gemini-3-flash": {
        "provider": "google",
        "model_id": "gemini-3-flash-preview",
        "display_name": "Gemini 3 Flash",
        "max_response_tokens": 16_384,
        "context_window_tokens": 120_000,
    },
    "gemini-3.1-pro": {
        "provider": "google",
        "model_id": "gemini-3.1-pro-preview",
        "display_name": "Gemini 3.1 Pro",
        "max_response_tokens": 32_000,
        "context_window_tokens": 120_000,
    },
    "gpt-5.4": {
        "provider": "openai",
        "model_id": "gpt-5.4-2026-03-05",
        "display_name": "GPT-5.4",
        "max_response_tokens": 16_000,
        "context_window_tokens": 128_000,
    },
    "gpt-5.4-nano": {
        "provider": "openai",
        "model_id": "gpt-5.4-nano-2026-03-17",
        "display_name": "GPT-5.4 Nano",
        "max_response_tokens": 8_192,
        "context_window_tokens": 128_000,
    },
    "gpt-5-mini": {
        "provider": "openai",
        "model_id": "gpt-5-mini-2025-08-07",
        "display_name": "GPT-5 Mini",
        "max_response_tokens": 8_192,
        "context_window_tokens": 128_000,
    },
}

DEFAULT_MODEL = "claude-opus-4"


def get_model_config(model_key: str) -> dict:
    """Return model config for given key, falling back to default."""
    return AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS[DEFAULT_MODEL])


def list_models() -> list[dict]:
    """Return list of models for frontend dropdown."""
    return [
        {"key": key, "display_name": cfg["display_name"], "provider": cfg["provider"]}
        for key, cfg in AVAILABLE_MODELS.items()
    ]
