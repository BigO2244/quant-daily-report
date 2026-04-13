from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update_agents_md.py"


class UpdateAgentsMdTest(unittest.TestCase):
    def test_update_agents_md_populates_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            agents_path = tmp_path / "AGENTS.md"
            agents_path.write_text(
                "\n".join(
                    [
                        "# AGENTS.md",
                        "",
                        "## Auto-Generated Nightly Findings",
                        "",
                        "<!-- BEGIN AUTO-GENERATED: NIGHTLY FINDINGS -->",
                        "_Pending nightly refresh._",
                        "<!-- END AUTO-GENERATED: NIGHTLY FINDINGS -->",
                        "",
                        "## Auto-Generated Workflow Inventory",
                        "",
                        "<!-- BEGIN AUTO-GENERATED: WORKFLOW INVENTORY -->",
                        "_Pending nightly refresh._",
                        "<!-- END AUTO-GENERATED: WORKFLOW INVENTORY -->",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            workflow_dir = tmp_path / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "nightly-agents-refresh.yml").write_text(
                "\n".join(
                    [
                        "name: Nightly Agents Refresh",
                        "on:",
                        "  workflow_dispatch:",
                        "  schedule:",
                        "    - cron: \"20 11 * * 1-5\"",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            audit_path = tmp_path / "repo_workflow_audit.md"
            audit_path.write_text(
                "\n".join(
                    [
                        "| File | Workflow Name | Triggers | Schedule Detail | Main Jobs | Email-Capable |",
                        "|---|---|---|---|---|---|",
                        "| `.github/workflows/daily-alpaca-paper.yml` | `Daily Alpaca Paper Run` | `workflow_dispatch`, `schedule` | `9:35 AM ET weekdays` | `engine_run`, `execute_orders`, `email` | Yes |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            findings_dir = tmp_path / "reports" / "agents"
            findings_dir.mkdir(parents=True)
            (findings_dir / "nightly_findings.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-08T11:00:00Z",
                        "headline": "Risk posture remains elevated",
                        "summary": ["Breadth improved but remains below neutral threshold."],
                        "actions": ["Review next post-trade reconciliation output."],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--agents",
                    str(agents_path),
                    "--workflow-dir",
                    str(workflow_dir),
                    "--audit",
                    str(audit_path),
                    "--report-dir",
                    str(findings_dir),
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            rendered = agents_path.read_text(encoding="utf-8")
            self.assertIn("Risk posture remains elevated", rendered)
            self.assertIn("Review next post-trade reconciliation output.", rendered)
            self.assertIn("`nightly-agents-refresh.yml`", rendered)
            self.assertIn("`daily-alpaca-paper.yml`", rendered)


if __name__ == "__main__":
    unittest.main()
