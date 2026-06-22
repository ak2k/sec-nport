"""Discover and download SEC Form N-PORT quarterly data sets.

The SEC's fair-access policy requires a descriptive ``User-Agent`` carrying a
contact address; set ``SEC_USER_AGENT`` (e.g. ``"sec-nport you@example.com"``).
Requests without one are rejected.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger("sync.sec")

_DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"
_FILE_URL = "https://www.sec.gov/files/dera/data/form-n-port-data-sets/{quarter}_nport.zip"

_QUARTER_RE = re.compile(r"(\d{4}q[1-4])_nport\.zip")


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError(
            "Set SEC_USER_AGENT (e.g. 'sec-nport you@example.com'). The SEC "
            "fair-access policy requires a User-Agent with contact info."
        )
    return ua


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent()
    return s


def list_quarters(sess: requests.Session | None = None) -> list[str]:
    """Return the available quarters (e.g. ``["2019q3", ..., "2026q1"]``)."""
    sess = sess or session()
    resp = sess.get(_DATASETS_PAGE, timeout=60)
    resp.raise_for_status()
    return sorted(set(_QUARTER_RE.findall(resp.text)))


def download_quarter(
    quarter: str, dest: str | Path, sess: requests.Session | None = None
) -> Path:
    """Download ``<quarter>_nport.zip`` to ``dest`` (atomic via a temp file)."""
    sess = sess or session()
    dest = Path(dest)
    url = _FILE_URL.format(quarter=quarter)
    log.info("downloading %s", url)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with sess.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    log.info("downloaded %s (%.0f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def quarter_parts(quarter: str) -> tuple[int, int]:
    """``"2024q1"`` -> ``(2024, 1)``."""
    return int(quarter[:4]), int(quarter[5])
