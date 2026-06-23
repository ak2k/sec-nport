"""Reconcile the local Parquet tree with the HuggingFace dataset repo.

Upload is additive and content-addressed (``upload_large_folder`` hashes each
file and skips those already present), so re-runs are cheap and resumable.
``huggingface_hub`` errors are wrapped as domain errors at this boundary.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from huggingface_hub import HfApi, create_repo, hf_hub_download
from huggingface_hub.errors import HfHubHTTPError, RepositoryNotFoundError
from pydantic import ValidationError

from sec_nport.errors import ExternalServiceError
from sec_nport.sec import SourceFingerprint

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

log: BoundLogger = structlog.get_logger(__name__)  # pyright: ignore[reportAny]

_PARTITION_RE = re.compile(r"year=(\d{4})/quarter=([1-4])/")
_SOURCE_RE = re.compile(r"_source/(\d{4}q[1-4])\.json")


def ensure_repo(repo_id: str, token: str | None = None) -> None:
    """Create the dataset repo if it does not already exist."""
    try:
        _ = create_repo(repo_id, repo_type="dataset", exist_ok=True, token=token)
    except HfHubHTTPError as exc:
        raise ExternalServiceError(f"failed to ensure dataset repo {repo_id}: {exc}") from exc


def existing_quarters(repo_id: str, token: str | None = None) -> set[str]:
    """Quarters already present on the remote (derived from partition paths)."""
    try:
        files = HfApi(token=token).list_repo_files(repo_id, repo_type="dataset")
    except RepositoryNotFoundError:
        return set()
    except HfHubHTTPError as exc:
        raise ExternalServiceError(f"failed to list {repo_id}: {exc}") from exc
    quarters: set[str] = set()
    for path in files:
        match = _PARTITION_RE.search(path)
        if match:
            quarters.add(f"{match.group(1)}q{match.group(2)}")
    return quarters


def read_fingerprints(repo_id: str, token: str | None = None) -> dict[str, SourceFingerprint]:
    """Stored source fingerprints keyed by quarter (``_source/<q>.json`` on HF).

    Empty when the repo (or its ``_source/`` tree) does not exist yet (first run).

    Raises:
        ExternalServiceError: on transport/HTTP failure, or a drifted fingerprint
            file (``extra="forbid"`` rejects unknown fields).

    """
    try:
        files = HfApi(token=token).list_repo_files(repo_id, repo_type="dataset")
    except RepositoryNotFoundError:
        return {}
    except HfHubHTTPError as exc:
        raise ExternalServiceError(f"failed to list {repo_id}: {exc}") from exc
    out: dict[str, SourceFingerprint] = {}
    for path in files:
        match = _SOURCE_RE.fullmatch(path)
        if not match:
            continue
        try:
            local = hf_hub_download(repo_id, path, repo_type="dataset", token=token)
            out[match.group(1)] = SourceFingerprint.model_validate_json(Path(local).read_text())
        except HfHubHTTPError as exc:
            raise ExternalServiceError(f"failed to read {path} from {repo_id}: {exc}") from exc
        except ValidationError as exc:
            raise ExternalServiceError(f"drifted fingerprint {path} in {repo_id}: {exc}") from exc
    return out


def write_local_fingerprint(data_dir: Path, quarter: str, fp: SourceFingerprint) -> None:
    """Stage ``<data_dir>/_source/<quarter>.json`` so the next upload carries it.

    The ``_`` prefix is load-bearing: the ``datasets`` loader (and the HF dataset
    viewer) ignores files/dirs starting with ``_`` or ``.``, so fingerprints sit
    alongside the Parquet tree without being mistaken for a data split.
    """
    dest = data_dir / "_source" / f"{quarter}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_text(fp.model_dump_json(indent=2) + "\n")


def upload(repo_id: str, local_dir: Path, token: str | None = None) -> None:
    """Upload the Parquet tree, source fingerprints, and dataset card to the repo."""
    log.info("upload.start", repo_id=repo_id, local_dir=str(local_dir))
    try:
        HfApi(token=token).upload_large_folder(
            repo_id=repo_id,
            repo_type="dataset",
            folder_path=str(local_dir),
            allow_patterns=["*.parquet", "README.md", "_source/*.json"],
            ignore_patterns=["._*", ".tmp-*"],
        )
    except HfHubHTTPError as exc:
        raise ExternalServiceError(f"failed to upload to {repo_id}: {exc}") from exc
