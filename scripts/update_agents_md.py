from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

NIGHTLY_START = "<!-- BEGIN AUTO-GENERATED: NIGHTLY FINDINGS -->"
NIGHTLY_END = "<!-- END AUTO-GENERATED: NIGHTLY FINDINGS -->"
WORKFLOW_START = "<!-- BEGIN AUTO-GENERATED: WORKFLOW INVENTORY -->"
WORKFLOW_END = "<!-- END AUTO-GENERATED: WORKFLOW INVENTORY -->"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _replace_section(text: str, start_marker: str, end_marker: str, body: str) -> str:
    pattern = re.compile(
        rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}",
        flags=re.DOTALL,
    )
    replacement = f"{start_marker}\n{body.rstrip()}\n{end_marker}"
    if not pattern.search(text):
        raise ValueError(f"Missing marker pair: {start_marker} / {end_marker}")
    return pattern.sub(replacement, text, count=1)


def _iter_candidate_files(*roots: Path) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            candidates.append(root)
            continue
        for suffix in ("*.json", "*.md"):
            candidates.extend(
                path for path in root.rglob(suffix) if path.is_file() and path.name != ".DS_Store"
            )
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def _extract_markdown_points(text: str) -> list[str]:
    points: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("- ", "* ")):
            points.append(line[2:].strip())
        elif not points:
            points.append(line)
        if len(points) >= 6:
            break
    return points


def _extract_json_points(payload: dict) -> list[str]:
    points: list[str] = []
    for key in ("headline", "summary", "findings", "observations", "risks", "actions"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            points.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    points.append(item.strip())
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                points.append(f"{sub_key}: {sub_value}")
        if len(points) >= 6:
            break
    return points[:6]


def _render_nightly_findings(report_dirs: list[Path]) -> str:
    preferred: list[Path] = []
    for root in report_dirs:
        preferred.append(root / "nightly_findings.json")
        preferred.append(root / "nightly_findings.md")
    for path in preferred:
        if path.exists():
            chosen = path
            break
    else:
        candidates = _iter_candidate_files(*report_dirs)
        chosen = candidates[0] if candidates else None

    stamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    lines = [f"- Last refresh: `{stamp}`"]
    if chosen is None:
        lines.append("- No nightly findings artifact was found under `reports/agents/`, `reports/ai_runs/`, or `reports/incidents/`.")
        lines.append("- Action: drop a `nightly_findings.json` or `nightly_findings.md` file into `reports/agents/` to populate this section.")
        return "\n".join(lines)

    lines.append(f"- Source: `{chosen.as_posix()}`")
    if chosen.suffix.lower() == ".json":
        payload = json.loads(_read_text(chosen))
        points = _extract_json_points(payload)
        generated_at = payload.get("generated_at") or payload.get("as_of")
        if generated_at:
            lines.append(f"- Findings generated at: `{generated_at}`")
    else:
        points = _extract_markdown_points(_read_text(chosen))
    if not points:
        points = ["Nightly findings file was present but did not contain summary-friendly fields."]
    lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def _parse_workflow_file(path: Path) -> dict[str, object]:
    text = _read_text(path)
    name_match = re.search(r"^name:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    cron_matches = re.findall(r"cron:\s*[\"']([^\"']+)[\"']", text)
    dispatch = bool(re.search(r"^\s*workflow_dispatch:\s*$", text, flags=re.MULTILINE))
    return {
        "file": path.name,
        "name": (name_match.group(1).strip() if name_match else path.stem),
        "dispatch": dispatch,
        "crons": cron_matches,
    }


def _parse_audit_inventory(audit_path: Path) -> list[dict[str, str]]:
    if not audit_path.exists():
        return []
    text = _read_text(audit_path)
    start = text.find("## Workflow Inventory")
    end = text.find("## Daily Alpaca Run Paths")
    if start != -1 and end != -1 and end > start:
        text = text[start:end]
    rows: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("| `.github/workflows/"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 6:
            continue
        rows.append(
            {
                "file": parts[0].strip("`").split("/")[-1],
                "name": parts[1].strip("`"),
                "triggers": parts[2].replace("`", "").strip(),
                "schedule": parts[3].replace("`", "").strip(),
            }
        )
    return rows


def _render_workflow_inventory(workflow_dir: Path, audit_path: Path) -> str:
    materialized = []
    if workflow_dir.exists():
        for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
            materialized.append(_parse_workflow_file(path))

    audit_rows = _parse_audit_inventory(audit_path)
    lines = []
    if materialized:
        lines.append("- Materialized workflow files in this checkout:")
        for item in materialized:
            triggers = []
            if item["dispatch"]:
                triggers.append("workflow_dispatch")
            if item["crons"]:
                triggers.append("schedule")
            triggers = [trigger for trigger in triggers if trigger]
            cron_text = ", ".join(f"`{cron}`" for cron in item["crons"]) or "none"
            trigger_text = ", ".join(triggers) or "unknown"
            lines.append(
                f"- `{item['file']}`: {item['name']} | triggers={trigger_text} | cron={cron_text}"
            )
    else:
        lines.append("- No materialized workflow YAML files were found under `.github/workflows/` in this checkout.")

    if audit_rows:
        lines.append("- Last audited workflow inventory from `repo_workflow_audit.md`:")
        for row in audit_rows:
            lines.append(
                f"- `{row['file']}`: {row['name']} | triggers={row['triggers']} | schedule={row['schedule']}"
            )
    else:
        lines.append("- No workflow audit inventory was available.")
    return "\n".join(lines)


def update_agents(
    *,
    agents_path: Path,
    workflow_dir: Path,
    audit_path: Path,
    report_dirs: list[Path],
) -> bool:
    original = _read_text(agents_path)
    nightly = _render_nightly_findings(report_dirs)
    workflows = _render_workflow_inventory(workflow_dir, audit_path)
    updated = _replace_section(original, NIGHTLY_START, NIGHTLY_END, nightly)
    updated = _replace_section(updated, WORKFLOW_START, WORKFLOW_END, workflows)
    if updated == original:
        return False
    agents_path.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the auto-generated sections in AGENTS.md")
    parser.add_argument("--agents", default="AGENTS.md")
    parser.add_argument("--workflow-dir", default=".github/workflows")
    parser.add_argument("--audit", default="repo_workflow_audit.md")
    parser.add_argument(
        "--report-dir",
        action="append",
        default=[],
        help="Directory or file to search for nightly findings; may be passed multiple times",
    )
    args = parser.parse_args()

    report_dirs = [Path(path) for path in args.report_dir] or [
        Path("reports/agents"),
        Path("reports/ai_runs"),
        Path("reports/incidents"),
    ]
    changed = update_agents(
        agents_path=Path(args.agents),
        workflow_dir=Path(args.workflow_dir),
        audit_path=Path(args.audit),
        report_dirs=report_dirs,
    )
    print("updated" if changed else "unchanged")


if __name__ == "__main__":
    main()
