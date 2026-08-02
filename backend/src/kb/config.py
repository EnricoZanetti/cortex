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
        description="Required when embedding_provider='openai'.",
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
