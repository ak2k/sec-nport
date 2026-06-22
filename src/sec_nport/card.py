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
and mirrored here as typed, partitioned Parquet.

- **Source**: SEC Form N-PORT Data Sets — public domain (U.S. Government work)
  <https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets>
- **Coverage**: October 2019 onward, refreshed quarterly
- **Format**: one Parquet dataset per table, hive-partitioned by source filing quarter

```
<TABLE>/year=YYYY/quarter=N/data.parquet
```

Types come from the CSVW schema (`nport_metadata.json`) the SEC ships in each
ZIP: money as `DECIMAL(36,12)`, surrogate keys as `BIGINT`, dates as `DATE`.

## Tables

30 related tables. `SUBMISSION`, `REGISTRANT`, `FUND_REPORTED_INFO` and the
monthly-return/risk tables key on **`ACCESSION_NUMBER`** (one filing).
`FUND_REPORTED_HOLDING` (every holding of every fund) keys on **`HOLDING_ID`**,
and the per-holding detail tables (`IDENTIFIERS`, `DEBT_SECURITY`, derivatives,
…) join back on it.

## Query directly with DuckDB

```sql
SELECT ISSUER_NAME, ISSUER_CUSIP, CURRENCY_VALUE
FROM 'hf://datasets/{repo_id}/FUND_REPORTED_HOLDING/year=2024/quarter=1/*.parquet'
ORDER BY CURRENCY_VALUE DESC
LIMIT 20;
```

## Provenance & caveats

Data is provided **as-filed** by registrants (including amendments) and may
contain redundancies or inconsistencies. This is an unofficial mirror; the
[SEC](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)
is the authoritative source. Not investment advice.
"""


def _tables(data_dir: Path) -> list[str]:
    if data_dir.is_dir():
        found = sorted(
            p.name for p in data_dir.iterdir() if p.is_dir() and any(p.rglob("*.parquet"))
        )
        if found:
            return found
    return list(_TABLES)


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
    content = _frontmatter(tables) + "\n\n" + _BODY.replace("{repo_id}", repo_id)
    out = data_dir / "README.md"
    _ = out.write_text(content, encoding="utf-8")
    log.info("card.written", tables=len(tables))
    return out
