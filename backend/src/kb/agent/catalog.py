"""The catalogue of chat models employees can pick from.

Adding a model is a one-line entry here; nothing else in the stack needs to know
about it. Each entry names the provider that serves it, which in turn names the
environment variable holding that provider's API key, so the UI can show a model
as unavailable (with the reason) instead of failing at request time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kb.config import get_settings

ProviderName = Literal["anthropic", "openai", "google"]

#: Which environment variable each provider's key comes from. Surfaced in the UI
#: so an operator can see exactly what to set to enable a model.
PROVIDER_ENV_VAR: dict[ProviderName, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

PROVIDER_LABEL: dict[ProviderName, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
}


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One selectable chat model."""

    id: str
    """Wire identifier passed to the provider SDK."""

    label: str
    """Human-readable name shown in the model picker."""

    provider: ProviderName
    description: str

    @property
    def env_var(self) -> str:
        return PROVIDER_ENV_VAR[self.provider]

    @property
    def provider_label(self) -> str:
        return PROVIDER_LABEL[self.provider]


#: Ordered as shown in the picker; the first available entry becomes the default.
MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="claude-opus-5",
        label="Claude Opus 5",
        provider="anthropic",
        description="Anthropic's most capable model for complex, multi-step questions.",
    ),
    ModelSpec(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        provider="anthropic",
        description="Fast and capable; a good default for everyday questions.",
    ),
    ModelSpec(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        provider="anthropic",
        description="Fastest and cheapest; best for simple lookups.",
    ),
    ModelSpec(
        id="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        provider="openai",
        description="OpenAI's fastest, most cost-efficient model.",
    ),
    ModelSpec(
        id="gpt-5.4",
        label="GPT-5.4",
        provider="openai",
        description="OpenAI's affordable model for coding and professional work.",
    ),
    ModelSpec(
        id="gemini-2.5-pro",
        label="Gemini 2.5 Pro",
        provider="google",
        description="Google's flagship model.",
    ),
    ModelSpec(
        id="gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        provider="google",
        description="Fast, cost-efficient Google model.",
    ),
)

MODELS_BY_ID: dict[str, ModelSpec] = {model.id: model for model in MODELS}


class ModelNotAvailableError(RuntimeError):
    """Raised when a model is unknown, or its provider has no API key configured."""


def provider_api_key(provider: ProviderName) -> str | None:
    """Return the configured key for a provider, or ``None`` when it is unset."""
    settings = get_settings()
    secret = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "google": settings.google_api_key,
    }[provider]
    value = secret.get_secret_value().strip() if secret else ""
    return value or None


def is_available(model: ModelSpec) -> bool:
    return provider_api_key(model.provider) is not None


def default_model() -> ModelSpec | None:
    """First model whose provider key is configured, in catalogue order."""
    return next((model for model in MODELS if is_available(model)), None)


def resolve(model_id: str, client_api_key: str | None = None) -> tuple[ModelSpec, str]:
    """Look up a model and its API key, with an actionable error if either is missing.

    ``client_api_key`` lets a caller bring their own key for this turn only: it is
    never persisted server-side, so a key entered in the browser's Settings page
    overrides (rather than requires) the operator's environment configuration.
    Returns the key alongside the spec so callers do not repeat the lookup, and
    so there is exactly one place that decides whether a model is usable.
    """
    model = MODELS_BY_ID.get(model_id)
    if model is None:
        known = ", ".join(sorted(MODELS_BY_ID))
        raise ModelNotAvailableError(f"Unknown model {model_id!r}. Available models: {known}.")
    api_key = (client_api_key or "").strip() or provider_api_key(model.provider)
    if api_key is None:
        raise ModelNotAvailableError(
            f"{model.label} is not configured: set {model.env_var} in the environment, "
            "or add a personal key in Settings."
        )
    return model, api_key
