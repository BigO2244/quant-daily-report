# AIOPS Operating Charter (v0.1)

## Purpose

This document defines the persistent operating contract for the AIOPS
system inside `quant-daily-report-main`.

It exists to ensure that: - We consistently use
`aiops run-all --spec <spec> --mode <MODE>` as the canonical lifecycle
command. - We preserve determinism, auditability, and capital protection
discipline. - Future sessions with AI assistants reference this document
as the authoritative workflow guide.

------------------------------------------------------------------------

# Canonical Lifecycle Command

## Always Prefer

    aiops run-all --spec <spec_path> --mode <MODE>

This command orchestrates:

1.  parse
2.  plan
3.  dispatch (Codex runner if available; fallback otherwise)
4.  run
5.  verify

This is the default and required workflow for all governed changes.

Manual parse/plan/dispatch sequences are discouraged unless debugging.

------------------------------------------------------------------------

# System Invariants (Non-Negotiable)

1.  No trading logic mutations outside governed specs.

2.  No external model API calls from Python code.

3.  Deterministic outputs (no randomness; no timestamps beyond RUN_ID).

4.  Minimal stdout (machine-parseable).

5.  All runs produce deterministic artifacts under:

    reports/ai_runs/`<RUN_ID>`{=html}/

Required artifacts: - plan.md - spec_snapshot.md - run_all_summary.md -
codex_task.txt (fallback only)

------------------------------------------------------------------------

# Exit Code Contract (run-all)

0 = OK\
2 = NEEDS_OPERATOR\
3 = VERIFY_FAILED\
4 = PARSE_OR_PLAN_FAILED\
5 = DISPATCH_FAILED\
6 = RUN_FAILED

Exit codes are stable and enforced by contract tests.

------------------------------------------------------------------------

# Operating Discipline

For any meaningful change:

1.  Write or modify a spec in `specs/`
2.  Execute: aiops run-all --spec `<spec>`{=html} --mode BUILD \| HARDEN
    \| EXPLORE
3.  Review:
    -   run_all_summary.md
    -   plan.md
    -   spec_snapshot.md
    -   git diff
    -   pytest results

Do not bypass this loop for production-intent changes.

------------------------------------------------------------------------

# Capital Protection Principle (Future Tier C)

Before any strategy-impacting change is considered for live capital:

-   verify must pass
-   pytest must pass
-   evidence artifacts must exist
-   run directory must be reproducible
-   diff must be reviewable

AIOPS is the governance layer, not the trading engine.

------------------------------------------------------------------------

# Assistant Memory Directive

Future AI sessions should treat this file as authoritative reference
for:

-   How AIOPS operates
-   What `run-all` means
-   What invariants must never be broken
-   How strategy changes are governed

If uncertain, default to this contract.

------------------------------------------------------------------------

# Status

Version: v0.1\
Created: 2026-03-01T22:48:23.582224Z\
Repository: quant-daily-report-main\
Branch: feature/aiops-orchestration

This document should evolve only through governed AIOPS specs.
