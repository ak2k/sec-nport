"""Tests for dataset-card generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_nport import card

if TYPE_CHECKING:
    from pathlib import Path


def test_card_falls_back_to_all_tables(tmp_path: Path):
    out = card.write_card("ak2k/sec-nport", tmp_path)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert text.count("config_name:") == 30
    assert "hf://datasets/ak2k/sec-nport" in text


def test_card_uses_present_table_dirs(tmp_path: Path):
    part = tmp_path / "FUND_REPORTED_HOLDING" / "year=2024" / "quarter=1"
    part.mkdir(parents=True)
    _ = (part / "data.parquet").write_bytes(b"x")
    out = card.write_card("me/ds", tmp_path)
    text = out.read_text(encoding="utf-8")
    assert text.count("config_name:") == 1
    assert "FUND_REPORTED_HOLDING" in text


def test_coverage_derived_from_partitions(tmp_path: Path):
    for year, qtr in [("2019", "4"), ("2024", "1"), ("2020", "2")]:
        part = tmp_path / "SUBMISSION" / f"year={year}" / f"quarter={qtr}"
        part.mkdir(parents=True)
        _ = (part / "data.parquet").write_bytes(b"x")
    text = card.write_card("trader298/sec-nport", tmp_path).read_text(encoding="utf-8")
    assert "2019 Q4 to 2024 Q1 (3 quarters)" in text


def test_coverage_falls_back_without_data(tmp_path: Path):
    text = card.write_card("trader298/sec-nport", tmp_path).read_text(encoding="utf-8")
    assert "October 2019 onward" in text
