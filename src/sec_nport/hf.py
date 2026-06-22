"""Reconcile the local Parquet tree with the HuggingFace dataset repo.

Upload is additive and content-addressed (``upload_large_folder`` hashes each
file and skips those already present), so re-runs are cheap and resumable.
``huggingface_hub`` errors are wrapped as domain errors at this boundary.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog
from huggingface_hub import HfApi, create_repo
from huggingface_hub.errors import HfHubHTTPError, RepositoryNotFoundError

from sec_nport.errors import ExternalServiceError

if TYPE_CHECKING:
    from pathlib import Path

    from structlog.stdlib import BoundLogger

log: BoundLogger = structlog.get_logger(__name__)  # pyright: ignore[reportAny]

_PARTITION_RE = re.compile(r"year=(\d{4})/quarter=([1-4])/")


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


def upload(repo_id: str, local_dir: Path, token: str | None = None) -> None:
    """Upload the Parquet tree (and the dataset card) to the dataset repo."""
    log.info("upload.start", repo_id=repo_id, local_dir=str(local_dir))
    try:
        HfApi(token=token).upload_large_folder(
            repo_id=repo_id,
            repo_type="dataset",
            folder_path=str(local_dir),
            allow_patterns=["*.parquet", "README.md"],
            ignore_patterns=["._*", ".tmp-*"],
        )
    except HfHubHTTPError as exc:
        raise ExternalServiceError(f"failed to upload to {repo_id}: {exc}") from exc
