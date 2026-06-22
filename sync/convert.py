"""Convert a quarterly N-PORT data-set ZIP to typed, partitioned Parquet.

Each quarterly ZIP from the SEC ships ``nport_metadata.json`` — a W3C CSVW
schema describing all 30 tables (column names, types, precision/scale, and the
date format). We drive the conversion off that file rather than a hardcoded
schema, so when the SEC adds or renames a column the output follows
automatically.

DuckDB reads each tab-delimited table out of the ZIP and writes one Parquet per
table, hive-partitioned by the source filing quarter:

    data/<TABLE>/year=YYYY/quarter=N/data.parquet

so each input quarter maps 1:1 onto one output partition per table — re-running
a quarter overwrites just its partition (idempotent), and reading a table back
globs across quarters with partition pruning on year/quarter.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path

import duckdb

log = logging.getLogger("sync.convert")

# N-PORT dates are formatted DD-MON-YYYY (e.g. 31-JAN-2024), not ISO. strptime's
# %b matches the abbreviated month name case-insensitively.
_DATE_FORMAT = "%d-%b-%Y"

_METADATA_FILE = "nport_metadata.json"


def _duckdb_type(datatype: dict) -> str:
    """Map a CSVW datatype to a DuckDB column type."""
    base = (datatype or {}).get("base", "string")
    if base == "string":
        return "VARCHAR"
    if base.startswith("date"):
        return "DATE"
    if base == "NUMBER":
        scale = int(datatype.get("dataScale") or 0)
        precision = int(datatype.get("dataPrecision") or 38)
        if scale == 0:
            # Surrogate keys / integer counts (NUMBER(p,0)). Real values fit
            # in 64 bits; HUGEINT would be faithful to p=38 but is wasteful.
            return "BIGINT"
        return f"DECIMAL({min(precision, 38)},{scale})"
    # Drift-safe fallback: anything unrecognised stays text rather than failing.
    return "VARCHAR"


def _cast_expr(name: str, datatype: dict) -> str:
    """A SELECT expression that casts the all-varchar column to its real type.

    The TSV is read all-varchar (robust, no per-quarter inference drift); empty
    strings become NULL before casting.
    """
    col = f'"{name}"'
    target = _duckdb_type(datatype)
    if target == "VARCHAR":
        return col
    non_empty = f"NULLIF({col}, '')"
    if target == "DATE":
        return f"strptime({non_empty}, '{_DATE_FORMAT}')::DATE AS {col}"
    return f"CAST({non_empty} AS {target}) AS {col}"


def _table_name(url: str) -> str:
    return url[:-4] if url.endswith(".tsv") else url


def read_metadata(zip_path: str | Path) -> dict:
    """Return the parsed CSVW metadata from a quarterly ZIP."""
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read(_METADATA_FILE))


def convert_quarter(
    zip_path: str | Path,
    year: int,
    quarter: int,
    out_dir: str | Path,
    *,
    memory_limit: str | None = None,
) -> list[dict]:
    """Convert one quarterly ZIP into typed, partitioned Parquet.

    Returns a list of ``{"table", "rows"}`` dicts, one per table written.
    """
    out_dir = Path(out_dir)
    con = duckdb.connect()
    if memory_limit:
        con.execute(f"SET memory_limit='{memory_limit}'")

    results: list[dict] = []
    with zipfile.ZipFile(zip_path) as zf:
        metadata = json.loads(zf.read(_METADATA_FILE))
        for table in metadata["tables"]:
            tsv = table["url"]
            name = _table_name(tsv)
            columns = table["tableSchema"]["columns"]
            select = ",\n        ".join(
                _cast_expr(c["name"], c.get("datatype", {})) for c in columns
            )

            part = out_dir / name / f"year={year}" / f"quarter={quarter}"
            part.mkdir(parents=True, exist_ok=True)
            final = part / "data.parquet"
            # Atomic: extract the TSV to a temp file, COPY to a temp Parquet in
            # the same dir, then os.replace into place.
            fd, tmp_tsv = tempfile.mkstemp(suffix=".tsv")
            os.close(fd)
            tmp_pq = part / f".tmp-{name}.parquet"
            try:
                with open(tmp_tsv, "wb") as f:
                    f.write(zf.read(tsv))
                con.execute(
                    f"""
                    COPY (
                        SELECT
                        {select}
                        FROM read_csv(
                            '{tmp_tsv}',
                            delim='\t', header=true, all_varchar=true
                        )
                    ) TO '{tmp_pq}' (FORMAT PARQUET, COMPRESSION zstd)
                    """
                )
                os.replace(tmp_pq, final)
                rows = con.execute(
                    f"SELECT count(*) FROM read_parquet('{final}')"
                ).fetchone()[0]
                results.append({"table": name, "rows": rows})
                log.info("%s %dq%d: %d rows", name, year, quarter, rows)
            finally:
                for p in (tmp_tsv, tmp_pq):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
    con.close()
    return results
