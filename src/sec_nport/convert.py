"""Convert a quarterly N-PORT data-set ZIP to typed, partitioned Parquet.

Each quarterly ZIP ships ``nport_metadata.json`` — a W3C CSVW schema describing
all 30 tables (column names, types, precision/scale, date format). We validate
it at the boundary (:class:`NportMetadata`) and drive the conversion off it, so
a new or renamed SEC column is picked up automatically rather than hardcoded.

DuckDB reads each tab-delimited table out of the ZIP and writes one Parquet per
table, hive-partitioned by the source filing quarter::

    data/<TABLE>/year=YYYY/quarter=N/data.parquet

Each input quarter maps 1:1 onto one output partition per table, so re-running a
quarter overwrites just its partition (idempotent) and reading a table back
globs across quarters with partition pruning on year/quarter.
"""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

import duckdb
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sec_nport.errors import ExternalServiceError

if TYPE_CHECKING:
    from pathlib import Path

    from structlog.stdlib import BoundLogger

log: BoundLogger = structlog.get_logger(__name__)  # pyright: ignore[reportAny]

# N-PORT dates are formatted DD-MON-YYYY (e.g. 31-JAN-2024), not ISO. strptime's
# %b matches the abbreviated month name case-insensitively.
_DATE_FORMAT = "%d-%b-%Y"
_METADATA_FILE = "nport_metadata.json"


# --- CSVW boundary models -------------------------------------------------
# DIVERGE (Profile B): the SEC's CSVW metadata is an external schema we don't
# control and carries fields we don't model (@context, dialect, ...), so
# extra="ignore" rather than the default "forbid".


class _Datatype(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    base: str = "string"
    data_precision: int | None = Field(default=None, alias="dataPrecision")
    data_scale: int | None = Field(default=None, alias="dataScale")


class _Column(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    datatype: _Datatype = Field(default_factory=_Datatype)


class _TableSchema(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    columns: list[_Column]


class _Table(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    url: str
    table_schema: _TableSchema = Field(alias="tableSchema")


class NportMetadata(BaseModel):
    """The validated CSVW table schema shipped in each quarterly ZIP."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    tables: list[_Table]


class TableResult(BaseModel):
    """One converted table."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    rows: int


# --- type mapping ---------------------------------------------------------


def _duckdb_type(datatype: _Datatype) -> str:
    """Map a CSVW datatype to a DuckDB column type."""
    base = datatype.base
    if base == "string":
        return "VARCHAR"
    if base.startswith("date"):
        return "DATE"
    if base == "NUMBER":
        scale = datatype.data_scale or 0
        precision = datatype.data_precision or 38
        if scale == 0:
            # Surrogate keys / integer counts; real values fit in 64 bits.
            return "BIGINT"
        return f"DECIMAL({min(precision, 38)},{scale})"
    # Drift-safe fallback: anything unrecognised stays text rather than failing.
    return "VARCHAR"


def _cast_expr(column: _Column) -> str:
    """A SELECT expression casting the all-varchar column to its real type."""
    col = f'"{column.name}"'
    target = _duckdb_type(column.datatype)
    if target == "VARCHAR":
        return col
    non_empty = f"NULLIF({col}, '')"
    if target == "DATE":
        return f"strptime({non_empty}, '{_DATE_FORMAT}')::DATE AS {col}"
    return f"CAST({non_empty} AS {target}) AS {col}"


def _table_name(url: str) -> str:
    return url.removesuffix(".tsv")


def read_metadata(zip_path: Path) -> NportMetadata:
    """Parse and validate the CSVW metadata from a quarterly ZIP.

    Raises:
        ExternalServiceError: if the ZIP lacks the metadata file or it is
            malformed against the expected schema.

    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            raw = zf.read(_METADATA_FILE)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ExternalServiceError(f"cannot read {_METADATA_FILE} from {zip_path}: {exc}") from exc
    try:
        return NportMetadata.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ExternalServiceError(f"malformed N-PORT metadata in {zip_path}: {exc}") from exc


def convert_quarter(
    zip_path: Path,
    year: int,
    quarter: int,
    out_dir: Path,
    *,
    memory_limit: str | None = None,
) -> list[TableResult]:
    """Convert one quarterly ZIP into typed, partitioned Parquet."""
    metadata = read_metadata(zip_path)
    con = duckdb.connect()
    if memory_limit:
        _ = con.execute(f"SET memory_limit='{memory_limit}'")

    results: list[TableResult] = []
    with zipfile.ZipFile(zip_path) as zf:
        present = set(zf.namelist())
        for table in metadata.tables:
            name = _table_name(table.url)
            # Early quarters' metadata declares tables whose TSV isn't shipped
            # (a table simply has no data that quarter). Skip it — no partition,
            # which hive globbing handles fine — rather than crash on read.
            if table.url not in present:
                log.info("convert.skip_absent_table", table=name, year=year, quarter=quarter)
                continue
            select = ",\n        ".join(_cast_expr(c) for c in table.table_schema.columns)
            part = out_dir / name / f"year={year}" / f"quarter={quarter}"
            part.mkdir(parents=True, exist_ok=True)
            final = part / "data.parquet"

            tmp_tsv = part / f".tmp-{name}.tsv"
            tmp_pq = part / f".tmp-{name}.parquet"
            try:
                _ = tmp_tsv.write_bytes(zf.read(table.url))
                _ = con.execute(
                    f"""
                    COPY (
                        SELECT
                        {select}
                        FROM read_csv(
                            '{tmp_tsv}', delim='\t', header=true, all_varchar=true
                        )
                    ) TO '{tmp_pq}' (FORMAT PARQUET, COMPRESSION zstd)
                    """  # noqa: S608  # column names come from the validated CSVW schema, not user input
                )
                _ = tmp_pq.replace(final)
                count = con.execute(
                    f"SELECT count(*) FROM read_parquet('{final}')"  # noqa: S608
                ).fetchone()
                rows = int(count[0]) if count else 0
                results.append(TableResult(table=name, rows=rows))
                log.info("convert.table", table=name, year=year, quarter=quarter, rows=rows)
            finally:
                tmp_tsv.unlink(missing_ok=True)
                tmp_pq.unlink(missing_ok=True)
    con.close()
    return results
