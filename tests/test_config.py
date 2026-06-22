"""Tests for environment-driven settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_nport.config import get_settings

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("SEC_USER_AGENT", "sec-nport you@example.com")
    monkeypatch.setenv("HF_REPO_ID", "me/ds")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    settings = get_settings()
    assert settings.sec_user_agent == "sec-nport you@example.com"
    assert settings.hf_repo_id == "me/ds"
    assert settings.hf_token is None  # optional — only needed for upload


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    for var in ("SEC_USER_AGENT", "HF_TOKEN", "HF_REPO_ID"):
        monkeypatch.delenv(var, raising=False)

    settings = get_settings()
    assert settings.hf_repo_id == "ak2k/sec-nport"
    assert settings.sec_user_agent is None
    assert settings.log_level == "info"
