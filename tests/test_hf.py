"""Tests for the HF fingerprint helpers — local I/O, path regex, and the read seam.

The read seam (``read_fingerprints``) is exercised hermetically by stubbing the two
``huggingface_hub`` calls it makes (``HfApi.list_repo_files`` + ``hf_hub_download``);
that drives this module's own filter/parse/validate logic without touching the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from huggingface_hub.errors import RepositoryNotFoundError

from sec_nport import hf
from sec_nport.errors import ExternalServiceError

# Unit-testing the source-path filter reaches into this private regex.
from sec_nport.hf import _SOURCE_RE  # pyright: ignore[reportPrivateUsage]
from sec_nport.sec import SourceFingerprint

if TYPE_CHECKING:
    from pathlib import Path


def test_write_local_fingerprint_roundtrips(tmp_path: Path):
    fp = SourceFingerprint(last_modified="Wed, 10 Jul 2024 12:00:00 GMT", content_length=12345)

    hf.write_local_fingerprint(tmp_path, "2024q1", fp)

    written = tmp_path / "_source" / "2024q1.json"
    assert written.exists()
    assert SourceFingerprint.model_validate_json(written.read_text()) == fp


def test_source_re_matches_only_fingerprint_paths():
    match = _SOURCE_RE.fullmatch("_source/2024q1.json")
    assert match is not None
    assert match.group(1) == "2024q1"

    assert _SOURCE_RE.fullmatch("SUBMISSION/year=2024/quarter=1/data.parquet") is None
    assert _SOURCE_RE.fullmatch("_source/README.md") is None
    assert _SOURCE_RE.fullmatch("_source/2024q5.json") is None  # no Q5


def test_read_fingerprints_filters_and_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fp = SourceFingerprint(last_modified="Wed, 10 Jul 2024 12:00:00 GMT", content_length=7)
    blob = tmp_path / "2024q1.json"
    _ = blob.write_text(fp.model_dump_json())
    # Only the fingerprint path should be read; parquet/README/_source-README are skipped.
    repo_files = [
        "_source/2024q1.json",
        "SUBMISSION/year=2024/quarter=1/data.parquet",
        "README.md",
        "_source/README.md",
    ]

    class FakeApi:
        def __init__(self, token: str | None = None) -> None:
            self._token = token

        def list_repo_files(self, repo_id: str, repo_type: str | None = None) -> list[str]:
            assert (repo_id, repo_type) == ("me/ds", "dataset")
            return repo_files

    def fake_download(
        repo_id: str, filename: str, repo_type: str | None = None, token: str | None = None
    ) -> str:
        assert filename == "_source/2024q1.json"  # never the parquet/README entries
        assert (repo_id, repo_type, token) == ("me/ds", "dataset", None)
        return str(blob)

    monkeypatch.setattr(hf, "HfApi", FakeApi)
    monkeypatch.setattr(hf, "hf_hub_download", fake_download)

    assert hf.read_fingerprints("me/ds") == {"2024q1": fp}


def test_read_fingerprints_empty_when_repo_absent(monkeypatch: pytest.MonkeyPatch):
    class FakeApi:
        def __init__(self, token: str | None = None) -> None:
            self._token = token

        def list_repo_files(self, repo_id: str, repo_type: str | None = None) -> list[str]:
            response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co"))
            raise RepositoryNotFoundError(f"missing {repo_id} ({repo_type})", response=response)

    monkeypatch.setattr(hf, "HfApi", FakeApi)

    assert hf.read_fingerprints("me/ds") == {}  # first-run: no repo yet


def test_read_fingerprints_drift_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    drifted = tmp_path / "2024q1.json"
    # Unknown field — extra="forbid" must reject it, wrapped as a domain error.
    _ = drifted.write_text('{"last_modified": "x", "content_length": 7, "etag": "surprise"}')

    class FakeApi:
        def __init__(self, token: str | None = None) -> None:
            self._token = token

        def list_repo_files(self, repo_id: str, repo_type: str | None = None) -> list[str]:
            assert (repo_id, repo_type) == ("me/ds", "dataset")
            return ["_source/2024q1.json"]

    def fake_download(
        repo_id: str, filename: str, repo_type: str | None = None, token: str | None = None
    ) -> str:
        assert filename == "_source/2024q1.json"
        return str(drifted)

    monkeypatch.setattr(hf, "HfApi", FakeApi)
    monkeypatch.setattr(hf, "hf_hub_download", fake_download)

    with pytest.raises(ExternalServiceError, match="drifted fingerprint"):
        _ = hf.read_fingerprints("me/ds")
