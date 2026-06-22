"""Command-line entry point (typer).

sec-nport sync                 # build + upload quarters not yet on HF
sec-nport sync --all           # backfill every quarter
sec-nport sync --quarter 2024q1
sec-nport sync --no-upload     # convert locally only
sec-nport list [--all]         # print pending (or all) quarters as JSON
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Annotated

import structlog
import typer

from sec_nport import card, convert, hf, sec
from sec_nport.config import Settings, get_settings
from sec_nport.log import configure

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

log: BoundLogger = structlog.get_logger("sec_nport")  # pyright: ignore[reportAny]

app = typer.Typer(add_completion=False, help="Sync SEC Form N-PORT to a HuggingFace dataset.")


def _pending(settings: Settings, available: list[str]) -> list[str]:
    done = hf.existing_quarters(settings.hf_repo_id, settings.hf_token)
    return [q for q in available if q not in done]


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
        quarters = available if all_quarters else _pending(settings, available)

    if not quarters:
        log.info("sync.nothing_to_do")
        return
    log.info("sync.start", count=len(quarters), quarters=quarters)

    settings.work_dir.mkdir(parents=True, exist_ok=True)
    for q in quarters:
        year, qtr = sec.quarter_parts(q)
        zip_path = settings.work_dir / f"{q}_nport.zip"
        _ = sec.download_quarter(client, q, zip_path)
        _ = convert.convert_quarter(
            zip_path, year, qtr, settings.data_dir, memory_limit=settings.memory_limit
        )
        zip_path.unlink(missing_ok=True)
        time.sleep(1)  # SEC fair-access courtesy

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
    client = sec.make_client(settings.sec_user_agent)
    available = sec.list_quarters(client)
    quarters = available if all_quarters else _pending(settings, available)
    typer.echo(json.dumps(quarters))


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
