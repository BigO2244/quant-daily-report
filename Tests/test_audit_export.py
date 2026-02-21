from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd

from audit.export import write_audit_bundle


def _xlsx_sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path, "r") as zf:
        workbook_xml = zf.read("xl/workbook.xml")
    root = ET.fromstring(workbook_xml)
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [node.attrib.get("name", "") for node in root.findall("x:sheets/x:sheet", ns)]


def test_audit_export_writes_files(tmp_path: Path) -> None:
    trades = pd.DataFrame(
        [
            {
                "date": "2022-01-03",
                "ticker": "AAPL",
                "sleeve": "sleeve_1",
                "side": "BUY",
                "shares": 10.0,
                "price": 150.0,
                "notional": 1500.0,
                "reason": "rebalance",
            }
        ]
    )
    holdings = pd.DataFrame(
        [
            {
                "date": "2022-01-03",
                "ticker": "AAPL",
                "sleeve": "sleeve_1",
                "shares": 10.0,
                "price": 150.0,
                "market_value": 1500.0,
                "weight": 0.15,
            },
            {
                "date": "2022-01-03",
                "ticker": "CASH",
                "sleeve": "CASH",
                "shares": 8500.0,
                "price": 1.0,
                "market_value": 8500.0,
                "weight": 0.85,
            },
        ]
    )
    portfolio = pd.DataFrame(
        [
            {
                "date": "2022-01-03",
                "total_equity": 10_000.0,
                "cash": 8_500.0,
                "gross_exposure": 0.15,
                "net_exposure": 0.15,
                "turnover": 0.15,
            }
        ]
    )
    summary = {"policy": "FULL", "max_drawdown": -0.12, "cagr": 0.08}

    out_dir = write_audit_bundle(
        run_id="unit_test_run",
        outdir=tmp_path / "audit" / "unit_test_run",
        trades_df=trades,
        holdings_daily_df=holdings,
        portfolio_daily_df=portfolio,
        summary=summary,
    )

    assert (out_dir / "trades.csv").exists()
    assert (out_dir / "holdings_daily.csv").exists()
    assert (out_dir / "portfolio_daily.csv").exists()
    workbook = out_dir / "audit.xlsx"
    assert workbook.exists()

    sheet_names = _xlsx_sheet_names(workbook)
    assert sheet_names == ["Summary", "Trades", "HoldingsDaily", "PortfolioDaily"]
