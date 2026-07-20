from __future__ import annotations

import csv
from pathlib import Path

from pa_agent.records.trade_logger import _CSV_FIELDNAMES, _append_csv_row


def _row(record_time: str) -> dict[str, str]:
    return {field: (record_time if field == "record_time" else "") for field in _CSV_FIELDNAMES}


def test_trade_csv_appends_when_header_is_current(tmp_path: Path) -> None:
    path = tmp_path / "trades.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow(_row("first"))

    _append_csv_row(path, _row("second"))

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["record_time"] for row in rows] == ["first", "second"]


def test_trade_csv_migrates_legacy_header_once(tmp_path: Path) -> None:
    path = tmp_path / "trades.csv"
    path.write_text("record_time,symbol\nfirst,BTCUSDT\n", encoding="utf-8")

    _append_csv_row(path, _row("second"))

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert reader.fieldnames == _CSV_FIELDNAMES
    assert [row["record_time"] for row in rows] == ["first", "second"]
