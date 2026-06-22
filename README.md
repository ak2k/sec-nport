# sec-nport

Sync the SEC's **Form N-PORT** structured data sets to a HuggingFace dataset as
typed, partitioned Parquet — queryable directly from DuckDB.

Form N-PORT is the monthly portfolio-holdings report filed by registered
investment companies and ETFs. The [SEC publishes it quarterly](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)
as ZIP archives of tab-delimited tables (30 of them, ~447 MB/quarter). This
tool converts each quarter to one Parquet dataset per table, hive-partitioned
by the source filing quarter, and mirrors it to HuggingFace.

## How it works

```
SEC quarterly ZIP  ──►  DuckDB (typed from the ZIP's CSVW schema)  ──►  Parquet  ──►  HuggingFace
   YYYYqN_nport.zip                                                   data/<TABLE>/year=YYYY/quarter=N/data.parquet
```

- **Types are derived, not hardcoded.** Each ZIP ships `nport_metadata.json`
  (a W3C CSVW schema), validated at the boundary (`convert.NportMetadata`) and
  used to type every column — money as `DECIMAL(36,12)`, surrogate keys as
  `BIGINT`, dates (`DD-MON-YYYY`) as `DATE`. New/renamed SEC columns are picked
  up automatically.
- **1:1 quarter ↔ partition.** Each input ZIP maps onto one partition per
  table, so re-running a quarter overwrites just its partition (idempotent) and
  reading a table globs across quarters with partition pruning.
- **Incremental.** A scheduled run builds only quarters not yet on HuggingFace;
  `upload_large_folder` is content-addressed, so re-runs are cheap.

## Stack

Built on the [python-starter](https://github.com/ak2k/python-starter) template:
`uv`, `ruff` + `basedpyright` strict, Pydantic v2 at boundaries, `httpx`,
`structlog`, `typer`, domain errors (`sec_nport.errors.AppError`). See
`AGENTS.md` for the full contract.

## Usage

```bash
uv sync
export SEC_USER_AGENT="sec-nport you@example.com"   # SEC fair-access policy
export HF_TOKEN=...                                  # for upload
export HF_REPO_ID="<user>/sec-nport"

uv run sec-nport sync --all                # backfill every quarter
uv run sec-nport sync --quarter 2024q1     # one quarter
uv run sec-nport sync                      # only quarters not yet on HF
uv run sec-nport sync --quarter 2024q1 --no-upload   # convert locally, skip upload
uv run sec-nport list [--all]              # print quarters as JSON (CI matrix)
```

## Develop

```bash
make check    # ruff + basedpyright strict + pytest (CI runs this)
make fix      # autofix + format, then check
nix develop   # optional: uv + make in a pinned shell
```

## CI

`.github/workflows/sync.yml` runs quarterly (and on dispatch): a `detect` job
lists pending quarters, a `sync` matrix converts + uploads one quarter per job,
and a `card` job regenerates the dataset card. Configure repo `vars`
(`HF_REPO_ID`, `SEC_USER_AGENT`) and a `HF_TOKEN` secret.

## Layout

| Path | |
|------|--|
| `src/sec_nport/sec.py` | discover + download SEC quarterly ZIPs (httpx, UA-gated) |
| `src/sec_nport/convert.py` | CSVW-schema-driven typed Parquet conversion (DuckDB) |
| `src/sec_nport/hf.py` | HuggingFace upload + incremental detection |
| `src/sec_nport/card.py` | generate the dataset card (one viewer config per table) |
| `src/sec_nport/cli.py` | `sec-nport` CLI (typer) |
| `src/sec_nport/config.py` | typed settings (pydantic-settings) |

The data is U.S. Government public domain; this is an unofficial mirror.
