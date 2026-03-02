"""Spec file linting tests to validate section extraction."""

import re
from pathlib import Path


def extract_section(spec_text: str, section_name: str) -> str:
    """Extract body under a level-2 markdown heading (matches aiops.plan._extract_section)."""
    _SECTION_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$")
    
    lines = spec_text.splitlines()
    start_idx = None

    for idx, line in enumerate(lines):
        match = _SECTION_HEADING_PATTERN.match(line.strip())
        if match and match.group(1).strip() == section_name:
            start_idx = idx + 1
            break

    if start_idx is None:
        return ""

    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        match = _SECTION_HEADING_PATTERN.match(lines[idx].strip())
        if match:
            end_idx = idx
            break

    section_lines = lines[start_idx:end_idx]
    while section_lines and not section_lines[0].strip():
        section_lines.pop(0)
    while section_lines and not section_lines[-1].strip():
        section_lines.pop()
    return "\n".join(section_lines)


def test_daily_alpaca_paper_spec_files_section():
    """Test that daily_alpaca_paper_run_0935_et.md has non-empty FILES section."""
    spec_path = Path("specs/daily_alpaca_paper_run_0935_et.md")
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    
    spec_text = spec_path.read_text(encoding="utf-8")
    files_section = extract_section(spec_text, "FILES")
    
    assert files_section.strip(), "FILES section is empty"
    assert ".github/workflows/daily_quant_report_premarket.yml" in files_section
    assert ".github/workflows/alpaca_paper_execute_open.yml" in files_section
    assert "specs/daily_alpaca_paper_run_0935_et.md" in files_section
    print(f"✓ FILES section extracted:\n{files_section}")


def test_daily_alpaca_paper_spec_acceptance_criteria_section():
    """Test that daily_alpaca_paper_run_0935_et.md has non-empty ACCEPTANCE CRITERIA section."""
    spec_path = Path("specs/daily_alpaca_paper_run_0935_et.md")
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    
    spec_text = spec_path.read_text(encoding="utf-8")
    criteria_section = extract_section(spec_text, "ACCEPTANCE CRITERIA")
    
    assert criteria_section.strip(), "ACCEPTANCE CRITERIA section is empty"
    # Check all 5 criteria are present
    assert "Pre-market report runs on trading days" in criteria_section
    assert "Execution runs on trading days at 09:35 ET" in criteria_section
    assert "Both jobs implement trading-day guard" in criteria_section
    assert "Execution is idempotent per REPORT_DATE" in criteria_section
    assert "Each run writes RUN_ID archive artifacts" in criteria_section
    print(f"✓ ACCEPTANCE CRITERIA section extracted:\n{criteria_section}")


def test_spec_mode_and_objective_headers():
    """Test that spec has required MODE and OBJECTIVE headers."""
    spec_path = Path("specs/daily_alpaca_paper_run_0935_et.md")
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    
    spec_text = spec_path.read_text(encoding="utf-8")
    
    # Check for required headers (MODE, PROJECT_TYPE, RISK_TIER, OBJECTIVE)
    assert "MODE:" in spec_text, "MODE header not found"
    assert "HARDEN" in spec_text[:spec_text.find("##")], "MODE: HARDEN not in header section"
    
    assert "OBJECTIVE:" in spec_text, "OBJECTIVE header not found"
    assert "Auto-run Alpaca paper execution" in spec_text, "OBJECTIVE description not found"
    
    assert "PROJECT_TYPE:" in spec_text, "PROJECT_TYPE header not found"
    assert "RISK_TIER:" in spec_text, "RISK_TIER header not found"
    
    print(f"✓ All required headers present (MODE, PROJECT_TYPE, RISK_TIER, OBJECTIVE)")


if __name__ == "__main__":
    import sys
    try:
        test_spec_mode_and_objective_headers()
        test_daily_alpaca_paper_spec_files_section()
        test_daily_alpaca_paper_spec_acceptance_criteria_section()
        print("\n✓ All spec lint tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ Spec lint test failed: {e}", file=sys.stderr)
        sys.exit(1)
