"""Tests for the CSVW-driven typed conversion."""

from __future__ import annotations

import datetime
import json
import zipfile
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from sync import convert  # noqa: E402  (import follows the duckdb skip guard)


def test_type_mapping():
    assert convert._duckdb_type({"base": "string", "maxLength": 20}) == "VARCHAR"
    assert convert._duckdb_type({"base": "date (DD-MON-YYYY)"}) == "DATE"
    # scale 0 -> integer key; scale > 0 -> exact decimal
    assert convert._duckdb_type({"base": "NUMBER", "dataPrecision": 38, "dataScale": 0}) == "BIGINT"
    assert convert._duckdb_type({"base": "NUMBER", "dataPrecision": 36, "dataScale": 12}) == "DECIMAL(36,12)"
    # unknown base stays text rather than failing
    assert convert._duckdb_type({"base": "wat"}) == "VARCHAR"
    assert convert._duckdb_type({}) == "VARCHAR"


def _make_zip(path: Path):
    meta = {
        "tables": [
            {
                "url": "T.tsv",
                "tableSchema": {
                    "columns": [
                        {"name": "ACCESSION_NUMBER", "datatype": {"base": "string", "maxLength": 20}},
                        {"name": "HOLDING_ID", "datatype": {"base": "NUMBER", "dataPrecision": 38, "dataScale": 0}},
                        {"name": "BALANCE", "datatype": {"base": "NUMBER", "dataPrecision": 36, "dataScale": 12}},
                        {"name": "REPORT_DATE", "datatype": {"base": "date (DD-MON-YYYY)"}},
                    ]
                },
            }
        ]
    }
    tsv = (
        "ACCESSION_NUMBER\tHOLDING_ID\tBALANCE\tREPORT_DATE\n"
        "0001-24-000001\t42\t100.500000000000\t31-JAN-2024\n"
        "0001-24-000002\t\t\t\n"  # empty fields -> NULL
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("nport_metadata.json", json.dumps(meta))
        zf.writestr("T.tsv", tsv)


def test_convert_quarter_types_and_values(tmp_path):
    zip_path = tmp_path / "2024q1_nport.zip"
    _make_zip(zip_path)
    out = tmp_path / "data"

    results = convert.convert_quarter(zip_path, 2024, 1, out)
    assert results == [{"table": "T", "rows": 2}]

    parquet = out / "T" / "year=2024" / "quarter=1" / "data.parquet"
    assert parquet.exists()  # hive-partitioned path

    con = duckdb.connect()
    # The file itself holds only the typed table columns; year/quarter are
    # encoded in the path, recovered by hive-aware readers (asserted below).
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
        f"FROM read_parquet('{parquet}', hive_partitioning=false) "
        f"ORDER BY ACCESSION_NUMBER"
    ).fetchall()
    # row 1: typed values, DD-MON-YYYY parsed to a real date
    assert rows[0][0] == "0001-24-000001"
    assert rows[0][1] == 42
    assert rows[0][2] == pytest.approx(100.5)
    assert rows[0][3] == datetime.date(2024, 1, 31)
    # row 2: empty fields became NULL
    assert rows[1][1] is None and rows[1][2] is None and rows[1][3] is None

    # year/quarter are recoverable from the partition path via a hive glob
    yq = con.execute(
        f"SELECT DISTINCT year, quarter FROM read_parquet('{out}/T/**/*.parquet')"
    ).fetchall()
    assert yq == [(2024, 1)]


def test_convert_is_idempotent(tmp_path):
    zip_path = tmp_path / "2024q1_nport.zip"
    _make_zip(zip_path)
    out = tmp_path / "data"
    convert.convert_quarter(zip_path, 2024, 1, out)
    # re-running overwrites the same partition cleanly, no leftover temp files
    convert.convert_quarter(zip_path, 2024, 1, out)
    part = out / "T" / "year=2024" / "quarter=1"
    assert [p.name for p in part.iterdir()] == ["data.parquet"]
