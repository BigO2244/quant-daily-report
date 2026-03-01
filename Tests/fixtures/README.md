# Test Fixtures & Golden Tests

**Purpose**: Stable, deterministic test inputs for validating AIOPS contract compliance.

## Directory Structure

```
tests/fixtures/
├── README.md                          (this file)
├── specs/
│   ├── explore_mode_spec.md           (EXPLORE mode baseline)
│   ├── build_mode_spec.md             (BUILD mode baseline)
│   ├── harden_mode_spec.md            (HARDEN mode baseline)
│   └── invalid_spec_missing_mode.md   (Negative test: missing MODE header)
├── golden/
│   ├── plan_output_build_mode.txt     (Expected plan.md content for BUILD spec)
│   ├── run_all_summary_build_mode.md  (Expected run_all_summary.md for BUILD spec)
│   └── cli_stdout_4lines.txt          (Expected 4-line plan stdout)
└── codex_tasks/
    ├── task_1_simple.txt              (Simple codex task for testing)
    └── task_2_complex.txt             (Complex task with special chars)
```

## Fixture Specs

### `explore_mode_spec.md`

**Purpose**: Minimal valid spec for EXPLORE mode (lightest verification).

**Headers**:
```
MODE: EXPLORE
PROJECT_TYPE: quant-research
RISK_TIER: low
OBJECTIVE: Test basic EXPLORE mode parsing
```

**Use Cases**:
- test_parse_handles_explore_mode ✓
- test_verify_explore_mode_passes_light_checks ✓

**Determinism**: Yes (no timestamps, deterministic section extraction)

---

### `build_mode_spec.md`

**Purpose**: Standard spec for BUILD mode (medium verification).

**Headers**:
```
MODE: BUILD
PROJECT_TYPE: quant-research
RISK_TIER: medium
OBJECTIVE: Build complete AIOPS contract validation suite
```

**Sections**:
- FILES (create: plan.py, run_all.py; modify: cli.py)
- ACCEPTANCE CRITERIA (deterministic CLI contracts, exit codes, golden tests)

**Use Cases**:
- test_plan_stdout_4lines ✓
- test_plan_deterministic_on_rebuild ✓
- test_run_all_success_path ✓
- test_run_all_summary_stable_ordering ✓

**Determinism**: Yes (static sections, no timestamps)

---

### `harden_mode_spec.md`

**Purpose**: Strict spec for HARDEN mode (full verification).

**Headers**:
```
MODE: HARDEN
PROJECT_TYPE: quant-research
RISK_TIER: high
OBJECTIVE: Harden AIOPS lifecycle with contract tests and deterministic outputs
```

**Sections**:
- FILES (create multiple test files, update docs, modify core)
- ACCEPTANCE CRITERIA (contracts, CI gates, no secrets, determinism)

**Use Cases**:
- test_verify_harden_mode_enforces_contracts ✓
- test_run_all_harden_mode_full_lifecycle ✓
- test_no_secrets_in_summary ✓

**Determinism**: Yes (all sections static)

---

### `invalid_spec_missing_mode.md`

**Purpose**: Negative test fixture (deliberately invalid).

**Structure**:
```
OBJECTIVE: Test error handling
PROJECT_TYPE: quant-research
RISK_TIER: low
(missing MODE header)
```

**Use Cases**:
- test_parse_fails_on_missing_mode ✓
- test_run_all_exits_4_on_parse_failure ✓

**Expected Behavior**: Exit code 1 (ValidationError)

---

## Golden Outputs

### `plan_output_build_mode.txt`

**Purpose**: Canonical plan.md content for build_mode_spec.md.

**Format**:
```
PLAN_VERSION: 1
SPEC_PATH: tests/fixtures/specs/build_mode_spec.md
MODE: BUILD
SPEC_HASH: <deterministic sha256>

FILES:
create:\n- aiops/plan.py\n- aiops/run_all.py\n\nmodify:\n- aiops/cli.py

ACCEPTANCE_CRITERIA:
- Deterministic CLI contracts
- Exit codes and output formats stable
- Golden tests enforce contracts

PLAN_HASH: <deterministic sha256>
```

**How to Generate**:
```bash
# Generate and capture plan content
RUN_ID=$(aiops plan --spec tests/fixtures/specs/build_mode_spec.md --mode BUILD | grep RUN_ID | cut -d' ' -f2)
cat reports/ai_runs/$RUN_ID/plan.md > tests/fixtures/golden/plan_output_build_mode.txt

# Verify stable (run again)
RUN_ID=$(aiops plan --spec tests/fixtures/specs/build_mode_spec.md --mode BUILD | grep RUN_ID | cut -d' ' -f2)
diff <(cat reports/ai_runs/$RUN_ID/plan.md) tests/fixtures/golden/plan_output_build_mode.txt
```

**Usage in Tests**:
```python
def test_plan_matches_golden_build_mode(tmp_path, monkeypatch):
    spec_path = Path("tests/fixtures/specs/build_mode_spec.md")
    exit_code, run_id = run_plan(spec_path, mode_override="BUILD")
    
    plan_content = (Path.cwd() / "reports" / "ai_runs" / run_id / "plan.md").read_text()
    golden = Path("tests/fixtures/golden/plan_output_build_mode.txt").read_text()
    
    assert exit_code == 0
    assert plan_content == golden  # Exact match (deterministic)
```

---

### `run_all_summary_build_mode.md`

**Purpose**: Canonical run_all_summary.md output for full successful build lifecycle.

**Format**:
```markdown
# AIOps Run-All Summary

## Inputs
- RUN_ID: 20260301_143022_abc1234
- SPEC_PATH: tests/fixtures/specs/build_mode_spec.md
- MODE: BUILD

## Stage Results
| Stage | Exit Code |
|---|---|
| parse | 0 |
| plan | 0 |
| dispatch | 0 |
| run | 0 |
| verify | 0 |

## Final
- RUN_ALL_STATUS: SUCCESS
- EXIT_CODE: 0

```

**Constraints** (to enable golden-test matching):
- No actual datetime in summary (timestamp in RUN_ID only)
- Stage results in fixed order: parse, plan, dispatch, run, verify
- RUN_ID deterministic within test (mocked now_local() + fixed git SHA)

**Usage in Tests**:
```python
def test_run_all_summary_matches_golden_build_mode(tmp_path, monkeypatch):
    # Mock timestamp and git metadata for deterministic RUN_ID
    monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 14, 30, 22, tz...))
    monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "abc1234"})
    monkeypatch.setattr("aiops.dispatch.run_dispatch", lambda ...: 0)  # Successful dispatch
    
    exit_code = run_all(Path("tests/fixtures/specs/build_mode_spec.md"), mode_override="BUILD")
    
    run_id = "20260301_143022_abc1234"
    summary_path = Path.cwd() / "reports" / "ai_runs" / run_id / "run_all_summary.md"
    summary_content = summary_path.read_text()
    golden = Path("tests/fixtures/golden/run_all_summary_build_mode.md").read_text()
    
    assert exit_code == 0
    assert summary_content == golden  # Exact match
```

---

### `cli_stdout_4lines.txt`

**Purpose**: Expected stdout from `aiops plan` command (exactly 4 lines).

**Format** (one per line):
```
RUN_ID: 20260301_143022_abc1234
RUN_DIR: /absolute/path/to/reports/ai_runs/20260301_143022_abc1234
PLAN_PATH: /absolute/path/to/reports/ai_runs/20260301_143022_abc1234/plan.md
SPEC_SNAPSHOT_PATH: /absolute/path/to/reports/ai_runs/20260301_143022_abc1234/spec_snapshot.md
```

**Validation**:
```python
def test_plan_cli_stdout_exactly_4_lines(capsys):
    # Run plan command via CLI
    exit_code = main(["plan", "--spec", "tests/fixtures/specs/build_mode_spec.md", "--mode", "BUILD"])
    captured = capsys.readouterr()
    
    lines = captured.out.strip().split('\n')
    
    assert exit_code == 0
    assert len(lines) == 4
    assert lines[0].startswith("RUN_ID: ")
    assert lines[1].startswith("RUN_DIR: ")
    assert lines[2].startswith("PLAN_PATH: ")
    assert lines[3].startswith("SPEC_SNAPSHOT_PATH: ")
```

---

## Codex Task Fixtures

### `task_1_simple.txt`

**Purpose**: Simple task for testing dispatch execution.

**Content**:
```
Perform basic analysis:
1. Count lines in input file
2. Report count to output
```

**Use Case**:
- test_dispatch_executes_simple_codex_task ✓

---

### `task_2_complex.txt`

**Purpose**: Complex task with special characters for edge case testing.

**Content**:
```
Execute analysis:
- Input: "data with 'quotes' and \"escapes\""
- Calculate: sum($x^2) for all x
- Output: {"status": "ok", "value": 42}
- Escape sequences: \n \t \\ \" \'
```

**Use Case**:
- test_dispatch_handles_special_chars_in_task ✓

---

## Creating New Fixtures

### Procedure: Add a New Golden Test

1. **Create spec fixture** in `tests/fixtures/specs/`:
   ```bash
   cat > tests/fixtures/specs/custom_spec.md << 'EOF'
   MODE: BUILD
   OBJECTIVE: Custom test objective
   ...
   EOF
   ```

2. **Generate golden output**:
   ```bash
   # Run aiops and capture output
   EXIT_CODE=$(aiops plan --spec tests/fixtures/specs/custom_spec.md 2>&1 | head -4 > /tmp/out.txt && echo $?)
   
   # Store in golden directory
   cp /tmp/out.txt tests/fixtures/golden/custom_output.txt
   ```

3. **Write test** in `tests/test_aiops_contracts.py`:
   ```python
   def test_custom_spec_golden_match(monkeypatch, tmp_path):
       # Mock time and git for determinism
       monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(...))
       monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "abc1234"})
       
       exit_code, run_id = run_plan(Path("tests/fixtures/specs/custom_spec.md"))
       
       with open("tests/fixtures/golden/custom_output.txt") as f:
           golden = f.read()
       
       actual = (Path.cwd() / "reports" / "ai_runs" / run_id / "plan.md").read_text()
       
       assert actual == golden
   ```

---

## Running Golden Tests

```bash
# Run all contract tests (includes golden tests)
pytest tests/test_aiops_contracts.py -v

# Run only golden tests
pytest tests/test_aiops_contracts.py -k "golden" -v

# Run specific golden test
pytest tests/test_aiops_contracts.py::test_plan_matches_golden_build_mode -v

# Run and show diff on failure
pytest tests/test_aiops_contracts.py --tb=short
```

---

## Maintaining Fixtures

### When to Update Fixtures

✓ **Update golden output when**:
- Plan rendering changes intentionally (e.g., new PLAN_VERSION)
- File sections reordered for better clarity (but keep ordering stable)

✗ **Never update golden output when**:
- Test failures suggest fixture outdated without code changes
- Trying to "fix" test by changing golden (reverse: fix code to match golden)

### Validation Checklist

Before committing new/updated fixtures:

```bash
# 1. Verify fixtures are valid specs
aiops parse tests/fixtures/specs/*.md

# 2. Verify golden outputs match actual execution
for spec in tests/fixtures/specs/*.md; do
  aiops plan --spec $spec 2>&1 | head -10
done

# 3. Run all contract tests
pytest tests/test_aiops_contracts.py -v

# 4. Run full test suite (no regressions)
pytest -q

# 5. Commit with message citing fixture change
git add tests/fixtures/
git commit -m "Update golden fixtures for plan rendering v0.2"
```

---

## References

- [System Contract v0.1](../../specs/aiops_system_contract_v0_1.md) — Full contract specification
- [AIOPS Workflow Guide](../../docs/aiops_workflow.md) — Operator documentation
- [test_aiops_contracts.py](../test_aiops_contracts.py) — Contract test suite using these fixtures

