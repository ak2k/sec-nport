"""Typed configuration sourced from the environment.

Follows the canonical pydantic-settings shape: `extra="forbid"` + `frozen=True`
so accidental env vars and mid-run mutation fail loudly. Field names map to
upper-case env vars (case-insensitive), so `sec_user_agent` reads
`SEC_USER_AGENT` — the conventional, un-prefixed names the SEC and HuggingFace
ecosystems already use.

`sec_user_agent` and `hf_token` are optional because convert-only runs need
neither; the code that performs a download or upload validates their presence
and raises a domain error if missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["debug", "info", "warning", "error"]


class Settings(BaseSettings):
    """Application settings sourced from environment + `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    sec_user_agent: str | None = Field(
        default=None,
        description="User-Agent with contact info; required by the SEC fair-access policy.",
    )
    hf_token: str | None = Field(
        default=None,
        description="HuggingFace write token (only needed for upload).",
    )
    hf_repo_id: str = Field(
        default="ak2k/sec-nport",
        description="Target HuggingFace dataset repo id.",
    )
    data_dir: Path = Field(
        default=Path("data"),
        description="Output directory for the partitioned Parquet tree.",
    )
    work_dir: Path = Field(
        default=Path(".work"),
        description="Scratch directory for downloaded ZIPs.",
    )
    memory_limit: str | None = Field(
        default=None,
        description="DuckDB memory limit, e.g. '8GB' (default: unbounded).",
    )
    log_level: LogLevel = Field(default="info", description="Application log level.")
    log_json: bool = Field(default=False, description="Emit JSON logs (production).")


def get_settings() -> Settings:
    """Construct Settings; BaseSettings populates fields from the environment."""
    return Settings()
