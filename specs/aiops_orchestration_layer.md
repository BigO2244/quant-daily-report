# SPEC — AIOPS Orchestration Layer

## PURPOSE
Introduce deterministic orchestration to eliminate human relay between ChatGPT (spec author) and Codex (implementer).

## MODE COMPATIBILITY
EXPLORE: Not applicable
BUILD: Required
HARDEN: Extend only

---

## COMMANDS TO ADD

1. aiops dispatch --run <RUN_ID>
2. aiops run --spec <SPEC_PATH> --mode <MODE>

---

## BEHAVIOR

aiops dispatch:

1. Read plan.md from /reports/ai_runs/<RUN_ID>/
2. Validate plan hash exists
3. Invoke Codex CLI with:
   - Plan path
   - Mode
4. After Codex completes:
   - Execute aiops verify --run <RUN_ID>
5. Exit with non-zero code if verify fails

aiops run:

1. Execute parse
2. Execute plan
3. Execute dispatch

---

## FILES

modify:
- aiops CLI entrypoint file

create:
- none

---

## ACCEPTANCE CRITERIA

- Deterministic behavior
- No trading logic mutation
- No external model calls
- Plan hash preserved
- Verify runs automatically
- Exit codes propagate correctly

---

## INVARIANTS

- No secrets printed
- No file writes outside run directory except Codex changes
- No randomness
- No dynamic path discovery
- Stable CLI output formatting

---

## VERIFICATION GATES

Gate 1: Existing tests pass
Gate 2: Orchestration commands function end-to-end
Gate 3: Failure in verify halts pipeline