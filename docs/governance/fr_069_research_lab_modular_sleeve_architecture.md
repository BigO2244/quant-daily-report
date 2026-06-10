# FR-069 — Research Lab / Modular Sleeve Architecture (Design)

Status: DESIGN (design-only — no production refactor in this FR)
Owner: Caerus Research Program
Last Updated: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL (this document changes nothing that runs — no
code, execution, broker, cron, registry, allocation, or paper/live behavior)

## 0. Purpose and Boundary

The FR-068 PIT milestone gives Caerus, for the first time, a survivorship-free
research foundation: `Universe(as_of_date)`, a delisted-inclusive security master,
membership families, and Sharadar SEP prices. The Polaris priced rebaseline proved
the foundation is decision-relevant (Sharpe overstated 1.05→0.85; drawdown
understated −43%→−54%). Today, however, each strategy ("sleeve") reaches that data
through its own bespoke path (`alpha_lab_v1/v2`, `flow_detection`, `research/cygnus`,
`research/phoenix`, ad-hoc audit harnesses), with inconsistent universe sources,
price sources, metrics, and governance.

This document designs a **Research Lab**: a modular architecture where every sleeve
is a pluggable module behind one contract, all consuming the PIT foundation, the
same backtest engine, the same metrics, and the same governance gates.

**This FR is design only.** It proposes contracts and a migration sequence; it does
not refactor production, does not move any sleeve, and does not change execution,
ranking, sizing, risk, cron, or the strategy registry. Implementation is sequenced
into later FRs, each small and reversible.

## 1. Problem Statement (why modularize)

Observed fragmentation as of 2026-06-10:

- **Universe inconsistency:** most harnesses read the static `data/universe.csv`
  (survivorship-biased, now non-decision-grade); only the FR-068 rebaseline reads
  `Universe(as_of_date)`.
- **Price inconsistency:** yfinance matrix (current-only), Sharadar SEP cache
  (PIT, delisted), and per-sleeve caches coexist with no single contract.
- **Metric inconsistency:** CAGR/Sharpe/etc. are computed in several places
  (`alpha_stack/research/metrics.py`, audit scripts, Cygnus backtest) with subtly
  different conventions.
- **Governance inconsistency:** no enforced `universe_method` tag; holdout
  protection and pre-registration are honored by convention, not by contract.
- **Onboarding cost:** adding a sleeve (Cygnus, Vela) means re-implementing data
  access, backtest loop, metrics, and artifact writing each time.

A modular lab makes the PIT-first, holdout-protected, pre-registered path the
**default and the easy path**, so survivorship bias and look-ahead cannot quietly
re-enter.

## 2. Layered Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  L5  Governance & Evaluation                                        │
│      pre-registration, universe_method gate, holdout guard,         │
│      promotion readiness, decision-grade verdicts                   │
├──────────────────────────────────────────────────────────────────┤
│  L4  Backtest / Validation Harness (shared, single implementation)  │
│      walk-forward, costs, turnover, NAV, attribution                │
├──────────────────────────────────────────────────────────────────┤
│  L3  Sleeve Registry  (pluggable strategy modules behind a contract)│
│      polaris · orion · lyra · phoenix · cygnus · vela · argo(overlay)│
├──────────────────────────────────────────────────────────────────┤
│  L2  Signal / Feature Library  (reusable, side-effect-free)         │
│      momentum, vol/ATR, event-reaction, revenue accel, quality...   │
├──────────────────────────────────────────────────────────────────┤
│  L1  Data Layer  (one contract, PIT by construction)                │
│      Universe(as_of_date, family) · Prices(SEP) · Fundamentals(PIT) │
│      Events(EDGAR) · Benchmarks · Trading calendar                  │
└──────────────────────────────────────────────────────────────────┘
```

Dependencies point downward only. A sleeve (L3) may use any L2 features and L1
data, but never reaches around the data contract or writes production state.

### L1 — Data Layer (the PIT contract)

One module surface, PIT by construction:

- `Universe(as_of_date, family, min_confidence)` — already built (FR-068);
  families: `sharadar_security_existence`, `caerus_large_cap`, and (future)
  `small_cap_band`, `sp500_proxy`, `event_universe`.
- `Prices` — adjusted daily closes from the Sharadar SEP cache (delisted-inclusive),
  with the current-only yfinance matrix demoted to a clearly-labeled diagnostic
  fallback. Returns are PIT (no future bars).
- `Fundamentals` — filed-date-gated XBRL (already in `data/fundamental/`), extended
  via Sharadar SF1 when licensed; never restated-backward.
- `Events` — EDGAR acceptance-timestamped tape (already built for Cygnus).
- `Benchmarks` / `TradingCalendar` — SPY/IWM/SGOV, US session calendar.

Invariant: **no L1 accessor silently falls back to `data/universe.csv`**; missing
PIT data fails loudly (the `PITUniverseUnavailable` pattern).

### L2 — Signal / Feature Library

Pure, deterministic, side-effect-free functions over L1 panels: momentum
(12-1/6-1/3-1), ATR/vol adjustment, cross-sectional z-scores and percentiles,
event-reaction abnormal return, revenue-YoY acceleration, drift confirmation,
quality screens. Existing implementations (`alpha_stack/sleeves/`,
`research/cygnus/features.py`, `alpha_lab_v1/signals.py`) are consolidated here over
time — no new math, just one home and one convention.

### L3 — Sleeve Registry and the Sleeve Contract

A **Sleeve** is a module implementing a small, explicit interface (illustrative):

```python
class Sleeve(Protocol):
    sleeve_id: str                 # e.g. "caerus_polaris"
    universe_family: str           # e.g. "caerus_large_cap"
    benchmark: str                 # e.g. "SPY"

    def candidates(self, as_of) -> list[str]: ...        # from Universe(as_of, family)
    def signals(self, panel, as_of) -> Frame: ...        # L2 features only
    def select(self, signals, as_of) -> Weights: ...     # ranking + sizing + risk
    def exit_rules(self) -> ExitPolicy: ...
    spec: SleeveSpec               # top_n, costs, rebalance, risk caps (frozen)
```

The contract makes the universe family and benchmark **declared, not implicit**, and
forces every sleeve through `Universe(as_of_date)`. Mapping of existing strategies:

| Sleeve | sleeve_id | universe_family | Status today | Lab target |
|---|---|---|---|---|
| Polaris | `caerus_polaris` | `caerus_large_cap` | paper baseline; PIT-rebaselined (MATERIAL) | reference sleeve |
| Orion | `caerus_orion` | `caerus_large_cap` | shadow; rebaseline pending | wrap `h2_rank_decay_exit` |
| Lyra | `caerus_lyra` | `caerus_large_cap` | shadow; rebaseline pending | wrap `h1_weekly` |
| Phoenix | `caerus_phoenix` | `caerus_large_cap` | research (crisis reversal) | sleeve + regime input |
| Cygnus | `caerus_cygnus` | event universe (EDGAR) | v0 shelved (FAIL); v1 vendor-gated | event sleeve |
| Vela | `caerus_vela` (proposed) | `small_cap_band` | blocked on Stage 0 (now unblockable via Sharadar) | small-cap sleeve |
| Argo | `caerus_argo` | n/a (meta) | regime/selector overlay | L5 selector, not a sleeve |

Argo is explicitly **not** a sleeve — it is a meta/selection overlay that consumes
sleeve evidence at L5.

### L4 — Backtest / Validation Harness (single implementation)

One engine runs any sleeve: per-date `candidates → signals → select → weights`,
applies the frozen cost/turnover/rebalance/risk rules, and produces NAV, returns,
turnover, holdings, and per-security contribution. The committed `alpha_lab_v2`
engine is the seed; it is generalized to take a `Sleeve` rather than a hard-coded
universe + signal frame. Walk-forward windows and **holdout protection are enforced
here** (the harness refuses to read beyond the configured holdout).

### L5 — Governance & Evaluation

- **Pre-registration:** pass/fail criteria frozen before the first run (as Cygnus
  A4 and Vela did).
- **`universe_method` gate:** every evidence artifact must carry
  `universe_method = pit_universe`; `legacy_current_universe` artifacts are
  non-decision-grade by contract.
- **Holdout guard:** holdout windows are unreadable until variant selection is
  frozen and the owner authorizes a single holdout run.
- **Promotion readiness / Argo:** consume only PIT-method evidence.

## 3. Artifact Contract

Every sleeve run emits the same envelope (extending today's conventions):

`schema_version, sleeve_id, universe_family, universe_method, universe_snapshot_hash,
price_source, trade_date / window, holdout_excluded, spec, metrics{cagr,sharpe,
sortino,max_drawdown,volatility,turnover,hit_rate,beta,excess_vs_benchmark},
attribution[], governance_label, execution_impact, reason_codes`.

The `universe_snapshot_hash` + `price_source` make every result reproducible and
auditable; `universe_method` makes survivorship status machine-checkable.

## 4. What Stays Frozen (non-goals)

- No production Polaris/Orion/Lyra behavior, ranking, sizing, risk, cost, or
  execution change.
- No cron, broker, allocation, or `strategy_registry.json` change.
- No new alpha math — the lab consolidates existing logic, it does not invent.
- No holdout access.
- This FR ships **no code**. Each migration step below is its own future FR with
  its own validation and rollback.

## 5. Migration Sequence (each a separate, reversible future FR)

1. **L1 contract finalization** — promote `Universe(as_of_date)` + a `Prices(SEP)`
   accessor to the canonical data surface; mark yfinance matrix diagnostic-only.
2. **L4 generalization** — refactor the `alpha_lab_v2` engine to accept a `Sleeve`
   (Polaris as the reference sleeve; output parity vs the FR-068 rebaseline as the
   acceptance test).
3. **Orion + Lyra sleeves** — wrap the existing H2/H1 specs; PIT rebaseline both
   (completes the FR-068 set).
4. **L2 consolidation** — move momentum/vol/event/quality features into the shared
   library behind tests (no math change).
5. **Event + small-cap families** — `event_universe` (Cygnus) and `small_cap_band`
   (Vela) membership families; unblock Vela Stage 1 on the PIT foundation.
6. **L5 governance gate** — enforce `universe_method = pit_universe` in promotion
   readiness and the review packet; route Argo to PIT-method evidence only.

Order is lowest-risk-first; nothing migrates until its parity/validation passes,
and production consumers keep their current behavior until a separate
deployment/governance approval.

## 6. Open Questions (owner decisions)

1. **Sleeve registry home** — extend `config/research/strategy_registry.json`
   semantics (owner-gated; registry edits are out of scope here) or a separate
   research-only sleeve manifest? Recommend the latter until the contract is proven.
2. **Large-cap family definition** — keep `caerus_large_cap` on current
   `scalemarketcap` (PIT-approximate) or rebuild on DAILY market cap (PIT-exact)
   before the Orion/Lyra rebaselines?
3. **Price source of record** — SEP as canonical for all research; what is the
   reconciliation policy vs the live Alpaca/FR-066 NAV series for shadow/paper?
4. **Benchmark families** — SPY for large-cap sleeves; IWM/S&P 600 proxy for Vela —
   standardize the benchmark per `universe_family`.
5. **Backwards-compat window** — how long do `legacy_current_universe` artifacts
   remain readable (lineage) before archival?

## 7. Success Criteria (for the migration FRs, not this design)

- A new sleeve can be added by implementing the contract only (no bespoke data,
  backtest, or metrics code).
- Every evidence artifact carries `universe_method = pit_universe` and a universe
  hash; legacy artifacts are machine-flagged non-decision-grade.
- Polaris through the generalized harness reproduces the FR-068 rebaseline within
  tolerance (parity gate).
- Holdout windows are unreadable without explicit owner authorization.
- No production execution/model/cron/registry behavior changed by any step.
