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
