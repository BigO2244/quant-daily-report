# Shadow Learning Health

## Purpose

FR-014 adds a read-only diagnostic surface for shadow learning artifacts.

The diagnostic summarizes whether weekly learning interpretation has enough
shadow evidence to be useful. It does not alter feedback-loop generation,
learning logic, shadow artifacts, hydration, broker behavior, execution,
promotion logic, accounting semantics, or timing semantics.

## Diagnostic Command

```text
python3 -m scripts.research.check_shadow_learning_health --latest --markdown
python3 -m scripts.research.check_shadow_learning_health --trade-date YYYY-MM-DD --json
python3 -m scripts.research.check_shadow_learning_health --trade-date YYYY-MM-DD --strict
```

`--strict` exits nonzero unless learning health is `READY`.

## Health States

| State | Meaning | Operator Interpretation |
|---|---|---|
| `READY` | Required shadow learning inputs are present and not stale. | Weekly learning review can proceed within normal confidence caveats. |
| `INCOMPLETE` | Required inputs are missing, stale, or no-data. | Do not use weekly learning interpretation until source artifacts refresh. |
| `UNKNOWN` | The diagnostic cannot resolve a trade date or source state. | Inspect `outputs/shadow_candidates/` and source-readiness diagnostics. |

## Inputs

The diagnostic reads existing artifacts only:

- `outputs/shadow_candidates/<DATE>/shadow_evaluation.json`
- `outputs/shadow_candidates/<DATE>/comparison.json`
- `outputs/shadow_candidates/<DATE>/feedback_loop_summary.json`
- per-strategy feedback artifacts under `polaris/`, `orion/`, and `lyra/`
- hydration status indirectly through the portfolio learning report

It does not write reports or repair missing artifacts.

## Operator Guidance

Use this diagnostic before treating weekly learning evidence as reliable.

Escalate or wait when:

- required artifacts are missing;
- source artifacts are stale after post-close hydration should have completed;
- learning readiness is LOW for a strategy;
- watch items indicate hydration did not run, hydration failed, or provider data
  lag remains unresolved.

LOW learning readiness is not a promotion blocker by itself because FR-014 is
advisory. It is a research interpretation caveat.

## Relationship To FR-030

FR-030 explains daily research interpretation. FR-014 learning health explains
whether weekly learning evidence is complete enough to support synthesis.

Both remain read-only telemetry. Neither changes strategy behavior, broker
state, promotion logic, or execution.
