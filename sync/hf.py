"""Reconcile the local Parquet tree with the HuggingFace dataset repo.

Upload is additive and content-addressed (``upload_large_folder`` hashes each
file and skips those already present), so re-runs are cheap and resumable.
Authentication uses the ``HF_TOKEN`` environment variable.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("sync.hf")

_PARTITION_RE = re.compile(r"year=(\d{4})/quarter=([1-4])/")


def ensure_repo(repo_id: str, token: str | None = None) -> None:
    from huggingface_hub import create_repo

    create_repo(repo_id, repo_type="dataset", exist_ok=True, token=token)


def existing_quarters(repo_id: str, token: str | None = None) -> set[str]:
    """Quarters already present on the remote (from the partition paths)."""
    from huggingface_hub import HfApi
    from huggingface_hub.utils import RepositoryNotFoundError

    try:
        files = HfApi(token=token).list_repo_files(repo_id, repo_type="dataset")
    except RepositoryNotFoundError:
        return set()
    quarters: set[str] = set()
    for path in files:
        m = _PARTITION_RE.search(path)
        if m:
            quarters.add(f"{m.group(1)}q{m.group(2)}")
    return quarters


def upload(repo_id: str, local_dir: str | Path, token: str | None = None) -> None:
    """Upload the Parquet tree (and the dataset card) to the dataset repo."""
    from huggingface_hub import upload_large_folder

    upload_large_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(local_dir),
        allow_patterns=["*.parquet", "README.md"],
        ignore_patterns=["._*", ".tmp-*"],
    )
