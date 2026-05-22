# Research Clarity Wave

## Purpose

This document defines the additive FR-024 through FR-027 research clarity bundle.
The bundle is designed to improve operator interpretation speed without changing
execution behavior, accounting semantics, broker behavior, cron behavior,
dashboard behavior, or promotion logic.

FR-028 timing semantics remain out of scope. Operational shadow NAV remains LOW
confidence until that governed accounting review is completed.

## Artifact Location

Generated artifacts are written only when the builder is run explicitly:

```text
outputs/research_clarity/<TRADE_DATE>/
```

The current builder is:

```text
scripts/research/build_research_clarity_wave.py
```

It reads dated shadow candidate artifacts and writes immutable generated
research outputs. Source artifacts are read-only inputs.

## FR-024 NAV Surface Provenance

`nav_surface_registry.json` and `surface_metadata.json` classify reported
performance evidence by truth surface:

- `LIVE_BROKER_PAPER_NAV`: broker-authoritative when reconciled.
- `OPERATIONAL_SHADOW_NAV`: model portfolio interpretation, LOW confidence
  until FR-028 is governed.
- `RESEARCH_BACKTEST_NAV`: research/backtest surface with synthetic execution
  assumptions.
- `CONVENIENCE_LATEST_PUBLICATION`: latest-style pointer, not canonical evidence
  without source verification.

These classifications prevent broker NAV, shadow NAV, research backtest NAV, and
latest publications from being blended into one performance claim.

## FR-025 Immutable Portfolio Memory

`holdings_snapshot.json`, `weights_snapshot.json`, `exposures_snapshot.json`,
`rebalance_delta.json`, and `manifest.json` establish dated evidence for future
attribution and replay.

The generated files are immutable on write. If an existing artifact would be
rewritten with different content, the builder fails instead of silently changing
history.

## FR-026 Exposure Intelligence

`exposure_summary.json`, `factor_risk_flags.json`,
`concentration_monitor.json`, and `exposure_drift_summary.json` surface:

- position concentration;
- sector concentration;
- top-three concentration;
- turnover proxy;
- momentum sensitivity proxy;
- missing liquidity and volatility source caveats;
- single-date drift baseline status.

These outputs are advisory telemetry. They do not gate execution or change
strategy behavior.

## FR-027 Regime Fragility Intelligence

`regime_performance_breakdown.json`, `regime_fragility_report.json`,
`regime_exposure_matrix.json`, and `attribution_by_regime.json` connect strategy
performance, concentration, and exposure hints to available regime evidence.

If the source artifact lacks regime metadata, regime confidence is explicitly
low/unknown rather than inferred from filesystem paths or later information.

## Operator Interpretation

Use the bundle to answer:

- Which truth surface produced this performance number?
- Which strategy is concentrated by position or sector?
- Which exposure caveats should lower confidence?
- Which regime evidence is present, missing, or advisory?
- Which generated files are immutable evidence versus convenience reports?

Do not use the bundle to:

- migrate accounting semantics;
- rewrite NAV chains;
- promote Orion or Lyra;
- reinterpret historical reports as timing-corrected;
- substitute shadow NAV for broker NAV.

## MCP Compatibility

Future MCP or research retrieval layers should consume this bundle as local,
read-only, provenance-aware research evidence. Retrieval layers should preserve:

- truth-surface classification;
- confidence classification;
- immutable content hashes;
- trade-date boundaries;
- LOW confidence caveats for operational shadow NAV;
- generated-vs-canonical distinctions from `docs/documentation_taxonomy.md`.

MCP transport, agents, autonomous reasoning, and orchestration are not part of
this wave.
