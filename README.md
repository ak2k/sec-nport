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
  (a W3C CSVW schema); `convert.py` reads it to type every column — money as
  `DECIMAL(36,12)`, surrogate keys as `BIGINT`, dates (`DD-MON-YYYY`) as `DATE`.
  New/renamed SEC columns are picked up automatically.
- **1:1 quarter ↔ partition.** Each input ZIP maps onto one partition per
  table, so re-running a quarter overwrites just its partition (idempotent) and
  reading a table globs across quarters with partition pruning.
- **Incremental.** A scheduled run builds only quarters not yet on HuggingFace;
  `upload_large_folder` is content-addressed, so re-runs are cheap.

## Usage

```bash
pip install -r sync/requirements.txt
export SEC_USER_AGENT="sec-nport you@example.com"   # SEC fair-access policy
export HF_TOKEN=...                                  # for upload
export HF_REPO_ID="<user>/sec-nport"

python -m sync --all                 # backfill every quarter
python -m sync --quarter 2024q1      # one quarter
python -m sync                       # only quarters not yet on HF
python -m sync --quarter 2024q1 --no-upload   # convert locally, skip upload
```

## CI

`.github/workflows/sync.yml` runs quarterly (and on dispatch): a `detect` job
lists pending quarters, a `sync` matrix converts + uploads one quarter per job,
and a `card` job regenerates the dataset card. Configure repo `vars`
(`HF_REPO_ID`, `SEC_USER_AGENT`) and a `HF_TOKEN` secret.

## Layout

| Path | |
|------|--|
| `sync/sec.py` | discover + download SEC quarterly ZIPs (UA-gated) |
| `sync/convert.py` | CSVW-schema-driven typed Parquet conversion |
| `sync/hf.py` | HuggingFace upload + incremental detection |
| `sync/card.py` | generate the dataset card (one viewer config per table) |
| `sync/__main__.py` | orchestrator (`python -m sync`) |

The data is U.S. Government public domain; this is an unofficial mirror.
