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
    "gemini-flash": {
        "provider": "google",
        "model_id": "gemini-2.0-flash",
        "display_name": "Gemini 2.0 Flash",
        "max_response_tokens": 8_192,
        "context_window_tokens": 120_000,
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
