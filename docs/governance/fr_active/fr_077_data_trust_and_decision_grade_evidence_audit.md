# FR-077 Data Trust And Decision-Grade Evidence Audit

Status: DRAFT_RESEARCH_REVIEW  
Date: 2026-06-19  
Scope: Research/governance only. No trading, execution, strategy, allocation, broker, or promotion behavior changed.

## Objective

FR-077 inventories Caerus data trust, historical-result validity, execution evidence coverage, sleeve promotion evidence, and falsification risks. The goal is to prevent research, reliability, or promotion surfaces from implying decision-grade confidence where lineage or artifact coverage does not support it.

## Output Artifacts

- `outputs/research/data_trust_audit/data_lineage_inventory.json`
- `outputs/research/data_trust_audit/data_lineage_inventory.md`
- `outputs/research/data_trust_audit/historical_results_trust_review.json`
- `outputs/research/data_trust_audit/historical_results_trust_review.md`
- `outputs/research/data_trust_audit/execution_evidence_coverage.json`
- `outputs/research/data_trust_audit/execution_evidence_coverage.md`
- `outputs/research/data_trust_audit/sleeve_promotion_evidence_review.json`
- `outputs/research/data_trust_audit/sleeve_promotion_evidence_review.md`
- `outputs/research/data_trust_audit/falsification_review.md`

## Executive Findings

1. `TRUSTED` is narrow. PIT liquidity/ADV is the strongest current source, but trust applies to liquidity/capacity joins, not all downstream claims.
2. Most data sources are `PARTIAL`: price, benchmark, security-master, CIK/ticker, EDGAR 13D, broker snapshots, and sleeve/shadow evidence all need use-case-specific caveats.
3. Execution evidence is `NOT_DECISION_GRADE` locally. There are 33 run directories, 0 decision-grade execution bundles, 0 in-run posttrade recon artifacts, 0 in-run target-attainment artifacts, 0 integrity artifacts, and 0 reliability reports.
4. FR-074 replay GREEN is not proof of clean operations. It is classifier telemetry over LOW evidence coverage.
5. Historical performance claims are partially evidence-backed as model/backtest or shadow-observation metrics, not as decision-grade live-paper alpha.
6. No sleeve is decision-grade for pilot capital today. Orion/Lyra redundancy triage is the strongest actionable research conclusion.

## Trust Taxonomy

| Classification | Meaning | Allowed Use |
|---|---|---|
| `TRUSTED` | Source is strong for a narrow, stated purpose with manifest or clear PIT lineage. | Decision support only inside that purpose and with explicit joins. |
| `PARTIAL` | Source is useful but has staleness, missingness, survivorship, or convention risk. | Research and diagnostics with caveats. |
| `LOW_COVERAGE` | Source mechanics work, but sample/date/artifact coverage is too thin. | Pilot artifact or builder validation only. |
| `NOT_DECISION_GRADE` | Source cannot support operational, promotion, or live-performance truth claims. | Context only; must not gate promotion or assert clean execution. |

## Control Implications

- Reliability readiness must add artifact-completeness gating before GREEN can support promotion.
- Promotion evidence must require PIT universe, benchmark source, return convention, holdout status, cost model, drawdown, turnover, capacity, and execution evidence coverage.
- Operator/CIO reporting should suppress or downgrade claims when portfolio history, NAV chain, broker positions, or run-level execution evidence are stale/missing.
- Standalone broker or target-attainment artifacts must not be treated as run-level proof unless linked to `run_id` and `trade_date`.

## Coverage Gaps

1. Complete run-bundle retention is missing for historical execution roots.
2. Broker-authoritative portfolio history is stale locally after `2026-04-09`.
3. Price/benchmark surfaces use multiple sources and conventions without a universal lineage field.
4. EDGAR identity joins still rely on partial static mappings in some paths.
5. Sleeve promotion language is not uniformly constrained by evidence-envelope completeness.
6. Reliability history lacks a native artifact-completeness invariant.

## Recommended Next Controls

1. FR-074 Phase C: add artifact-completeness invariant and require FULL coverage for promotion-usable GREEN.
2. FR-077 Phase B: create a machine-readable `decision_grade_evidence_contract.json` used by promotion and CIO reports.
3. Add return-convention and source-lineage fields to all performance and benchmark artifacts.
4. Add a run-retention validator that fails governance review when execution roots lack required broker/recon/target/reliability files.
5. Add a report suppressor that labels claims `RESEARCH_ONLY`, `LOW_COVERAGE`, or `NOT_DECISION_GRADE` when evidence contracts are incomplete.


## Falsification Caveats

1. Historical `RELIABILITY_GREEN` means no FR-074 invariant fired under sparse legacy artifacts, not historically clean execution. Replay rows also contain trade-date lineage defects such as invalid or mismatched dates.
2. Static metadata/evidence-envelope validation is necessary but insufficient; it does not prove referenced metrics, benchmark series, fills, costs, or missingness completeness.
3. Broker-authoritative terminal order evidence must outrank model-reported execution status.
4. Legacy mixed-convention Shadow history is lineage-only unless explicitly restated and validated.
5. PIT-rebased evidence and legacy current-universe outputs must not be mixed in promotion conclusions.

## Governance Conclusion

Caerus has enough evidence to continue research triage and to identify several high-value data-quality gaps. It does not yet have enough local execution evidence or unified lineage controls to let reliability GREEN, historical alpha, or sleeve promotion language stand as decision-grade operational truth.
