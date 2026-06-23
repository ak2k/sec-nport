"""Tests for the CLI detect logic — hermetic via httpx.MockTransport + monkeypatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from sec_nport import hf, sec

# Unit-testing the detect logic reaches into this private helper.
from sec_nport.cli import _pending, seed  # pyright: ignore[reportPrivateUsage]
from sec_nport.config import Settings
from sec_nport.sec import SourceFingerprint

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    Handler = Callable[[httpx.Request], httpx.Response]

# Per-quarter fingerprints the (mocked) SEC HEAD returns.
_HEAD: dict[str, SourceFingerprint] = {
    "2024q1": SourceFingerprint(last_modified="Mon, 01 Apr 2024 00:00:00 GMT", content_length=100),
    "2024q2": SourceFingerprint(last_modified="Tue, 02 Jul 2024 00:00:00 GMT", content_length=200),
    "2024q3": SourceFingerprint(last_modified="Wed, 02 Oct 2024 00:00:00 GMT", content_length=300),
}


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _head_handler(request: httpx.Request) -> httpx.Response:
    quarter = request.url.path.split("/")[-1].removesuffix("_nport.zip")
    fp = _HEAD[quarter]
    return httpx.Response(
        200,
        headers={"Last-Modified": fp.last_modified, "Content-Length": str(fp.content_length)},
    )


def _settings() -> Settings:
    return Settings(hf_repo_id="me/ds", hf_token=None)


def _patch_detect(
    monkeypatch: pytest.MonkeyPatch, present: set[str], stored: dict[str, SourceFingerprint]
) -> None:
    def fake_present(_repo: str, _token: str | None = None) -> set[str]:
        return present

    def fake_read(_repo: str, _token: str | None = None) -> dict[str, SourceFingerprint]:
        return stored

    monkeypatch.setattr(hf, "existing_quarters", fake_present)
    monkeypatch.setattr(hf, "read_fingerprints", fake_read)


def test_pending_selects_changed_and_new(monkeypatch: pytest.MonkeyPatch):
    # Stored: 2024q1 unchanged, 2024q2 differs (re-post); 2024q3 absent on HF (new).
    _patch_detect(
        monkeypatch,
        present={"2024q1", "2024q2"},
        stored={
            "2024q1": _HEAD["2024q1"],
            "2024q2": SourceFingerprint(last_modified="STALE", content_length=999),
        },
    )

    with _client(_head_handler) as client:
        pending = _pending(_settings(), client, ["2024q1", "2024q2", "2024q3"])

    assert pending == ["2024q2", "2024q3"]


def test_pending_empty_when_all_unchanged(monkeypatch: pytest.MonkeyPatch):
    _patch_detect(monkeypatch, present={"2024q1", "2024q2", "2024q3"}, stored=dict(_HEAD))

    with _client(_head_handler) as client:
        pending = _pending(_settings(), client, ["2024q1", "2024q2", "2024q3"])

    assert pending == []


def test_pending_rebuilds_quarter_present_without_partition(monkeypatch: pytest.MonkeyPatch):
    # Fingerprint matches but the partition is absent (a stranded partial upload):
    # the presence gate must still flag it for rebuild.
    _patch_detect(monkeypatch, present=set(), stored={"2024q1": _HEAD["2024q1"]})

    with _client(_head_handler) as client:
        pending = _pending(_settings(), client, ["2024q1"])

    assert pending == ["2024q1"]


def test_pending_skips_unavailable_quarter(monkeypatch: pytest.MonkeyPatch):
    # 2024q2's ZIP isn't fetchable yet (404); it must not abort detection of 2024q1.
    def handler(request: httpx.Request) -> httpx.Response:
        quarter = request.url.path.split("/")[-1].removesuffix("_nport.zip")
        if quarter == "2024q2":
            return httpx.Response(404)
        fp = _HEAD[quarter]
        return httpx.Response(
            200,
            headers={"Last-Modified": fp.last_modified, "Content-Length": str(fp.content_length)},
        )

    _patch_detect(monkeypatch, present=set(), stored={})

    with _client(handler) as client:
        pending = _pending(_settings(), client, ["2024q1", "2024q2"])

    assert pending == ["2024q1"]


def test_seed_writes_and_uploads_only_missing(monkeypatch: pytest.MonkeyPatch):
    # 2024q1 already seeded; seed must fingerprint only the missing q2/q3 and upload once.
    def fake_get_settings() -> Settings:
        return _settings()

    def fake_make_client(_user_agent: str | None) -> httpx.Client:
        return _client(_head_handler)

    def fake_present(_repo: str, _token: str | None = None) -> set[str]:
        return {"2024q1", "2024q2", "2024q3"}

    def fake_read(_repo: str, _token: str | None = None) -> dict[str, SourceFingerprint]:
        return {"2024q1": _HEAD["2024q1"]}

    def fake_head(_client: httpx.Client, quarter: str) -> SourceFingerprint:
        return _HEAD[quarter]

    written: list[str] = []

    def fake_write(_data_dir: Path, quarter: str, _fp: SourceFingerprint) -> None:
        written.append(quarter)

    def fake_ensure(_repo: str, _token: str | None = None) -> None:
        return None

    uploaded: list[str] = []

    def fake_upload(repo_id: str, _local_dir: Path, _token: str | None = None) -> None:
        uploaded.append(repo_id)

    monkeypatch.setattr("sec_nport.cli.get_settings", fake_get_settings)
    monkeypatch.setattr(sec, "make_client", fake_make_client)
    monkeypatch.setattr(sec, "head_quarter", fake_head)
    monkeypatch.setattr(hf, "existing_quarters", fake_present)
    monkeypatch.setattr(hf, "read_fingerprints", fake_read)
    monkeypatch.setattr(hf, "write_local_fingerprint", fake_write)
    monkeypatch.setattr(hf, "ensure_repo", fake_ensure)
    monkeypatch.setattr(hf, "upload", fake_upload)

    seed()

    assert sorted(written) == ["2024q2", "2024q3"]
    assert uploaded == ["me/ds"]
