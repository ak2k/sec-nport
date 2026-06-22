"""Tests for the CSVW-driven typed conversion."""

from __future__ import annotations

import datetime
import json
import zipfile
from typing import TYPE_CHECKING

import duckdb
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sec_nport import convert

# Unit-testing the internal type mapping reaches into these private helpers.
from sec_nport.convert import _Datatype, _duckdb_type  # pyright: ignore[reportPrivateUsage]
from sec_nport.errors import ExternalServiceError

if TYPE_CHECKING:
    from pathlib import Path


def test_type_mapping():
    assert _duckdb_type(_Datatype(base="string")) == "VARCHAR"
    assert _duckdb_type(_Datatype(base="date (DD-MON-YYYY)")) == "DATE"
    assert _duckdb_type(_Datatype(base="NUMBER", dataPrecision=38, dataScale=0)) == "BIGINT"
    assert (
        _duckdb_type(_Datatype(base="NUMBER", dataPrecision=36, dataScale=12)) == "DECIMAL(36,12)"
    )
    assert _duckdb_type(_Datatype(base="wat")) == "VARCHAR"  # drift-safe fallback


@given(
    precision=st.integers(min_value=1, max_value=38),
    scale=st.integers(min_value=1, max_value=12),
)
def test_number_with_scale_maps_to_decimal(precision: int, scale: int):
    mapped = _duckdb_type(_Datatype(base="NUMBER", dataPrecision=precision, dataScale=scale))
    assert mapped == f"DECIMAL({min(precision, 38)},{scale})"


def _make_zip(path: Path, *, metadata: object | None = None) -> None:
    meta = (
        metadata
        if metadata is not None
        else {
            "tables": [
                {
                    "url": "T.tsv",
                    "tableSchema": {
                        "columns": [
                            {
                                "name": "ACCESSION_NUMBER",
                                "datatype": {"base": "string", "maxLength": 20},
                            },
                            {
                                "name": "HOLDING_ID",
                                "datatype": {"base": "NUMBER", "dataPrecision": 38, "dataScale": 0},
                            },
                            {
                                "name": "BALANCE",
                                "datatype": {
                                    "base": "NUMBER",
                                    "dataPrecision": 36,
                                    "dataScale": 12,
                                },
                            },
                            {"name": "REPORT_DATE", "datatype": {"base": "date (DD-MON-YYYY)"}},
                        ]
                    },
                }
            ]
        }
    )
    tsv = (
        "ACCESSION_NUMBER\tHOLDING_ID\tBALANCE\tREPORT_DATE\n"
        "0001-24-000001\t42\t100.500000000000\t31-JAN-2024\n"
        "0001-24-000002\t\t\t\n"  # empty fields -> NULL
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("nport_metadata.json", json.dumps(meta))
        zf.writestr("T.tsv", tsv)


def test_convert_types_values_and_partitioning(tmp_path: Path):
    zip_path = tmp_path / "2024q1_nport.zip"
    _make_zip(zip_path)
    out = tmp_path / "data"

    results = convert.convert_quarter(zip_path, 2024, 1, out)
    assert [(r.table, r.rows) for r in results] == [("T", 2)]

    parquet = out / "T" / "year=2024" / "quarter=1" / "data.parquet"
    assert parquet.exists()

    con = duckdb.connect()
    schema = {
        name: typ
        for name, typ, *_ in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{parquet}', hive_partitioning=false)"
        ).fetchall()
    }
    assert schema == {
        "ACCESSION_NUMBER": "VARCHAR",
        "HOLDING_ID": "BIGINT",
        "BALANCE": "DECIMAL(36,12)",
        "REPORT_DATE": "DATE",
    }

    rows = con.execute(
        f"SELECT ACCESSION_NUMBER, HOLDING_ID, BALANCE, REPORT_DATE "
        f"FROM read_parquet('{parquet}', hive_partitioning=false) ORDER BY ACCESSION_NUMBER"
    ).fetchall()
    assert rows[0][0] == "0001-24-000001"
    assert rows[0][1] == 42
    assert rows[0][2] == pytest.approx(100.5)
    assert rows[0][3] == datetime.date(2024, 1, 31)  # DD-MON-YYYY parsed
    assert rows[1][1] is None and rows[1][2] is None and rows[1][3] is None  # empties -> NULL

    yq = con.execute(
        f"SELECT DISTINCT year, quarter FROM read_parquet('{out}/T/**/*.parquet')"
    ).fetchall()
    assert yq == [(2024, 1)]  # recovered from the partition path


def test_convert_is_idempotent(tmp_path: Path):
    zip_path = tmp_path / "2024q1_nport.zip"
    _make_zip(zip_path)
    out = tmp_path / "data"
    _ = convert.convert_quarter(zip_path, 2024, 1, out)
    _ = convert.convert_quarter(zip_path, 2024, 1, out)
    part = out / "T" / "year=2024" / "quarter=1"
    assert [p.name for p in part.iterdir()] == ["data.parquet"]  # no leftover temp files


def test_malformed_metadata_raises_domain_error(tmp_path: Path):
    zip_path = tmp_path / "2024q1_nport.zip"
    _make_zip(zip_path, metadata={"not_tables": []})  # missing required 'tables'
    with pytest.raises(ExternalServiceError):
        convert.read_metadata(zip_path)


def test_missing_metadata_file_raises_domain_error(tmp_path: Path):
    zip_path = tmp_path / "2024q1_nport.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("T.tsv", "a\tb\n1\t2\n")
    with pytest.raises(ExternalServiceError):
        convert.read_metadata(zip_path)


def test_table_absent_from_zip_is_skipped(tmp_path: Path):
    # Early quarters declare tables in metadata whose TSV isn't shipped.
    meta = {
        "tables": [
            {
                "url": "T.tsv",
                "tableSchema": {"columns": [{"name": "A", "datatype": {"base": "string"}}]},
            },
            {
                "url": "GONE.tsv",
                "tableSchema": {"columns": [{"name": "A", "datatype": {"base": "string"}}]},
            },
        ]
    }
    zip_path = tmp_path / "2019q4_nport.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("nport_metadata.json", json.dumps(meta))
        zf.writestr("T.tsv", "A\nx\n")  # GONE.tsv intentionally absent
    out = tmp_path / "data"

    results = convert.convert_quarter(zip_path, 2019, 4, out)
    assert [r.table for r in results] == ["T"]  # GONE skipped, no crash
    assert not (out / "GONE").exists()
