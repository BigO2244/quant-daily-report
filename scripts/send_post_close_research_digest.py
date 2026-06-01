#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.quant_report import send_email  # noqa: E402


ET = ZoneInfo("America/New_York")


def _load_dotenv(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _is_date_text(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
    except Exception:
        return False
    return True


def _today_et() -> str:
    return dt.datetime.now(ET).date().isoformat()


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def _date_dirs(root: Path, required_file: str | None = None) -> set[str]:
    if not root.exists():
        return set()
    out: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or not _is_date_text(child.name):
            continue
        if required_file and not (child / required_file).exists():
            continue
        out.add(child.name)
    return out


def _latest_price_hydration_date(repo_root: Path, today: str) -> str | None:
    root = repo_root / "outputs" / "price_hydration"
    candidates: list[str] = []
    for date_text in sorted(_date_dirs(root, "status.json")):
        if date_text > today:
            continue
        payload = _read_json(root / date_text / "status.json") or {}
        if str(payload.get("status") or "").upper() == "OK":
            candidates.append(str(payload.get("as_of_date") or payload.get("trade_date") or date_text))
    return sorted(candidates)[-1] if candidates else None


def select_target_date(repo_root: Path | str = Path("."), explicit_date: str | None = None) -> tuple[str, list[str]]:
    repo = Path(repo_root)
    today = _today_et()
    if explicit_date:
        if not _is_date_text(explicit_date):
            raise ValueError(f"invalid_date:{explicit_date}")
        if explicit_date > today:
            raise ValueError(f"future_date_not_allowed:{explicit_date}")
        return explicit_date, ["date_explicit"]

    hydrated = _latest_price_hydration_date(repo, today)
    if hydrated:
        return hydrated, ["date_selected_latest_successful_price_hydration"]

    shadow_dates = _date_dirs(repo / "outputs" / "shadow_candidates")
    actionable_shadow_dates = [
        date_text
        for date_text in shadow_dates
        if date_text <= today and any((repo / "outputs" / "shadow_candidates" / date_text / name).exists() for name in ("caerus_polaris.json", "caerus_orion.json", "caerus_lyra.json"))
    ]
    if actionable_shadow_dates:
        return sorted(actionable_shadow_dates)[-1], ["date_selected_latest_shadow_candidate_artifact"]

    attribution_dates = _date_dirs(repo / "outputs" / "attribution", "attribution_summary.json")
    decision_dates = _date_dirs(repo / "outputs" / "decision_attribution", "strategy_decision_summary.json")
    core_dates = sorted(date_text for date_text in attribution_dates | decision_dates if date_text <= today)
    if core_dates:
        return core_dates[-1], ["date_selected_latest_review_artifact"]

    return today, ["date_defaulted_today_no_actionable_artifacts"]


def _position_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "n/a"
    return "{symbol} ret={ret} pnl={pnl}".format(
        symbol=row.get("symbol") or "n/a",
        ret=_fmt(row.get("return_pct") if row.get("return_pct") is not None else row.get("realized_return")),
        pnl=_fmt(row.get("pnl_contribution_pct") if row.get("pnl_contribution_pct") is not None else row.get("pnl_contribution")),
    )


def _load_review(repo_root: Path, trade_date: str) -> tuple[dict[str, Any], Path, Path | None]:
    review_path = repo_root / "outputs" / "research_review" / trade_date / "research_review.json"
    summary_path = repo_root / "outputs" / "research_review" / trade_date / "research_review_summary.json"
    review = _read_json(review_path)
    if review is None:
        raise RuntimeError(f"research_review_missing:{review_path}")
    summary = _read_json(summary_path)
    return review, review_path, summary_path if summary is not None else None


def build_digest_email(repo_root: Path | str, trade_date: str) -> dict[str, Any]:
    repo = Path(repo_root)
    review, review_path, summary_path = _load_review(repo, trade_date)
    sections = review.get("sections") if isinstance(review.get("sections"), dict) else {}
    overall = review.get("overall") if isinstance(review.get("overall"), dict) else {}
    position = sections.get("position_attribution") if isinstance(sections.get("position_attribution"), dict) else {}
    decision = sections.get("decision_attribution") if isinstance(sections.get("decision_attribution"), dict) else {}
    signal = sections.get("signal_quality") if isinstance(sections.get("signal_quality"), dict) else {}
    freshness = sections.get("data_freshness") if isinstance(sections.get("data_freshness"), dict) else {}
    actions = list(sections.get("recommended_next_actions") or [])

    contributors = position.get("top_contributor_per_strategy") if isinstance(position.get("top_contributor_per_strategy"), dict) else {}
    detractors = position.get("top_detractor_per_strategy") if isinstance(position.get("top_detractor_per_strategy"), dict) else {}
    top_lines = [f"{strategy}: {_position_label(row)}" for strategy, row in sorted(contributors.items())]
    bottom_lines = [f"{strategy}: {_position_label(row)}" for strategy, row in sorted(detractors.items())]
    freshness_reasons = [code for code in list(freshness.get("reason_codes") or []) if code != "ok"]
    missing_warnings = [code for code in freshness_reasons if "missing" in str(code).lower() or "stale" in str(code).lower()]
    paths = {
        "html": f"outputs/research_review/{trade_date}/research_review.html",
        "md": f"outputs/research_review/{trade_date}/research_review.md",
        "json": f"outputs/research_review/{trade_date}/research_review.json",
    }

    subject = f"[Alpha Stack] Post-Close Research Digest — {trade_date}"
    text_lines = [
        f"Post-Close Research Digest — {trade_date}",
        "",
        f"Research readiness: {_fmt(overall.get('readiness'))}",
        f"Confidence: {_fmt(overall.get('confidence'))}",
        f"Attribution status: {'available' if position.get('available') else 'missing'}",
        f"Positions analyzed: {_fmt(position.get('total_positions_analyzed'))}",
        f"Decisions analyzed: {_fmt(decision.get('decisions_analyzed'))}",
        f"Price freshness: {_fmt(position.get('is_price_source_fresh'))} (max date: {_fmt(position.get('price_source_max_date'))})",
        "",
        "Top contributors:",
        *(f"- {line}" for line in top_lines),
        "",
        "Top detractors:",
        *(f"- {line}" for line in bottom_lines),
        "",
        "Signal notes:",
        f"- Strongest observed signal: {_fmt((signal.get('strongest_observed_signal') or {}).get('signal_name'))}",
        f"- Weakest observed signal: {_fmt((signal.get('weakest_observed_signal') or {}).get('signal_name'))}",
        f"- Signal confidence: {_fmt(signal.get('confidence'))}",
        f"- Signal reason codes: {_fmt(signal.get('reason_codes'))}",
        "",
        "Data freshness / missing artifact warnings:",
        *(f"- {code}" for code in (missing_warnings or ["none"])),
        "",
        "Recommended next actions:",
        *(f"- {action}" for action in (actions or ["No action generated."])),
        "",
        "Generated files:",
        f"- HTML: {paths['html']}",
        f"- Markdown: {paths['md']}",
        f"- JSON: {paths['json']}",
    ]
    if summary_path:
        text_lines.extend(["", f"Summary source: {summary_path}"])
    text_lines.append(f"Review source: {review_path}")

    body_text = "\n".join(text_lines)
    body_html = _build_digest_html(
        trade_date=trade_date,
        overall=overall,
        position=position,
        decision=decision,
        signal=signal,
        warnings=missing_warnings,
        actions=actions,
        top_lines=top_lines,
        bottom_lines=bottom_lines,
        paths=paths,
    )
    return {
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "paths": paths,
        "review_path": str(review_path),
        "summary_path": str(summary_path) if summary_path else None,
        "readiness": overall.get("readiness"),
        "confidence": overall.get("confidence"),
    }


def _build_digest_html(
    *,
    trade_date: str,
    overall: dict[str, Any],
    position: dict[str, Any],
    decision: dict[str, Any],
    signal: dict[str, Any],
    warnings: list[str],
    actions: list[str],
    top_lines: list[str],
    bottom_lines: list[str],
    paths: dict[str, str],
) -> str:
    def items(rows: list[str]) -> str:
        return "".join(f"<li>{html.escape(row)}</li>" for row in rows) or "<li>none</li>"

    return f"""<!doctype html>
<html>
<body style="font-family:Arial,sans-serif;color:#1f2933">
  <h2>Post-Close Research Digest — {html.escape(trade_date)}</h2>
  <p><strong>Research readiness:</strong> {html.escape(_fmt(overall.get("readiness")))}<br>
  <strong>Confidence:</strong> {html.escape(_fmt(overall.get("confidence")))}<br>
  <strong>Attribution status:</strong> {html.escape("available" if position.get("available") else "missing")}<br>
  <strong>Positions analyzed:</strong> {html.escape(_fmt(position.get("total_positions_analyzed")))}<br>
  <strong>Decisions analyzed:</strong> {html.escape(_fmt(decision.get("decisions_analyzed")))}<br>
  <strong>Price freshness:</strong> {html.escape(_fmt(position.get("is_price_source_fresh")))} ({html.escape(_fmt(position.get("price_source_max_date")))})</p>

  <h3>Top Contributors</h3>
  <ul>{items(top_lines)}</ul>
  <h3>Top Detractors</h3>
  <ul>{items(bottom_lines)}</ul>
  <h3>Signal Notes</h3>
  <ul>
    <li>Strongest observed signal: {html.escape(_fmt((signal.get("strongest_observed_signal") or {}).get("signal_name")))}</li>
    <li>Weakest observed signal: {html.escape(_fmt((signal.get("weakest_observed_signal") or {}).get("signal_name")))}</li>
    <li>Signal confidence: {html.escape(_fmt(signal.get("confidence")))}</li>
    <li>Reason codes: {html.escape(_fmt(signal.get("reason_codes")))}</li>
  </ul>
  <h3>Data Freshness / Missing Artifact Warnings</h3>
  <ul>{items(warnings or ["none"])}</ul>
  <h3>Recommended Next Actions</h3>
  <ul>{items(actions or ["No action generated."])}</ul>
  <h3>Generated Files</h3>
  <ul>
    <li>HTML: {html.escape(paths["html"])}</li>
    <li>Markdown: {html.escape(paths["md"])}</li>
    <li>JSON: {html.escape(paths["json"])}</li>
  </ul>
</body>
</html>"""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send the post-close Caerus research digest email.")
    parser.add_argument("--date", default=None, help="Digest date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--no-email", action="store_true", help="Build and print the email body without sending.")
    parser.add_argument("--print-target-date", action="store_true", help="Print selected target date and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo = Path(args.repo_root)
    try:
        target_date, reason_codes = select_target_date(repo, args.date)
    except ValueError as exc:
        print(f"[POST_CLOSE_DIGEST][ERROR] {exc}", file=sys.stderr)
        return 2
    if args.print_target_date:
        print(target_date)
        return 0

    digest = build_digest_email(repo, target_date)
    if args.no_email:
        print(f"Subject: {digest['subject']}\n")
        print(digest["body_text"])
        print(f"[POST_CLOSE_DIGEST][NO_EMAIL] target_date={target_date} reason_codes={','.join(reason_codes)}")
        return 0

    _load_dotenv(repo)
    send_email(
        subject=str(digest["subject"]),
        body_text=str(digest["body_text"]),
        body_html=str(digest["body_html"]),
    )
    print(f"[POST_CLOSE_DIGEST][OK] email sent: {digest['subject']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
