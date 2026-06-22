"""Generate the HuggingFace dataset card (README.md) from the data on disk.

The YAML frontmatter declares one ``config`` per table so the dataset viewer
shows each one; configs are derived from the table directories present, so the
card always matches the actual data.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("sync.card")

_BODY = """\
# SEC Form N-PORT Data Sets

Monthly portfolio holdings reported by registered investment companies and ETFs
on **Form N-PORT**, published by the U.S. SEC as quarterly structured data sets
and mirrored here as typed, partitioned Parquet.

- **Source**: [SEC Form N-PORT Data Sets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets) (public domain, U.S. Government work)
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
`FUND_REPORTED_HOLDING` (every holding of every fund) keys on
**`HOLDING_ID`**, and the per-holding detail tables (`IDENTIFIERS`,
`DEBT_SECURITY`, derivatives, …) join back on it.

## Query directly with DuckDB

No download required — read the partitioned Parquet over HTTP:

```sql
-- Largest holdings reported in 2024 Q1
SELECT ISSUER_NAME, ISSUER_CUSIP, CURRENCY_VALUE
FROM 'hf://datasets/{repo_id}/FUND_REPORTED_HOLDING/year=2024/quarter=1/*.parquet'
ORDER BY CURRENCY_VALUE DESC
LIMIT 20;

-- Partition pruning across the whole history
SELECT year, count(*) AS holdings
FROM 'hf://datasets/{repo_id}/FUND_REPORTED_HOLDING/**/*.parquet'
GROUP BY year ORDER BY year;
```

## Provenance & caveats

Data is provided **as-filed** by registrants (including amendments) and may
contain redundancies or inconsistencies relative to other publications. This is
an unofficial mirror; the [SEC](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)
is the authoritative source. Not investment advice.
"""


# The 30 N-PORT tables (stable since the data sets began in 2019). Used as the
# config list when no local data tree is available (e.g. a CI card-only job);
# a present data dir overrides this, so a new SEC table is picked up there.
_TABLES = [
    "SUBMISSION", "REGISTRANT", "FUND_REPORTED_INFO", "INTEREST_RATE_RISK",
    "BORROWER", "BORROW_AGGREGATE", "MONTHLY_TOTAL_RETURN",
    "MONTHLY_RETURN_CAT_INSTRUMENT", "FUND_VAR_INFO", "FUND_REPORTED_HOLDING",
    "IDENTIFIERS", "DEBT_SECURITY", "DEBT_SECURITY_REF_INSTRUMENT",
    "CONVERTIBLE_SECURITY_CURRENCY", "REPURCHASE_AGREEMENT",
    "REPURCHASE_COUNTERPARTY", "REPURCHASE_COLLATERAL",
    "DERIVATIVE_COUNTERPARTY", "SWAPTION_OPTION_WARNT_DERIV",
    "DESC_REF_INDEX_BASKET", "DESC_REF_INDEX_COMPONENT", "DESC_REF_OTHER",
    "FUT_FWD_NONFOREIGNCUR_CONTRACT", "FWD_FOREIGNCUR_CONTRACT_SWAP",
    "NONFOREIGN_EXCHANGE_SWAP", "FLOATING_RATE_RESET_TENOR", "OTHER_DERIV",
    "OTHER_DERIV_NOTIONAL_AMOUNT", "SECURITIES_LENDING", "EXPLANATORY_NOTE",
]


def _tables(data_dir: Path) -> list[str]:
    if data_dir.is_dir():
        found = sorted(
            p.name
            for p in data_dir.iterdir()
            if p.is_dir() and any(p.rglob("*.parquet"))
        )
        if found:
            return found
    return _TABLES


def _frontmatter(repo_id: str, tables: list[str]) -> str:
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
    for t in tables:
        lines.append(f"- config_name: {t}")
        lines.append("  data_files:")
        lines.append(f'  - split: train\n    path: "{t}/**/*.parquet"')
    lines.append("---")
    return "\n".join(lines)


def write_card(repo_id: str, data_dir: str | Path) -> Path:
    """Write ``<data_dir>/README.md`` (the dataset card) and return its path."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    tables = _tables(data_dir)
    content = _frontmatter(repo_id, tables) + "\n\n" + _BODY.replace("{repo_id}", repo_id)
    out = data_dir / "README.md"
    out.write_text(content, encoding="utf-8")
    log.info("wrote dataset card with %d table configs", len(tables))
    return out
