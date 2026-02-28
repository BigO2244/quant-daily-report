# SPEC — AIOPS Orchestration Layer

## OBJECTIVE
Introduce deterministic orchestration to eliminate human relay between ChatGPT (spec author) and Codex (implementer).

## MODE
BUILD

## FILES

create:
- none

modify:
- aiops/cli.py

## ACCEPTANCE CRITERIA

- New commands added:
  - aiops plan <spec_path> --mode <MODE>
  - aiops dispatch --run <RUN_ID>
  - aiops run <spec_path> --mode <MODE>
- Deterministic behavior
- No trading logic mutation
- Plan hash preserved
- Verify runs automatically after dispatch
- Exit codes propagate correctly

## INVARIANTS

- No secrets printed
- No file writes outside run directory except Codex changes
- No randomness
- Stable CLI output