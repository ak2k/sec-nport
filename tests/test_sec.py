"""Tests for SEC discovery + download — hermetic via httpx.MockTransport."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from sec_nport import sec
from sec_nport.errors import ExternalServiceError, InputValidationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_make_client_requires_user_agent():
    with pytest.raises(InputValidationError):
        _ = sec.make_client(None)


def test_make_client_sets_user_agent():
    client = sec.make_client("sec-nport you@example.com")
    assert client.headers["User-Agent"] == "sec-nport you@example.com"
    client.close()


def test_make_client_requires_email_in_user_agent():
    with pytest.raises(InputValidationError):
        _ = sec.make_client("sec-nport no-email-here")


def test_403_error_mentions_user_agent():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with _client(handler) as client, pytest.raises(ExternalServiceError, match="SEC_USER_AGENT"):
        _ = sec.list_quarters(client)


def test_list_quarters_parses_and_dedups_links():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="a/2024q1_nport.zip b/2023q4_nport.zip c/2024q1_nport.zip",
        )

    with _client(handler) as client:
        assert sec.list_quarters(client) == ["2023q4", "2024q1"]


def test_list_quarters_wraps_http_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _client(handler) as client, pytest.raises(ExternalServiceError):
        _ = sec.list_quarters(client)


def test_download_quarter_writes_file(tmp_path: Path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ZIPDATA")

    dest = tmp_path / "2024q1_nport.zip"
    with _client(handler) as client:
        _ = sec.download_quarter(client, "2024q1", dest)
    assert dest.read_bytes() == b"ZIPDATA"


def test_download_retries_on_429_then_succeeds(tmp_path: Path):
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429)  # rate-limited once, then OK
        return httpx.Response(200, content=b"OK")

    dest = tmp_path / "2024q1_nport.zip"
    with _client(handler) as client:
        _ = sec.download_quarter(client, "2024q1", dest)
    assert attempts["n"] == 2
    assert dest.read_bytes() == b"OK"


def test_download_wraps_error_and_cleans_temp(tmp_path: Path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    dest = tmp_path / "2024q1_nport.zip"
    with _client(handler) as client, pytest.raises(ExternalServiceError):
        _ = sec.download_quarter(client, "2024q1", dest)
    assert not dest.exists()
    assert not dest.with_suffix(".zip.tmp").exists()


def test_quarter_parts():
    assert sec.quarter_parts("2024q1") == (2024, 1)
