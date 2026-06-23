"""Command-line entry point (typer).

sec-nport sync                 # build + upload quarters not yet on HF
sec-nport sync --all           # backfill every quarter
sec-nport sync --quarter 2024q1
sec-nport sync --no-upload     # convert locally only
sec-nport list [--all]         # print pending (or all) quarters as JSON
sec-nport seed                 # backfill source fingerprints for quarters on HF
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Annotated

import structlog
import typer

from sec_nport import card, convert, hf, sec
from sec_nport.config import Settings, get_settings
from sec_nport.errors import ExternalServiceError
from sec_nport.log import configure

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from structlog.stdlib import BoundLogger

log: BoundLogger = structlog.get_logger("sec_nport")  # pyright: ignore[reportAny]

app = typer.Typer(add_completion=False, help="Sync SEC Form N-PORT to a HuggingFace dataset.")


def _pending(settings: Settings, client: httpx.Client, available: list[str]) -> list[str]:
    """Quarters needing a (re)build: source fingerprint new/changed, or no partition yet.

    The presence check (``q not in present``) keeps detection self-healing: a quarter
    whose fingerprint landed but whose partitions did not (a partial upload) is rebuilt
    rather than skipped on the matching fingerprint.
    """
    present = hf.existing_quarters(settings.hf_repo_id, settings.hf_token)
    known = hf.read_fingerprints(settings.hf_repo_id, settings.hf_token)
    out: list[str] = []
    for q in available:
        try:
            current = sec.head_quarter(client, q)
        except ExternalServiceError:
            # The SEC lists a quarter on the index page slightly before (or while)
            # its ZIP is fetchable, and can serve a transient WAF 403. One quarter
            # we can't HEAD today must not abort detection of all the others — skip
            # it and let the next daily run pick it up.
            log.warning("detect.head_unavailable", quarter=q)
            continue
        if q not in present or known.get(q) != current:
            out.append(q)
    return out


def _locally_done(data_dir: Path, quarter: str) -> bool:
    """True if this quarter's output already exists locally (resume guard).

    SUBMISSION is present in every quarter, so its partition is the marker.
    """
    year, qtr = sec.quarter_parts(quarter)
    return (data_dir / "SUBMISSION" / f"year={year}" / f"quarter={qtr}" / "data.parquet").exists()


@app.command()
def sync(
    quarter: Annotated[str | None, typer.Option(help="Single quarter, e.g. 2024q1.")] = None,
    all_quarters: Annotated[bool, typer.Option("--all", help="Process every quarter.")] = False,
    no_upload: Annotated[bool, typer.Option("--no-upload", help="Convert locally only.")] = False,
) -> None:
    """Download, convert, and (unless --no-upload) upload N-PORT quarters."""
    settings = get_settings()
    configure(settings.log_level, settings.log_json)
    client = sec.make_client(settings.sec_user_agent)

    if quarter:
        quarters = [quarter]
    else:
        available = sec.list_quarters(client)
        quarters = available if all_quarters else _pending(settings, client, available)

    if not quarters:
        log.info("sync.nothing_to_do")
        return
    log.info("sync.start", count=len(quarters), quarters=quarters)

    settings.work_dir.mkdir(parents=True, exist_ok=True)
    for q in quarters:
        if _locally_done(settings.data_dir, q):
            log.info("sync.skip_done", quarter=q)
            # Record the fingerprint even on the resume path: a quarter with
            # partitions but no fingerprint would otherwise be re-detected as
            # pending every run yet skipped here, never converging.
            hf.write_local_fingerprint(settings.data_dir, q, sec.head_quarter(client, q))
            continue
        year, qtr = sec.quarter_parts(q)
        zip_path = settings.work_dir / f"{q}_nport.zip"
        _ = sec.download_quarter(client, q, zip_path)
        _ = convert.convert_quarter(
            zip_path, year, qtr, settings.data_dir, memory_limit=settings.memory_limit
        )
        # Record the fingerprint of the source we just processed so a future
        # re-post (changed Last-Modified/Content-Length) is detected as pending.
        hf.write_local_fingerprint(settings.data_dir, q, sec.head_quarter(client, q))
        zip_path.unlink(missing_ok=True)
        time.sleep(3)  # SEC fair-access courtesy (429-prone on large files)

    if not no_upload:
        hf.ensure_repo(settings.hf_repo_id, settings.hf_token)
        _ = card.write_card(settings.hf_repo_id, settings.data_dir)
        hf.upload(settings.hf_repo_id, settings.data_dir, settings.hf_token)


@app.command("list")
def list_quarters(
    all_quarters: Annotated[
        bool, typer.Option("--all", help="List all, not just pending.")
    ] = False,
) -> None:
    """Print quarters as a JSON list (for the CI matrix)."""
    settings = get_settings()
    configure(settings.log_level, settings.log_json)  # detect warnings → stderr, JSON → stdout
    client = sec.make_client(settings.sec_user_agent)
    available = sec.list_quarters(client)
    quarters = available if all_quarters else _pending(settings, client, available)
    typer.echo(json.dumps(quarters))


@app.command()
def seed() -> None:
    """Backfill _source/<q>.json fingerprints for quarters already on HF.

    One-time baseline so fingerprint-based detect doesn't treat every existing
    quarter as new. HEAD-only — uploads just the new fingerprint files; existing
    partitions are left untouched.

    The baseline is the *current* SEC source fingerprint, which assumes each
    quarter's published partitions were built from the source as it stands now. If
    a quarter was re-posted after its partitions were built, reprocess it
    (``sync --quarter <q>``) instead of seeding, so the data matches the baseline.
    """
    settings = get_settings()
    configure(settings.log_level, settings.log_json)
    client = sec.make_client(settings.sec_user_agent)
    present = hf.existing_quarters(settings.hf_repo_id, settings.hf_token)
    known = hf.read_fingerprints(settings.hf_repo_id, settings.hf_token)
    missing = sorted(present - set(known))
    if not missing:
        log.info("seed.nothing_to_do", present=len(present))
        return
    log.info("seed.start", count=len(missing), quarters=missing)
    for q in missing:
        hf.write_local_fingerprint(settings.data_dir, q, sec.head_quarter(client, q))
    hf.ensure_repo(settings.hf_repo_id, settings.hf_token)
    hf.upload(settings.hf_repo_id, settings.data_dir, settings.hf_token)
    log.info("seed.done", count=len(missing))


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
