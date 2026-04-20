# Options Strategy — Caerus Family Fund

**Context**: Personal/family capital only. No external investors, no registration
requirements, no redemption risk. All constraints are self-imposed: risk tolerance,
tax efficiency, and strategy conviction.

**Current state**: Regime-gated protective puts live in paper execution as of
2026-04-20. SPY puts fire automatically when the model classifies crisis vol +
washed-out breadth. Up to 3 contracts on a $9.7K portfolio at 500bps premium budget.

---

## Core Thesis

The regime engine already does the hard work — it classifies market state across
four independent dimensions (trend, volatility, breadth, macro). Options are the
most capital-efficient way to express a high-conviction directional view when that
classification fires a strong signal.

The edge is **not** vol surface expertise or beating market makers on pricing.
The edge is being right about the regime — a binary call (risk-on vs. risk-off)
that the model has been scoring and tracking. Options translate that regime call
into convex payoffs instead of linear ones.

---

## Why Personal Capital Changes the Calculus

| Factor | Implication |
|---|---|
| No redemption risk | Can hold through theta decay for months waiting for the thesis to pay |
| No benchmark pressure | Concentrated, high-conviction positions are fine |
| No LP communication | Complex strategies don't need to be explained to non-quants |
| Full tax control | Harvest losses, time gains, optimize long-term vs. short-term |
| Patient capital | Can size puts systematically as an annual carry cost, not a crisis reaction |

The key behavioral advantage: the system removes the emotional decision. The model
decides when to buy puts, not a gut reaction to a bad tape.

---

## Current Implementation

### What's Live (Paper)

| Component | Status | File |
|---|---|---|
| Regime-gated put selection | Active | `core/options_overlay_shadow.py` |
| Directional contract sizing | Active | `config/options_overlay_policy.json` |
| Paper execution via Alpaca | Active | `scripts/cron_execute.sh` |
| Protective put (crisis regime) | Executing | `config/options_execution_policy.json` |

### Current Sizing Parameters

```
Protective put:   500bps budget, $150/contract estimate, max 5 contracts
Put spread:       200bps budget,  $75/contract estimate, max 3 contracts
Feasibility floor: $50 minimum premium budget to attempt execution
```

### When Each Strategy Fires

| Strategy | Triggers on |
|---|---|
| Protective put | Crisis VIX (>30) OR washed-out breadth (<30% above 200-DMA) |
| Put spread | Risk-off regime + deteriorating breadth (elevated VIX 22-30) |
| Covered call | Risk-on trending + normal/elevated VIX (not yet live) |
| LEAP call | Risk-on or neutral + healthy/mixed breadth (not yet live) |

---

## Open Questions to Revisit

### 1. Is the $150/contract cost estimate calibrated?

The `per_contract_cost_estimate_dollars: 150` in the policy is a rough planning
number. In practice:
- Crisis VIX (>30): 35-DTE 2%-OTM SPY put costs ~$400–800/contract
- Elevated VIX (22-30): same put costs ~$150–350/contract
- Calm VIX (<16): same put costs ~$50–100/contract

**Action**: After first few live executions, compare actual fill prices to the
$150 estimate and recalibrate. Consider making this VIX-regime-conditional.

### 2. Should we scale contract count with portfolio size?

Current max is 5 contracts regardless of portfolio size. At $9.7K that's
meaningful (3 contracts ≈ $450 premium = 4.6% of portfolio). At $100K that's
trivially small. Consider:
- `max_contracts_pct_of_equity: 5%` as an alternative to a flat cap
- Or a tiered schedule: $0–25K → max 3, $25–100K → max 10, $100K+ → uncapped

### 3. Put spread vs. naked put — which performs better in our regime?

Put spread (buy near-ATM, sell further OTM):
- Lower net premium — costs less in carry
- Capped upside — you miss the fat tail if the market crashes hard
- Better for "risk-off but not catastrophic" environments

Naked protective put:
- Higher carry cost
- Full convexity — unlimited profit if market collapses
- Better when the model classifies crisis (which is exactly when it fires now)

**Action**: Track shadow P&L of both strategies through a full market cycle before
deciding which to weight more heavily.

### 4. Should we add calls on the upside?

The current overlay is entirely defensive (puts). In a strong risk-on regime
with healthy breadth, covered calls or LEAP calls could:
- Harvest premium from existing equity positions (covered call)
- Provide leveraged upside exposure on high-conviction names (LEAP)

Neither has live execution wired yet. These are the next options strategies to
promote from shadow to paper.

### 5. Tax treatment

Options have specific tax rules that matter at the personal level:
- **Section 1256 contracts** (broad-based index options like SPX, XSP): 60%
  long-term / 40% short-term regardless of holding period — favorable
- **SPY options** (ETF options): taxed like equity — short-term if held <1 year
- **Wash sale risk**: Closing a put at a loss and reopening a similar put within
  30 days triggers wash sale; the loss is deferred

**Consideration**: If options become a meaningful part of the strategy, evaluate
whether shifting from SPY puts to SPX/XSP puts (European-style, cash-settled,
Section 1256) is worth the larger contract size ($49K vs. ~$5K notional for XSP).

---

## Strategy Evaluation Framework

Track these metrics per options cycle (entry to expiry or close):

| Metric | What it tells you |
|---|---|
| Premium paid vs. intrinsic value at expiry | Did the regime call pay off? |
| Theta decay as % of portfolio per month | True cost of carry |
| Max drawdown avoided vs. unhedged | Hedge effectiveness |
| Contracts filled vs. recommended | Execution slippage / budget accuracy |
| Regime state at entry vs. at expiry | Did the model classify correctly? |

Review cadence: monthly for individual trade P&L, quarterly for strategy-level
assessment, annually for parameter recalibration.

---

## Longer-Term Possibilities

These are not near-term — placeholder for when the current system is validated
through a full market cycle:

**Volatility targeting**: Size the equity book inversely to realized vol, so the
portfolio maintains constant vol exposure. Options become a vol-scaling tool
rather than just a hedge.

**Dispersion trading**: If the universe expands to 500+ tickers, index vol vs.
constituent vol relationships become tradeable. Sell index puts, buy single-name
puts on the weaker names. Requires much more capital and infrastructure.

**Systematic premium harvesting**: In calm/normal VIX regimes, systematically
sell covered calls on top equity positions. Not implemented yet — requires
covered inventory validation in the overlay.

**LEAP calls as cash replacement**: Use deep ITM LEAP calls (85% moneyness,
390 DTE) instead of holding the underlying directly. Frees cash, reduces PDT
exposure, and provides similar delta. Already in the strategy config — needs
execution wiring.

---

## Immediate Next Steps

1. Monitor first live paper put executions — verify fills, OCC symbol construction,
   and position appearing correctly in Alpaca
2. Recalibrate `per_contract_cost_estimate_dollars` after 2–3 real fills
3. Wire covered call execution path in `core/options_execution.py`
4. After one full market cycle (3–6 months), compare hedged vs. unhedged NAV
   to validate the regime-gated approach

---

*Last updated: 2026-04-20. Resume from "Open Questions" section.*
