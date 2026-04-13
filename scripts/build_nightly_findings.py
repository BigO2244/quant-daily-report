from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_dashboard_payload(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _extract_research_digest_schedule(audit_path: Path) -> str | None:
    if not audit_path.exists():
        return None
    for raw_line in audit_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("| `.github/workflows/research-digest.yml` |"):
            parts = [part.strip().replace("`", "") for part in line.strip("|").split("|")]
            if len(parts) >= 4:
                return parts[3]
    return None


def _build_findings(payload: dict[str, Any], audit_path: Path) -> dict[str, Any]:
    broker = payload.get("broker") or {}
    pretrade = broker.get("pretrade") or {}
    posttrade = broker.get("posttrade") or {}
    delta = broker.get("delta") or {}

    authoritative = bool(broker.get("authoritativeState"))
    trust_level = str(broker.get("trustLevel") or "LOW").upper()
    pretrade_ok = bool(pretrade.get("snapshotOk"))
    posttrade_ok = bool(posttrade.get("snapshotOk"))
    recon_status = str(posttrade.get("reconStatus") or "UNKNOWN").upper()

    if authoritative and trust_level == "HIGH":
        headline = "Broker-authoritative state confirmed"
    elif trust_level == "MEDIUM":
        headline = "Broker state partially confirmed"
    else:
        headline = "Broker-authoritative state not confirmed"

    summary = [
        f"Trade date: {payload.get('tradeDate') or 'unknown'}.",
        f"Broker trust level: {trust_level}.",
        f"Pretrade status: {pretrade.get('status') or 'UNKNOWN'}; posttrade reconciliation: {recon_status}.",
    ]

    schedule = _extract_research_digest_schedule(audit_path)
    if schedule:
        summary.append(f"Nightly digest schedule from audit: {schedule}.")

    risks: list[str] = []
    if not pretrade_ok:
        risks.append("Pretrade broker snapshot was not confirmed in the latest available artifacts.")
    if not posttrade_ok:
        risks.append("Posttrade broker snapshot was not confirmed in the latest available artifacts.")
    if not authoritative:
        risks.append("The latest run did not confirm broker-authoritative post-trade state.")
    if recon_status not in {"PASS", "WARN", "OK"}:
        risks.append(f"Posttrade reconciliation status is {recon_status}.")
    for flag in list(pretrade.get("warningFlags") or []):
        risks.append(f"Broker preflight warning flag present: {flag}.")

    actions = [str(item) for item in list(posttrade.get("repairSuggestions") or []) if str(item).strip() and str(item).strip().lower() != "none"]
    if not actions:
        if not authoritative or not pretrade_ok or not posttrade_ok:
            actions.append("Sync the latest broker and run artifacts from the scheduler or CI before relying on the dashboard.")
        elif recon_status not in {"PASS", "OK"}:
            actions.append("Review the latest post-trade reconciliation report before the next scheduled execution.")
        else:
            actions.append("No operator action is currently required from the latest available artifacts.")

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "headline": headline,
        "summary": summary,
        "risks": risks,
        "actions": actions,
        "context": {
            "trade_date": payload.get("tradeDate"),
            "run_id": payload.get("runId"),
            "run_root": payload.get("runRoot"),
            "trust_level": trust_level,
            "authoritative_state": authoritative,
            "pretrade_snapshot_ok": pretrade_ok,
            "posttrade_snapshot_ok": posttrade_ok,
            "pretrade_positions_count": pretrade.get("positionsCount"),
            "posttrade_positions_count": posttrade.get("positionsCount"),
            "positions_delta": delta.get("positionsCount"),
            "cash_delta": delta.get("cash"),
            "equity_delta": delta.get("equity"),
            "affected_symbols": posttrade.get("affectedSymbols") or [],
            "paths": broker.get("paths") or {},
        },
    }


def _render_markdown(findings: dict[str, Any]) -> str:
    lines = [
        "# Nightly Findings",
        "",
        f"- Generated at: `{findings.get('generated_at')}`",
        f"- Headline: {findings.get('headline')}",
        "",
        "## Summary",
    ]
    lines.extend(f"- {item}" for item in findings.get("summary") or [])
    lines.append("")
    lines.append("## Risks")
    risks = findings.get("risks") or []
    if risks:
        lines.extend(f"- {item}" for item in risks)
    else:
        lines.append("- None detected from the available nightly artifacts.")
    lines.append("")
    lines.append("## Actions")
    lines.extend(f"- {item}" for item in findings.get("actions") or [])
    return "\n".join(lines) + "\n"


def write_findings(output_json: Path, output_md: Path, findings: dict[str, Any]) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(_render_markdown(findings), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build nightly agent findings from the latest dashboard payload")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--dashboard-data", default="web/dashboard/dashboard-data.json")
    parser.add_argument("--audit", default="repo_workflow_audit.md")
    parser.add_argument("--output-json", default="reports/agents/nightly_findings.json")
    parser.add_argument("--output-md", default="reports/agents/nightly_findings.md")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    dashboard_path = repo_root / args.dashboard_data
    audit_path = repo_root / args.audit
    findings = _build_findings(_load_dashboard_payload(dashboard_path), audit_path)
    write_findings(repo_root / args.output_json, repo_root / args.output_md, findings)
    print(str(repo_root / args.output_json))


if __name__ == "__main__":
    main()
