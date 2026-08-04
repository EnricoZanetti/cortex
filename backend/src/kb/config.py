"""Application settings.

All configuration is environment-driven (12-factor). Secrets never have defaults:
a missing ``OPENAI_API_KEY`` or ``MCP_API_KEY`` fails at import time rather than at
the first request, so a misconfigured deployment cannot start and silently misbehave.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: A list setting that accepts either a JSON array or a plain comma-separated string in
#: the environment. ``NoDecode`` switches off pydantic-settings' automatic JSON parsing
#: (which raises on ``FOO=a,b``) so the validator below can handle both forms -- writing
#: ``CORS_ORIGINS=http://localhost:3000`` in a .env file must simply work.
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    """Runtime configuration shared by the REST API, the MCP server and the CLI scripts."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Storage -----------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://kb:kb@localhost:5432/kb",
        description="SQLAlchemy URL for the metadata store (Postgres).",
    )
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: SecretStr | None = Field(default=None)
    qdrant_collection: str = Field(default="kb_chunks")

    # --- Embeddings --------------------------------------------------------------
    embedding_provider: Literal["openai", "hash"] = Field(
        default="openai",
        description=(
            "'openai' for real semantic embeddings. 'hash' is an offline, deterministic "
            "stand-in that needs no API key: retrieval then relies on the lexical half of "
            "the hybrid search only. Use it for tests, CI and demos without credentials."
        ),
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Required when embedding_provider='openai'. Also enables the OpenAI chat models."
        ),
    )
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536, ge=64, le=4096)
    embedding_batch_size: int = Field(default=96, ge=1, le=2048)

    # --- Chunking ----------------------------------------------------------------
    chunk_target_tokens: int = Field(default=800, ge=100, le=4000)
    chunk_overlap_tokens: int = Field(default=120, ge=0, le=1000)
    chunk_min_tokens: int = Field(default=150, ge=0, le=1000)

    # --- Retrieval ---------------------------------------------------------------
    retrieval_candidate_pool: int = Field(
        default=40,
        ge=5,
        le=500,
        description="Candidates fetched from each retriever before fusion.",
    )
    rrf_k: int = Field(default=60, ge=1, description="Reciprocal Rank Fusion smoothing constant.")
    max_chunks_per_document: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Diversity cap: max chunks one document may contribute to an unscoped search.",
    )

    # --- Ingestion ---------------------------------------------------------------
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    storage_dir: str = Field(
        default="/data/uploads",
        description="Where original uploaded files are kept (for re-processing and download).",
    )

    # --- Chat assistant ------------------------------------------------------------
    # One key per provider. A provider with no key simply has its models greyed out in
    # the picker, so the app runs with any subset of these configured (including none).
    anthropic_api_key: SecretStr | None = Field(
        default=None, description="Enables the Claude chat models."
    )
    google_api_key: SecretStr | None = Field(
        default=None, description="Enables the Gemini chat models."
    )
    chat_max_tool_iterations: int = Field(
        default=8,
        ge=1,
        le=25,
        description=("Safety valve on the agent loop: rounds of tool calls allowed per question."),
    )
    chat_max_output_tokens: int = Field(default=4096, ge=256, le=32000)
    mcp_server_url: str = Field(
        default="http://localhost:8080/mcp",
        description=(
            "Where the chat agent reaches the MCP server. The assistant is a normal MCP "
            "client: it goes over HTTP with the same bearer token any other client uses, "
            "rather than importing the tools directly."
        ),
    )

    # --- MCP server --------------------------------------------------------------
    mcp_api_key: SecretStr = Field(description="Required: bearer token clients must present.")
    mcp_path: str = Field(default="/mcp")
    mcp_allowed_hosts: CsvList = Field(
        default_factory=lambda: ["*"],
        description="Host headers accepted by the MCP transport (DNS-rebinding protection).",
    )
    mcp_allowed_origins: CsvList = Field(default_factory=lambda: ["*"])

    # --- API ---------------------------------------------------------------------
    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = Field(default=True)

    @field_validator("database_url", mode="before")
    @classmethod
    def _force_psycopg_driver(cls, value: object) -> object:
        """Coerce bare postgres(ql):// URLs to the psycopg3 driver.

        Managed Postgres providers (e.g. Render's ``fromDatabase`` connection string)
        hand back a driverless URL, which makes SQLAlchemy default to psycopg2 -- a
        dependency this project doesn't install.
        """
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return "postgresql+psycopg://" + value[len("postgres://") :]
            if value.startswith("postgresql://"):
                return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @field_validator("mcp_allowed_hosts", "mcp_allowed_origins", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept both JSON lists and plain comma-separated strings from the environment."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _default_cors_scheme(cls, origins: list[str]) -> list[str]:
        """Coerce bare hostnames to https origins.

        Browsers send a scheme in the ``Origin`` header, so a bare host never matches.
        Render's ``fromService``/``property: host`` (see render.yaml) yields exactly that:
        a scheme-less hostname.
        """
        return [
            origin if origin == "*" or "://" in origin else f"https://{origin}"
            for origin in origins
        ]

    @model_validator(mode="after")
    def _default_mcp_scheme(self) -> Settings:
        """Coerce a bare MCP host to a full URL.

        Render's ``fromService``/``property: host`` (see render.yaml) yields a scheme-less
        hostname, and httpx refuses to open a session against one -- the same quirk
        ``_default_cors_scheme`` works around for ``cors_origins``. Render's private
        networking is plain HTTP, and the MCP server only answers on ``mcp_path``, so a
        bare host needs both a scheme and the path added.
        """
        url = self.mcp_server_url
        if "://" not in url:
            url = f"http://{url}"
        if not url.rstrip("/").endswith(self.mcp_path):
            url = url.rstrip("/") + self.mcp_path
        self.mcp_server_url = url
        return self

    @model_validator(mode="after")
    def _check_invariants(self) -> Settings:
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_TARGET_TOKENS")
        if self.embedding_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError(
                "OPENAI_API_KEY is required when EMBEDDING_PROVIDER='openai'. "
                "Set EMBEDDING_PROVIDER=hash to run without an API key."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]
