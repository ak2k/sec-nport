"""Generate the HuggingFace dataset card (README.md).

The YAML frontmatter declares one ``config`` per table so the dataset viewer
shows each one. Configs come from the table directories present in the data
tree; when none exist (e.g. a CI card-only job) it falls back to the constant
list of N-PORT tables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from structlog.stdlib import BoundLogger

log: BoundLogger = structlog.get_logger(__name__)  # pyright: ignore[reportAny]

# The 30 N-PORT tables (stable since the data sets began in 2019). A present
# data tree overrides this, so a new SEC table is picked up automatically there.
_TABLES: tuple[str, ...] = (
    "SUBMISSION",
    "REGISTRANT",
    "FUND_REPORTED_INFO",
    "INTEREST_RATE_RISK",
    "BORROWER",
    "BORROW_AGGREGATE",
    "MONTHLY_TOTAL_RETURN",
    "MONTHLY_RETURN_CAT_INSTRUMENT",
    "FUND_VAR_INFO",
    "FUND_REPORTED_HOLDING",
    "IDENTIFIERS",
    "DEBT_SECURITY",
    "DEBT_SECURITY_REF_INSTRUMENT",
    "CONVERTIBLE_SECURITY_CURRENCY",
    "REPURCHASE_AGREEMENT",
    "REPURCHASE_COUNTERPARTY",
    "REPURCHASE_COLLATERAL",
    "DERIVATIVE_COUNTERPARTY",
    "SWAPTION_OPTION_WARNT_DERIV",
    "DESC_REF_INDEX_BASKET",
    "DESC_REF_INDEX_COMPONENT",
    "DESC_REF_OTHER",
    "FUT_FWD_NONFOREIGNCUR_CONTRACT",
    "FWD_FOREIGNCUR_CONTRACT_SWAP",
    "NONFOREIGN_EXCHANGE_SWAP",
    "FLOATING_RATE_RESET_TENOR",
    "OTHER_DERIV",
    "OTHER_DERIV_NOTIONAL_AMOUNT",
    "SECURITIES_LENDING",
    "EXPLANATORY_NOTE",
)

_BODY = """\
# SEC Form N-PORT Data Sets

Monthly portfolio holdings reported by registered investment companies and ETFs
on **Form N-PORT**, published by the U.S. SEC as quarterly structured data sets
and mirrored here as typed, partitioned Parquet — queryable directly from DuckDB.

- **Source**: SEC Form N-PORT Data Sets — public domain (U.S. Government work)
  <https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets>
- **Coverage**: {coverage}
- **Format**: one Parquet dataset per table, hive-partitioned by source filing quarter
- **Built by**: <https://github.com/ak2k/sec-nport> — a quarterly GitHub Action.
  Column types are derived from the CSVW schema (`nport_metadata.json`) the SEC
  ships in each ZIP: money as `DECIMAL(36,12)`, surrogate keys as `BIGINT`,
  dates as `DATE`.

## Dataset structure

Each of the 30 N-PORT tables is a separate subset (config), laid out as:

```
<TABLE>/year=YYYY/quarter=N/data.parquet
```

Partitioning by the source filing quarter means a query that filters on
`year`/`quarter` prunes to just those files.

`SUBMISSION`, `REGISTRANT`, `FUND_REPORTED_INFO` and the monthly-return/risk
tables key on **`ACCESSION_NUMBER`** (one filing). `FUND_REPORTED_HOLDING`
(every holding of every fund) keys on **`HOLDING_ID`**, and the per-holding
detail tables (`IDENTIFIERS`, `DEBT_SECURITY`, derivatives, …) join back on it.

Field-level definitions for every column are in the SEC's data dictionary:
<https://www.sec.gov/files/nport_readme.pdf>.

## Usage

Query in place with DuckDB — no download, partition pruning over the whole history:

```sql
-- one filing's largest reported holdings
SELECT ISSUER_NAME, ISSUER_CUSIP, CURRENCY_VALUE
FROM 'hf://datasets/{repo_id}/FUND_REPORTED_HOLDING/year=2024/quarter=1/*.parquet'
ORDER BY CURRENCY_VALUE DESC
LIMIT 20;

-- across all quarters (prunes by the year/quarter partitions)
SELECT year, count(*) AS holdings
FROM 'hf://datasets/{repo_id}/FUND_REPORTED_HOLDING/**/*.parquet'
GROUP BY year ORDER BY year;
```

Or load a table with the `datasets` library (use `streaming=True` for the large
holdings tables):

```python
from datasets import load_dataset

ds = load_dataset("{repo_id}", "FUND_REPORTED_HOLDING", split="train", streaming=True)
```

## Provenance & caveats

Data is provided **as-filed** by registrants (including amendments) and may
contain redundancies or inconsistencies relative to other publications. This is
an unofficial mirror; the
[SEC](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)
is the authoritative source. Not investment advice.

## Citation

Source data — U.S. Securities and Exchange Commission, *Form N-PORT Structured
Data Sets* (public domain). This Parquet mirror —
<https://github.com/ak2k/sec-nport>.
"""


def _tables(data_dir: Path) -> list[str]:
    if data_dir.is_dir():
        found = sorted(
            p.name for p in data_dir.iterdir() if p.is_dir() and any(p.rglob("*.parquet"))
        )
        if found:
            return found
    return list(_TABLES)


def _coverage(data_dir: Path) -> str:
    """Quarter range + count from the local SUBMISSION partitions.

    Falls back to a qualitative line when no data is present (e.g. a CI
    card-only job), so the card never hardcodes stats that would drift.
    """
    subs = data_dir / "SUBMISSION"
    quarters = sorted(
        (p.parent.parent.name.removeprefix("year="), p.parent.name.removeprefix("quarter="))
        for p in subs.glob("year=*/quarter=*/data.parquet")
    )
    if not quarters:
        return "October 2019 onward, refreshed quarterly"
    (lo_y, lo_q), (hi_y, hi_q) = quarters[0], quarters[-1]
    return f"{lo_y} Q{lo_q} to {hi_y} Q{hi_q} ({len(quarters)} quarters), refreshed quarterly"


def _frontmatter(tables: list[str]) -> str:
    lines = [
        "---",
        "license: cc0-1.0",
        "pretty_name: SEC Form N-PORT Data Sets",
        "tags:",
        "- finance",
        "- sec",
        "- edgar",
        "- mutual-funds",
        "- portfolio-holdings",
        "configs:",
    ]
    for table in tables:
        lines.append(f"- config_name: {table}")
        lines.append("  data_files:")
        lines.append(f'  - split: train\n    path: "{table}/**/*.parquet"')
    lines.append("---")
    return "\n".join(lines)


def write_card(repo_id: str, data_dir: Path) -> Path:
    """Write ``<data_dir>/README.md`` (the dataset card) and return its path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    tables = _tables(data_dir)
    body = _BODY.replace("{coverage}", _coverage(data_dir)).replace("{repo_id}", repo_id)
    content = _frontmatter(tables) + "\n\n" + body
    out = data_dir / "README.md"
    _ = out.write_text(content, encoding="utf-8")
    log.info("card.written", tables=len(tables))
    return out
