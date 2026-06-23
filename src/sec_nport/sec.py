"""Discover and download SEC Form N-PORT quarterly data sets.

The SEC fair-access policy rejects requests without a descriptive
``User-Agent`` carrying a contact address, so :func:`make_client` requires one.
Transport and HTTP failures are wrapped as domain errors; ``httpx`` exceptions
never leak to callers.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import stamina
import structlog
from pydantic import BaseModel, ConfigDict

from sec_nport.errors import ExternalServiceError, InputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from structlog.stdlib import BoundLogger

# structlog.get_logger() returns Any; the annotation narrows it for callers.
log: BoundLogger = structlog.get_logger(__name__)  # pyright: ignore[reportAny]

_DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"
_FILE_URL = "https://www.sec.gov/files/dera/data/form-n-port-data-sets/{quarter}_nport.zip"
_QUARTER_RE = re.compile(r"(\d{4}q[1-4])_nport\.zip")
_TIMEOUT = httpx.Timeout(60.0, read=600.0)

# The SEC rate-limits (429) bursts of large-file downloads and may return
# transient 5xx; back off and retry those (but not 403/404 — a UA or path fix,
# not a wait).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


@stamina.retry(on=_is_retryable, attempts=6, wait_initial=2.0, wait_max=120.0)
def _get_text(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    _ = response.raise_for_status()
    return response.text


@stamina.retry(on=_is_retryable, attempts=6, wait_initial=2.0, wait_max=120.0)
def _stream_to_file(client: httpx.Client, url: str, dest: Path) -> None:
    with client.stream("GET", url) as response:
        _ = response.raise_for_status()
        with dest.open("wb") as fh:  # truncates on each (re)try — clean restart
            for chunk in response.iter_bytes(1 << 20):
                _ = fh.write(chunk)


@stamina.retry(on=_is_retryable, attempts=6, wait_initial=2.0, wait_max=120.0)
def _head(client: httpx.Client, url: str) -> httpx.Response:
    response = client.head(url)
    _ = response.raise_for_status()
    return response


def _service_error(what: str, exc: httpx.HTTPError) -> ExternalServiceError:
    """Wrap an httpx error, adding UA guidance on the SEC's WAF 403."""
    hint = ""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == HTTPStatus.FORBIDDEN:
        hint = (
            " (403 — the SEC's fair-access WAF rejects a User-Agent without a "
            "contact email or one containing 'github'; check SEC_USER_AGENT)"
        )
    return ExternalServiceError(f"{what}: {exc}{hint}")


class SourceFingerprint(BaseModel):
    """Identity of a SEC source ZIP, used to detect re-posts.

    ``(Last-Modified, Content-Length)`` from the SEC's static file server. A
    change in either means the ZIP was re-published and the quarter needs
    reprocessing. ``frozen`` so it compares by value (the change-detection test);
    ``extra="forbid"`` so a drifted ``_source/<q>.json`` fails loud on read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    last_modified: str
    content_length: int


def make_client(user_agent: str | None) -> httpx.Client:
    """Build an httpx client carrying the SEC-required User-Agent.

    The SEC fair-access WAF requires a contact email in the User-Agent (and, in
    practice, rejects ones containing ``github``), so a missing email fails fast
    here rather than as an opaque 403 mid-run.

    Raises:
        InputValidationError: if ``user_agent`` is missing or has no email.

    """
    if not user_agent or "@" not in user_agent:
        raise InputValidationError(
            "SEC_USER_AGENT must be '<name> <contact-email>' (e.g. "
            "'sec-nport you@example.com'); the SEC fair-access policy requires a "
            "contact email and rejects User-Agents containing 'github'."
        )
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=_TIMEOUT, follow_redirects=True)


def list_quarters(client: httpx.Client) -> list[str]:
    """Return the available quarters, e.g. ``["2019q4", ..., "2026q1"]``.

    Raises:
        ExternalServiceError: on transport or HTTP failure.

    """
    try:
        text = _get_text(client, _DATASETS_PAGE)
    except httpx.HTTPError as exc:
        raise _service_error("failed to list N-PORT quarters", exc) from exc
    return sorted(set(_QUARTER_RE.findall(text)))


def download_quarter(client: httpx.Client, quarter: str, dest: Path) -> Path:
    """Download ``<quarter>_nport.zip`` to ``dest`` (atomic via a temp file).

    Raises:
        ExternalServiceError: on transport or HTTP failure.

    """
    url = _FILE_URL.format(quarter=quarter)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log.info("download.start", quarter=quarter, url=url)
    try:
        _stream_to_file(client, url, tmp)
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise _service_error(f"failed to download {quarter}", exc) from exc
    _ = tmp.replace(dest)
    log.info("download.done", quarter=quarter, mb=round(dest.stat().st_size / 1e6))
    return dest


def head_quarter(client: httpx.Client, quarter: str) -> SourceFingerprint:
    """Fingerprint ``<quarter>_nport.zip`` via an HTTP HEAD (no download).

    Raises:
        ExternalServiceError: on transport/HTTP failure, or if the response
            omits the headers needed to form a fingerprint.

    """
    url = _FILE_URL.format(quarter=quarter)
    try:
        response = _head(client, url)
    except httpx.HTTPError as exc:
        raise _service_error(f"failed to fingerprint {quarter}", exc) from exc
    last_modified = response.headers.get("last-modified")
    content_length = response.headers.get("content-length")
    if not last_modified or content_length is None or not content_length.isdigit():
        raise ExternalServiceError(
            f"cannot fingerprint {quarter}: HEAD response lacks Last-Modified or "
            f"Content-Length (last-modified={last_modified!r}, "
            f"content-length={content_length!r})"
        )
    return SourceFingerprint(last_modified=last_modified, content_length=int(content_length))


def quarter_parts(quarter: str) -> tuple[int, int]:
    """``"2024q1"`` -> ``(2024, 1)``."""
    return int(quarter[:4]), int(quarter[5])
