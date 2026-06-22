"""Sync SEC Form N-PORT data sets to a HuggingFace dataset.

One idempotent command: discover quarters, download each ZIP, convert to typed
partitioned Parquet, and (unless ``--no-upload``) reconcile the HuggingFace
dataset. Re-running converges — already-built quarters are skipped.

    python -m sync --all                  # backfill every quarter
    python -m sync --quarter 2024q1       # one quarter
    python -m sync                        # only quarters not yet on HF
    python -m sync --list-pending         # print quarters needing a build (JSON)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from sync import card, convert, hf, sec

log = logging.getLogger("sync")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sync", description=__doc__)
    p.add_argument("--quarter", help="single quarter, e.g. 2024q1")
    p.add_argument("--all", action="store_true", help="process every quarter")
    p.add_argument(
        "--list-pending",
        action="store_true",
        help="print quarters not yet on HF as a JSON list, then exit",
    )
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--work-dir", type=Path, default=Path(".work"))
    p.add_argument(
        "--repo-id", default=os.environ.get("HF_REPO_ID", "ak2k/sec-nport")
    )
    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--memory-limit", default=os.environ.get("NPORT_MEMORY_LIMIT"))
    return p


def _select_quarters(args, available: list[str]) -> list[str]:
    if args.quarter:
        return [args.quarter]
    if args.all:
        return available
    # Default / scheduled: only what the remote is missing.
    done = hf.existing_quarters(args.repo_id)
    return [q for q in available if q not in done]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    available = sec.list_quarters()

    if args.list_pending:
        done = hf.existing_quarters(args.repo_id)
        print(json.dumps([q for q in available if q not in done]))
        return 0

    quarters = _select_quarters(args, available)
    if not quarters:
        log.info("nothing to do — all quarters present")
        return 0
    log.info("processing %d quarter(s): %s", len(quarters), ", ".join(quarters))

    args.work_dir.mkdir(parents=True, exist_ok=True)
    for q in quarters:
        year, quarter = sec.quarter_parts(q)
        zip_path = args.work_dir / f"{q}_nport.zip"
        sec.download_quarter(q, zip_path)
        convert.convert_quarter(
            zip_path, year, quarter, args.data_dir, memory_limit=args.memory_limit
        )
        zip_path.unlink(missing_ok=True)
        time.sleep(1)  # SEC fair-access courtesy

    if not args.no_upload:
        hf.ensure_repo(args.repo_id)
        card.write_card(args.repo_id, args.data_dir)
        hf.upload(args.repo_id, args.data_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
