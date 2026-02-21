from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
import math
import zipfile

import pandas as pd


TRADES_COLUMNS = [
    "date",
    "ticker",
    "sleeve",
    "side",
    "shares",
    "price",
    "notional",
    "reason",
]

HOLDINGS_DAILY_COLUMNS = [
    "date",
    "ticker",
    "sleeve",
    "shares",
    "price",
    "market_value",
    "weight",
]

PORTFOLIO_DAILY_COLUMNS = [
    "date",
    "total_equity",
    "cash",
    "gross_exposure",
    "net_exposure",
    "turnover",
]


def _ensure_columns(df: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = pd.NA
    return out[columns]


def _coerce_date_col(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    out = df.copy()
    if col in out.columns:
        out[col] = pd.to_datetime(out[col], errors="coerce")
    return out


def _normalize_summary(summary: Mapping | pd.DataFrame | None, run_id: str) -> pd.DataFrame:
    if summary is None:
        return pd.DataFrame([{"run_id": run_id}])
    if isinstance(summary, pd.DataFrame):
        out = summary.copy()
    elif isinstance(summary, Mapping):
        out = pd.DataFrame([dict(summary)])
    else:
        out = pd.DataFrame([{"run_id": run_id, "summary_repr": str(summary)}])
    if "run_id" not in out.columns:
        out.insert(0, "run_id", run_id)
    return out


def _excel_col(col_idx: int) -> str:
    letters = []
    n = col_idx
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _is_null(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _to_scalar_for_excel(value: object) -> object:
    if _is_null(value):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, bool):
        return int(value)
    return value


def _cell_xml(row_idx: int, col_idx: int, value: object) -> str:
    ref = f"{_excel_col(col_idx)}{row_idx}"
    v = _to_scalar_for_excel(value)
    if v is None:
        return f'<c r="{ref}"/>'
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
            return f'<c r="{ref}"/>'
        return f'<c r="{ref}" t="n"><v>{v}</v></c>'
    text = _escape_xml(str(v))
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _sheet_xml(df: pd.DataFrame) -> str:
    rows = []
    headers = list(df.columns)
    if headers:
        header_cells = "".join(
            _cell_xml(1, idx + 1, col) for idx, col in enumerate(headers)
        )
        rows.append(f'<row r="1">{header_cells}</row>')

    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        cells = "".join(
            _cell_xml(row_idx, col_idx + 1, row[col])
            for col_idx, col in enumerate(headers)
        )
        rows.append(f'<row r="{row_idx}">{cells}</row>')

    if headers and len(df) >= 0:
        last_row = max(1, len(df) + 1)
        last_col = _excel_col(len(headers))
        dimension = f"A1:{last_col}{last_row}"
    else:
        dimension = "A1"

    rows_xml = "".join(rows)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        "<sheetViews><sheetView workbookViewId=\"0\"/></sheetViews>"
        "<sheetFormatPr defaultRowHeight=\"15\"/>"
        f"<sheetData>{rows_xml}</sheetData>"
        "</worksheet>"
    )


def _write_xlsx(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_items = list(sheets.items())

    workbook_sheets = []
    workbook_rels = []
    worksheet_types = []
    worksheet_xml = {}

    for idx, (name, frame) in enumerate(sheet_items, start=1):
        safe_name = _escape_xml(str(name)[:31] or f"Sheet{idx}")
        workbook_sheets.append(
            f'<sheet name="{safe_name}" sheetId="{idx}" r:id="rId{idx}"/>'
        )
        workbook_rels.append(
            '<Relationship '
            f'Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
        worksheet_types.append(
            "<Override "
            f'PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        worksheet_xml[f"xl/worksheets/sheet{idx}.xml"] = _sheet_xml(frame)

    styles_rel_id = len(sheet_items) + 1
    workbook_rels.append(
        '<Relationship '
        f'Id="rId{styles_rel_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(worksheet_types)
        + '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
        'Target="docProps/app.xml"/>'
        "</Relationships>"
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(workbook_sheets)}</sheets>"
        "</workbook>"
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(workbook_rels)}"
        "</Relationships>"
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )

    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>quant-daily-report-main</dc:creator>"
        "<cp:lastModifiedBy>quant-daily-report-main</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>'
        "</cp:coreProperties>"
    )

    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>quant-daily-report-main</Application>"
        "</Properties>"
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
        for worksheet_path, worksheet_text in worksheet_xml.items():
            zf.writestr(worksheet_path, worksheet_text)


def write_audit_bundle(
    *,
    run_id: str,
    trades_df: pd.DataFrame | None = None,
    holdings_daily_df: pd.DataFrame | None = None,
    portfolio_daily_df: pd.DataFrame | None = None,
    summary: Mapping | pd.DataFrame | None = None,
    outdir: str | Path | None = None,
) -> Path:
    """
    Write deterministic audit artifacts for a run and return output folder path.
    """
    if not run_id:
        raise ValueError("run_id is required for audit export")

    out_path = Path(outdir) if outdir is not None else Path("outputs/audit") / run_id
    out_path.mkdir(parents=True, exist_ok=True)

    trades = _ensure_columns(trades_df, TRADES_COLUMNS)
    holdings = _ensure_columns(holdings_daily_df, HOLDINGS_DAILY_COLUMNS)
    portfolio = _ensure_columns(portfolio_daily_df, PORTFOLIO_DAILY_COLUMNS)
    summary_df = _normalize_summary(summary, run_id=run_id)

    trades = _coerce_date_col(trades).sort_values(["date", "ticker"], na_position="last")
    holdings = _coerce_date_col(holdings).sort_values(
        ["date", "ticker"], na_position="last"
    )
    portfolio = _coerce_date_col(portfolio).sort_values(["date"], na_position="last")

    for frame in (trades, holdings, portfolio, summary_df):
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime(
                "%Y-%m-%d"
            )

    trades.to_csv(out_path / "trades.csv", index=False)
    holdings.to_csv(out_path / "holdings_daily.csv", index=False)
    portfolio.to_csv(out_path / "portfolio_daily.csv", index=False)

    workbook_path = out_path / "audit.xlsx"
    _write_xlsx(
        workbook_path,
        {
            "Summary": summary_df,
            "Trades": trades,
            "HoldingsDaily": holdings,
            "PortfolioDaily": portfolio,
        },
    )
    return out_path
