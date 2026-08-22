# Precompute Semantic Validation

## Purpose

FR-016 defines the advisory validation boundary for precompute bundle semantics
beyond file existence, JSON parseability, and trade-date matching.

This document governs the advisory diagnostic in
`scripts.research.check_precompute_semantic_validation`. The diagnostic does
not change `cron_precompute.sh`, `cron_execute.sh`,
`core/precompute_bundle_validation.py`, execution gating, self-heal behavior,
broker behavior, strategy logic, accounting semantics, or promotion logic.

## Current Validation Baseline

The deployed bundle validator currently checks:

- required files exist;
- required files are valid JSON;
- required file `trade_date` fields match the expected date when present;
- validation failures are persisted to workflow status artifacts;
- execution self-heal remains fail-closed when the bundle is incomplete.

Required precompute files:

- `contract.json`
- `daily_snapshot.json`
- `signals.json`
- `planned_execution_payload.json`

This is execution-integrity validation. It should remain blocking where already
deployed.

## Advisory Semantic Validation Scope

Future FR-016 implementation may add read-only semantic checks that inspect a
complete bundle and report operator-facing warnings. These checks should be
advisory until separately governed.

Candidate semantic checks:

| Domain | Advisory Check | Operator Meaning |
|---|---|---|
| Contract identity | Bundle files agree on trade date, run id, workflow kind, and strategy name where present. | Detect mixed-date or mixed-run evidence. |
| Planned orders | Planned execution payload has explicit action, symbol, quantity, and side fields for every order. | Detect malformed execution intent before operator review. |
| Position intent | Sell quantities do not exceed known planned/current position evidence when that evidence is available. | Surface potential pre-execution inconsistency without changing broker behavior. |
| Cash intent | Buy notional and cash assumptions are explicit when present. | Highlight incomplete buying-power context. |
| Strategy surface | The precompute/PAPER surface uses Orion; Shadow continues to model Polaris, Orion, and Lyra. The separate Lyra Live portfolio has its own authority and artifacts. | Prevent accidental cross-lane blending while preserving the actual multi-lane operating state. |
| Suppressed effects | Self-heal artifacts explicitly report suppressed email, shadow, latest, and reconciliation side effects. | Preserve degraded-state visibility. |
| Provenance | Bundle artifacts identify producer or source path where available. | Improve auditability and replay review. |

## Severity Vocabulary

Use a small advisory vocabulary:

| Severity | Meaning | Runtime Effect |
|---|---|---|
| `INFO` | Useful operator context. | No gating. |
| `WARN` | Semantics are incomplete or ambiguous but not proven unsafe. | No gating unless future governance promotes the check. |
| `FAIL_ADVISORY` | Evidence strongly indicates semantic inconsistency. | Report only in Phase A; future promotion requires FR review. |
| `NOT_ASSESSABLE` | Required evidence for the semantic check is missing. | Do not infer healthy state. |

Phase A semantic validation must not introduce new execution gates. Existing
bundle validation remains the only deployed blocking precompute validation.

## Output Shape For Future Implementation

The read-only diagnostic can print this advisory shape:

```json
{
  "schema_version": 1,
  "validation_scope": "precompute_semantic_advisory",
  "trade_date": "YYYY-MM-DD",
  "bundle_dir": "outputs/precompute/YYYY-MM-DD",
  "status": "WARN",
  "blocking": false,
  "checks": [
    {
      "name": "strategy_surface",
      "severity": "INFO",
      "status": "OK",
      "message": "Paper strategy remains Caerus Polaris."
    }
  ],
  "runtime_effect": "none"
}
```

Potential future persisted path:

- `outputs/workflow/<DATE>/precompute_semantic_validation.json`

This path is proposed only. The current diagnostic writes nothing unless an
operator redirects stdout.

## Diagnostic Command

```text
python3 -m scripts.research.check_precompute_semantic_validation \
  --bundle-dir outputs/precompute/YYYY-MM-DD \
  --trade-date YYYY-MM-DD \
  --markdown
```

Use `--json` for structured review and `--strict` when a nonzero exit is useful
for manual validation. Strict mode remains advisory; it is not wired into cron
or execution.

## Operator Guidance

Operators should continue to treat existing
`precompute_bundle_validation.json` and `execution_bundle_validation.json` as
the authoritative pre-execution integrity evidence.

When advisory semantic validation exists in a future phase:

- read it as interpretation support;
- do not override existing fail-closed bundle validation;
- do not use it to force execution;
- escalate repeated `WARN`, `FAIL_ADVISORY`, or `NOT_ASSESSABLE` results for
  governance review.

## Promotion Requirements

Before any semantic check becomes blocking, Caerus should require:

- bounded tests with synthetic bundles;
- historical dry-run review across prior precompute bundles;
- explicit false-positive review;
- rollback plan to advisory-only behavior;
- Friday governance review if execution behavior would change.

Until then, FR-016 remains advisory/read-only.
