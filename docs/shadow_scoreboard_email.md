# Shadow Scoreboard Email

## Purpose

The trading confirmation email includes a read-only Shadow Strategy Snapshot below the Polaris/SPY performance section. The section gives the operator a daily diagnostic view of Polaris, Orion, and Lyra without changing execution behavior.

## Source Artifacts

For `YYYY-MM-DD`, the scoreboard reads:

- `outputs/shadow_candidates/YYYY-MM-DD/shadow_evaluation.json`
- `outputs/shadow_candidates/YYYY-MM-DD/comparison.json`
- `outputs/shadow_candidates/YYYY-MM-DD/feedback_loop_summary.json`

The email renderer does not submit orders, alter execution status, or write strategy state.

## Fallback Behavior

If required shadow artifacts are unavailable, the email still renders the section:

```text
--- Shadow Strategy Snapshot ---
Shadow snapshot unavailable: <reason>
```

The section is never suppressed silently.

## Sample Section

```text
--- Shadow Strategy Snapshot ---
Diagnostic only; no capital-allocation or strategy-change instruction is implied.

Polaris:
Today: +0.68%
Since inception: +3.47%
vs SPY: -7.65%
Turnover: 12.00%
Top-3 concentration: 42.00%
Constituent changes: 1
Learning readiness: MEDIUM
Diagnostic state: building evidence
```

Orion and Lyra also show `vs Polaris`.

## Diagnostic Language Constraints

The section is factual and CIO/operator-style. It may use phrases such as:

- building evidence
- stable
- insufficient history
- data unavailable
- risk watch
- learning gap

It must not recommend promotion, replacement, or capital deployment.
