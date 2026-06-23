from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "build_nightly_findings.py"


class BuildNightlyFindingsTest(unittest.TestCase):
    def test_build_nightly_findings_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dashboard_dir = tmp_path / "web" / "dashboard"
            dashboard_dir.mkdir(parents=True)
            reports_dir = tmp_path / "reports" / "agents"
            reports_dir.mkdir(parents=True)

            (dashboard_dir / "dashboard_data.json").write_text(
                json.dumps(
                    {
                        "tradeDate": "2026-04-08",
                        "runId": "run-123",
                        "runRoot": "/tmp/run-123",
                        "broker": {
                            "authoritativeState": True,
                            "trustLevel": "HIGH",
                            "pretrade": {
                                "snapshotOk": True,
                                "status": "READY",
                                "positionsCount": 2,
                                "warningFlags": ["pdt_watch"],
                            },
                            "posttrade": {
                                "snapshotOk": True,
                                "reconStatus": "PASS",
                                "positionsCount": 3,
                                "repairSuggestions": ["review drift report"],
                                "affectedSymbols": ["NVDA"],
                            },
                            "delta": {
                                "positionsCount": 1,
                                "cash": -100.25,
                                "equity": 50.0,
                            },
                            "paths": {
                                "reconPosttrade": "/tmp/run-123/broker/recon_posttrade.json",
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "repo_workflow_audit.md").write_text(
                "\n".join(
                    [
                        "| File | Workflow Name | Triggers | Schedule Detail | Main Jobs | Email-Capable |",
                        "|---|---|---|---|---|---|",
                        "| `.github/workflows/research-digest.yml` | `Research Digest — Nightly` | `workflow_dispatch`, `schedule` | `0 12 * * 1-5`, `0 11 * * 1-5` => both weekday entries target `7:00 AM ET` across EST/EDT | `digest` | Yes |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--audit",
                    "repo_workflow_audit.md",
                    "--output-json",
                    "reports/agents/nightly_findings.json",
                    "--output-md",
                    "reports/agents/nightly_findings.md",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            findings = json.loads((reports_dir / "nightly_findings.json").read_text(encoding="utf-8"))
            findings_md = (reports_dir / "nightly_findings.md").read_text(encoding="utf-8")

            self.assertEqual(findings["headline"], "Broker-authoritative state confirmed")
            self.assertIn("Trade date: 2026-04-08.", findings["summary"])
            self.assertIn("Broker preflight warning flag present: pdt_watch.", findings["risks"])
            self.assertIn("review drift report", findings["actions"])
            self.assertIn("Nightly digest schedule from audit", findings_md)


if __name__ == "__main__":
    unittest.main()
