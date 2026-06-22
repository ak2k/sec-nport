"""Discover and download SEC Form N-PORT quarterly data sets.

The SEC fair-access policy rejects requests without a descriptive
``User-Agent`` carrying a contact address, so :func:`make_client` requires one.
Transport and HTTP failures are wrapped as domain errors; ``httpx`` exceptions
never leak to callers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
import structlog

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


def make_client(user_agent: str | None) -> httpx.Client:
    """Build an httpx client carrying the SEC-required User-Agent.

    Raises:
        InputValidationError: if ``user_agent`` is missing.

    """
    if not user_agent:
        raise InputValidationError(
            "SEC_USER_AGENT is required (e.g. 'sec-nport you@example.com'); the "
            "SEC fair-access policy rejects requests without contact info."
        )
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=_TIMEOUT, follow_redirects=True)


def list_quarters(client: httpx.Client) -> list[str]:
    """Return the available quarters, e.g. ``["2019q4", ..., "2026q1"]``.

    Raises:
        ExternalServiceError: on transport or HTTP failure.

    """
    try:
        response = client.get(_DATASETS_PAGE)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"failed to list N-PORT quarters: {exc}") from exc
    return sorted(set(_QUARTER_RE.findall(response.text)))


def download_quarter(client: httpx.Client, quarter: str, dest: Path) -> Path:
    """Download ``<quarter>_nport.zip`` to ``dest`` (atomic via a temp file).

    Raises:
        ExternalServiceError: on transport or HTTP failure.

    """
    url = _FILE_URL.format(quarter=quarter)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log.info("download.start", quarter=quarter, url=url)
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(1 << 20):
                    _ = fh.write(chunk)
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise ExternalServiceError(f"failed to download {quarter}: {exc}") from exc
    _ = tmp.replace(dest)
    log.info("download.done", quarter=quarter, mb=round(dest.stat().st_size / 1e6))
    return dest


def quarter_parts(quarter: str) -> tuple[int, int]:
    """``"2024q1"`` -> ``(2024, 1)``."""
    return int(quarter[:4]), int(quarter[5])
