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
from research.cio_briefing import build_cio_briefing  # noqa: E402


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


def _fmt_pct(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{numeric * 100:+.1f}%"


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
    summary = _read_json(summary_path) if summary_path else None
    if summary and isinstance(summary.get("sections"), dict):
        review = _merge_summary_sections(review, summary)
    sections = review.get("sections") if isinstance(review.get("sections"), dict) else {}
    overall = review.get("overall") if isinstance(review.get("overall"), dict) else {}
    position = sections.get("position_attribution") if isinstance(sections.get("position_attribution"), dict) else {}
    decision = sections.get("decision_attribution") if isinstance(sections.get("decision_attribution"), dict) else {}
    signal = sections.get("signal_quality") if isinstance(sections.get("signal_quality"), dict) else {}
    freshness = sections.get("data_freshness") if isinstance(sections.get("data_freshness"), dict) else {}
    risk_coverage = sections.get("risk_coverage") if isinstance(sections.get("risk_coverage"), dict) else {}
    deep_diff = sections.get("strategy_differentiation_deep") if isinstance(sections.get("strategy_differentiation_deep"), dict) else {}
    sizing = sections.get("position_sizing_research") if isinstance(sections.get("position_sizing_research"), dict) else {}
    universe = sections.get("universe_governance") if isinstance(sections.get("universe_governance"), dict) else {}
    tier2 = sections.get("tier2_research_controls") if isinstance(sections.get("tier2_research_controls"), dict) else {}
    promotion_governance = sections.get("promotion_governance") if isinstance(sections.get("promotion_governance"), dict) else {}
    regime_attribution = sections.get("regime_attribution") if isinstance(sections.get("regime_attribution"), dict) else {}
    dynamic_allocation = sections.get("dynamic_strategy_allocation") if isinstance(sections.get("dynamic_strategy_allocation"), dict) else {}
    tier3 = sections.get("tier3_research_controls") if isinstance(sections.get("tier3_research_controls"), dict) else {}
    final_summary = sections.get("final_control_summary") if isinstance(sections.get("final_control_summary"), dict) else {}
    cio = build_cio_briefing(review, repo)
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

    subject = f"[Alpha Stack] CIO Research Briefing — {trade_date}"
    thirty = cio.get("thirty_second_read") if isinstance(cio.get("thirty_second_read"), dict) else {}
    leaderboard = list(cio.get("strategy_leaderboard") or [])
    attribution_interpretation = cio.get("attribution_interpretation") if isinstance(cio.get("attribution_interpretation"), dict) else {}
    signal_assessment = cio.get("signal_evidence_assessment") if isinstance(cio.get("signal_evidence_assessment"), dict) else {}
    risk_assessment = cio.get("risk_blocker_assessment") if isinstance(cio.get("risk_blocker_assessment"), dict) else {}
    recommendation = cio.get("cio_recommendation") if isinstance(cio.get("cio_recommendation"), dict) else {}
    primary_recommendation = str(recommendation.get("primary") or (actions[0] if actions else "No action generated."))
    secondary_recommendations = list(recommendation.get("secondary") or [])
    leaderboard_lines = [
        (
            f"{row.get('rank')}. {row.get('strategy')} - hit rate {_fmt_pct(row.get('hit_rate'))}, "
            f"avg return {_fmt_pct(row.get('average_realized_return'))}, "
            f"avg contribution {_fmt_pct(row.get('average_pnl_contribution'))}"
        )
        for row in leaderboard
        if isinstance(row, dict)
    ]
    text_lines = [
        "CIO Briefing",
        "",
        str(cio.get("cio_takeaway") or "No CIO briefing narrative is available."),
        "",
        "30-Second Read",
        f"- Readiness: {_fmt(thirty.get('readiness') or overall.get('readiness'))}",
        f"- Confidence: {_fmt(thirty.get('confidence') or overall.get('confidence'))}",
        f"- Leading strategy: {_fmt(thirty.get('leading_strategy'))}",
        f"- Main contributor: {_fmt(thirty.get('main_contributor'))}",
        f"- Main detractor: {_fmt(thirty.get('main_detractor'))}",
        f"- Biggest blocker: {_fmt(thirty.get('biggest_blocker'))}",
        f"- Recommended action: {_fmt(thirty.get('recommended_action') or primary_recommendation)}",
        "",
        "Strategy Leaderboard",
        *(leaderboard_lines or ["No strategy leaderboard available."]),
        "",
        "Key Attribution Notes",
        str(attribution_interpretation.get("narrative") or "Attribution interpretation is not available."),
        "",
        "Signal Evidence",
        str(signal_assessment.get("conclusion") or "Signal evidence is not available."),
        "",
        "Risks / Blockers",
        str(risk_assessment.get("narrative") or "Risk and blocker assessment is not available."),
        "",
        "Recommended Action",
        primary_recommendation,
        *(f"- {item}" for item in secondary_recommendations[:3]),
        "",
        "Technical Appendix",
        "",
        f"Research readiness: {_fmt(overall.get('readiness'))}",
        f"Confidence: {_fmt(overall.get('confidence'))}",
        f"Attribution status: {'available' if position.get('available') else 'missing'}",
        f"Positions analyzed: {_fmt(position.get('total_positions_analyzed'))}",
        f"Decisions analyzed: {_fmt(decision.get('decisions_analyzed'))}",
        f"Price freshness: {_fmt(position.get('is_price_source_fresh'))} (max date: {_fmt(position.get('price_source_max_date'))})",
        f"Tier 2 risk coverage: {_fmt('available' if risk_coverage.get('available') else 'missing')} ({_fmt(risk_coverage.get('risk_level'))})",
        f"Deep differentiation verdict: {_fmt(deep_diff.get('aggregate_verdict'))}",
        f"Position sizing research: {_fmt('available' if sizing.get('available') else 'missing')}",
        f"Universe governance: {_fmt('available' if universe.get('available') else 'missing')}",
        f"Tier 2 recommendation: {_fmt(tier2.get('recommendation'))}",
        f"Tier 3 promotion governance: {_fmt('available' if promotion_governance.get('available') else 'missing')}",
        f"Tier 3 regime attribution: {_fmt('available' if regime_attribution.get('available') else 'missing')}",
        f"Tier 3 dynamic allocation: {_fmt('available' if dynamic_allocation.get('available') else 'missing')} (research only)",
        f"Tier 3 recommendation: {_fmt(tier3.get('recommendation'))}",
        f"Final control recommendation: {_fmt(final_summary.get('current_recommendation'))}",
        f"Polaris status: {_fmt(final_summary.get('polaris_status'))}",
        f"Orion status: {_fmt(final_summary.get('orion_status'))}",
        f"Lyra status: {_fmt(final_summary.get('lyra_status'))}",
        "",
        "Top contributors:",
        *(f"- {line}" for line in top_lines),
        "",
        "Top detractors:",
        *(f"- {line}" for line in bottom_lines),
        "",
        "Signal notes:",
        f"- Conclusion: {_fmt(signal_assessment.get('conclusion') or signal.get('reason_codes'))}",
        f"- Signal confidence: {_fmt(signal_assessment.get('confidence') or signal.get('confidence'))}",
        "",
        "Data freshness / missing artifact warnings:",
        *(f"- {code}" for code in (missing_warnings or ["none"])),
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
        cio=cio,
        warnings=missing_warnings,
        actions=actions,
        top_lines=top_lines,
        bottom_lines=bottom_lines,
        paths=paths,
        risk_coverage=risk_coverage,
        deep_diff=deep_diff,
        sizing=sizing,
        universe=universe,
        tier2=tier2,
        promotion_governance=promotion_governance,
        regime_attribution=regime_attribution,
        dynamic_allocation=dynamic_allocation,
        tier3=tier3,
        final_summary=final_summary,
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


def _merge_summary_sections(review: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(review)
    merged_sections = dict(review.get("sections") if isinstance(review.get("sections"), dict) else {})
    summary_sections = summary.get("sections") if isinstance(summary.get("sections"), dict) else {}
    for name, section in summary_sections.items():
        if not isinstance(section, dict):
            continue
        current = merged_sections.get(name)
        if not isinstance(current, dict) or _section_content_score(section) > _section_content_score(current):
            merged_sections[name] = section
    if merged_sections:
        merged["sections"] = merged_sections
    if isinstance(summary.get("overall"), dict) and not isinstance(merged.get("overall"), dict):
        merged["overall"] = summary["overall"]
    return merged


def _section_content_score(section: dict[str, Any]) -> int:
    score = 0
    for key in (
        "total_positions_analyzed",
        "positions_analyzed",
        "decisions_analyzed",
        "total_decisions_analyzed",
    ):
        try:
            score += int(section.get(key) or 0)
        except (TypeError, ValueError):
            pass
    for key in (
        "strategies",
        "signals",
        "top_contributor_per_strategy",
        "top_detractor_per_strategy",
        "top_contributors",
        "top_detractors",
    ):
        value = section.get(key)
        if isinstance(value, dict):
            nested = value.get("positions") or value.get("strategies") or value.get("signals")
            score += len(nested) if isinstance(nested, list) else len(value)
        elif isinstance(value, list):
            score += len(value)
    return score


def _build_digest_html(
    *,
    trade_date: str,
    overall: dict[str, Any],
    position: dict[str, Any],
    decision: dict[str, Any],
    signal: dict[str, Any],
    cio: dict[str, Any],
    warnings: list[str],
    actions: list[str],
    top_lines: list[str],
    bottom_lines: list[str],
    paths: dict[str, str],
    risk_coverage: dict[str, Any],
    deep_diff: dict[str, Any],
    sizing: dict[str, Any],
    universe: dict[str, Any],
    tier2: dict[str, Any],
    promotion_governance: dict[str, Any] | None = None,
    regime_attribution: dict[str, Any] | None = None,
    dynamic_allocation: dict[str, Any] | None = None,
    tier3: dict[str, Any] | None = None,
    final_summary: dict[str, Any] | None = None,
) -> str:
    promotion_governance = promotion_governance or {}
    regime_attribution = regime_attribution or {}
    dynamic_allocation = dynamic_allocation or {}
    tier3 = tier3 or {}
    final_summary = final_summary or {}
    def items(rows: list[str]) -> str:
        return "".join(f"<li>{html.escape(row)}</li>" for row in rows) or "<li>none</li>"

    thirty = cio.get("thirty_second_read") if isinstance(cio.get("thirty_second_read"), dict) else {}
    leaderboard = [
        (
            f"{row.get('rank')}. {row.get('strategy')} - hit rate {_fmt_pct(row.get('hit_rate'))}, "
            f"avg return {_fmt_pct(row.get('average_realized_return'))}, "
            f"avg contribution {_fmt_pct(row.get('average_pnl_contribution'))}"
        )
        for row in list(cio.get("strategy_leaderboard") or [])
        if isinstance(row, dict)
    ]
    attribution = cio.get("attribution_interpretation") if isinstance(cio.get("attribution_interpretation"), dict) else {}
    signal_assessment = cio.get("signal_evidence_assessment") if isinstance(cio.get("signal_evidence_assessment"), dict) else {}
    risk = cio.get("risk_blocker_assessment") if isinstance(cio.get("risk_blocker_assessment"), dict) else {}
    recommendation = cio.get("cio_recommendation") if isinstance(cio.get("cio_recommendation"), dict) else {}
    return f"""<!doctype html>
<html>
<body style="font-family:Arial,sans-serif;color:#1f2933">
  <h2>CIO Research Briefing — {html.escape(trade_date)}</h2>
  <h3>CIO Briefing</h3>
  <p>{html.escape(str(cio.get("cio_takeaway") or "No CIO briefing narrative is available."))}</p>
  <h3>30-Second Read</h3>
  <ul>
    <li>Readiness: {html.escape(_fmt(thirty.get("readiness") or overall.get("readiness")))}</li>
    <li>Confidence: {html.escape(_fmt(thirty.get("confidence") or overall.get("confidence")))}</li>
    <li>Leading strategy: {html.escape(_fmt(thirty.get("leading_strategy")))}</li>
    <li>Main contributor: {html.escape(_fmt(thirty.get("main_contributor")))}</li>
    <li>Main detractor: {html.escape(_fmt(thirty.get("main_detractor")))}</li>
    <li>Biggest blocker: {html.escape(_fmt(thirty.get("biggest_blocker")))}</li>
    <li>Recommended action: {html.escape(_fmt(thirty.get("recommended_action") or recommendation.get("primary")))}</li>
  </ul>
  <h3>Strategy Leaderboard</h3>
  <ul>{items(leaderboard or ["No strategy leaderboard available."])}</ul>
  <h3>Key Attribution Notes</h3>
  <p>{html.escape(str(attribution.get("narrative") or "Attribution interpretation is not available."))}</p>
  <h3>Signal Evidence</h3>
  <p>{html.escape(str(signal_assessment.get("conclusion") or "Signal evidence is not available."))}</p>
  <h3>Risks / Blockers</h3>
  <p>{html.escape(str(risk.get("narrative") or "Risk and blocker assessment is not available."))}</p>
  <h3>Recommended Action</h3>
  <p>{html.escape(str(recommendation.get("primary") or (actions[0] if actions else "No action generated.")))}</p>

  <h3>Technical Appendix</h3>
  <p><strong>Research readiness:</strong> {html.escape(_fmt(overall.get("readiness")))}<br>
  <strong>Confidence:</strong> {html.escape(_fmt(overall.get("confidence")))}<br>
  <strong>Attribution status:</strong> {html.escape("available" if position.get("available") else "missing")}<br>
  <strong>Positions analyzed:</strong> {html.escape(_fmt(position.get("total_positions_analyzed")))}<br>
  <strong>Decisions analyzed:</strong> {html.escape(_fmt(decision.get("decisions_analyzed")))}<br>
  <strong>Price freshness:</strong> {html.escape(_fmt(position.get("is_price_source_fresh")))} ({html.escape(_fmt(position.get("price_source_max_date")))})<br>
  <strong>Tier 2 risk coverage:</strong> {html.escape(_fmt("available" if risk_coverage.get("available") else "missing"))} ({html.escape(_fmt(risk_coverage.get("risk_level")))})<br>
  <strong>Deep differentiation verdict:</strong> {html.escape(_fmt(deep_diff.get("aggregate_verdict")))}<br>
  <strong>Position sizing research:</strong> {html.escape(_fmt("available" if sizing.get("available") else "missing"))}<br>
  <strong>Universe governance:</strong> {html.escape(_fmt("available" if universe.get("available") else "missing"))}<br>
  <strong>Tier 2 recommendation:</strong> {html.escape(_fmt(tier2.get("recommendation")))}<br>
  <strong>Tier 3 promotion governance:</strong> {html.escape(_fmt("available" if promotion_governance.get("available") else "missing"))}<br>
  <strong>Tier 3 regime attribution:</strong> {html.escape(_fmt("available" if regime_attribution.get("available") else "missing"))}<br>
  <strong>Tier 3 dynamic allocation (research only):</strong> {html.escape(_fmt("available" if dynamic_allocation.get("available") else "missing"))}<br>
  <strong>Tier 3 recommendation:</strong> {html.escape(_fmt(tier3.get("recommendation")))}<br>
  <strong>Final control recommendation:</strong> {html.escape(_fmt(final_summary.get("current_recommendation")))}<br>
  <strong>Polaris status:</strong> {html.escape(_fmt(final_summary.get("polaris_status")))}<br>
  <strong>Orion status:</strong> {html.escape(_fmt(final_summary.get("orion_status")))}<br>
  <strong>Lyra status:</strong> {html.escape(_fmt(final_summary.get("lyra_status")))}</p>

  <h3>Top Contributors</h3>
  <ul>{items(top_lines)}</ul>
  <h3>Top Detractors</h3>
  <ul>{items(bottom_lines)}</ul>
  <h3>Signal Notes</h3>
  <ul>
    <li>Conclusion: {html.escape(_fmt(signal_assessment.get("conclusion") or signal.get("reason_codes")))}</li>
    <li>Signal confidence: {html.escape(_fmt(signal_assessment.get("confidence") or signal.get("confidence")))}</li>
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
