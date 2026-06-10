# Sharadar Value Assessment — PIT Remediation & Alpha Research

Date: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Local-only: yes (Mac Studio). VM/deploy/cron/model/execution actions: none.
Spend actions: none (no subscription purchased; recommendation is advisory).
Holdout access: none. API keys: never logged or written to any artifact.

Companion docs: `research/survivorship_bias_audit_2026-06-10.md` (verdict),
`research/pit_universe_architecture_2026-06-10.md` (remediation plan + vendor
matrix), `docs/governance/fr_067_stage0_source_comparison.md` (Stage 0 source
comparison), `scripts/research/verify_sharadar_coverage.py` (coverage verifier,
already built and ready to run on a paid key).

---

## 1. Executive Summary

The survivorship audit returned **CONFIRMED_BIASED / HIGH**. The root defect is
that every official historical artifact is built from current-only data: the
trading universe (`data/universe.csv`, 200 current names), the price matrix
(`alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet`, current names
only), and the fundamentals store (`data/fundamental/`, 200 current names only)
contain **zero delisted securities and no point-in-time membership**. No amount
of internal reconstruction can manufacture the delisted losers and ended
memberships that are missing — local data can support diagnostics, not
decision-grade PIT evidence.

Sharadar is the lowest-friction vendor that can supply the missing pieces for
the **large-cap book** (Polaris/Orion/Lyra rebaseline): delisted prices,
corporate actions, stable identifiers, symbol-change history, S&P 500 membership
events, and PIT fundamentals. The free preview has **confirmed everything except
the one thing that matters most — delisted price history** (TWTR/ATVI SEP
queries return empty under the un-entitled account). That single unknown is
paywalled, and the coverage verifier needed to resolve it already exists.

**Recommendation: A) BUY_ONE_MONTH_NOW** (advisory — owner executes the
purchase). Buy one month of the **Sharadar SFA bundle** (or, minimally, **SEP**
for prices), run the existing verifier, and decide FR-068 on pre-registered
pass/fail. Confidence: **HIGH** for large-cap remediation value; **MEDIUM** for
small-cap (Vela) membership (Sharadar lacks S&P 600 membership → use market-cap
bands); **LOW/NO** for Cygnus v1 (Sharadar carries no analyst consensus, so it
does not solve EPS-surprise-vs-consensus).

A one-month validation costs on the order of ~$50–300 and is resolved in hours
because the verifier is built. The internal-only alternative cannot reach
decision-grade at any engineering cost. The asymmetry strongly favors buying one
month now.

---

## 2. Current Internal Data Inventory (Task 1)

### 2.1 What exists locally

| Source | Content | PIT / survivorship status |
|---|---|---|
| `data/universe.csv` | 200 current `ticker,sector` rows | Static current set; no membership dates, no delisted flag, no stable ID |
| `alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet` | Adjusted close, 2007–2026, ~203 cols | Current names only; **no delisted** (TWTR/ATVI/FB/SIVB/FRC/CTXS all absent) |
| `data/fundamental/<ticker>.parquet` | XBRL facts; `tag, period_end, filed_date, value` | 200 current names only; **filed_date is PIT-correct** but coverage is survivorship-biased |
| `cik_mapping_results.csv` | ticker → 10-digit CIK | CIK is a usable stable issuer ID, but only for current names |
| `research/cygnus/` EDGAR event tape | 8-K Item 2.02 by `acceptanceDateTime` | Event timing is genuinely PIT; universe is current names only |
| `core/security_master.py` + `data/security_master/manual_aliases.json` | Alias map (e.g. BK→BNY), MMC ignore | **Alias sidecar, not a PIT security master**; no listing/delisting/membership |
| `data/ticker_exceptions.json` | Provider-symbol aliases/notes | Operational alias handling only |

### 2.2 PIT requirements current data CAN satisfy

- **Event-time PIT** for filings (EDGAR `acceptanceDateTime`) — already proven.
- **Filed-fundamental PIT** for current names (`filed_date` gating) — usable.
- **Stable issuer ID for current names** via CIK.
- **Adjusted prices for current names** 2007–present.

### 2.3 PIT requirements current data CANNOT satisfy

- **Delisted securities / delisted prices / delisting returns** — none present.
- **Historical index membership** with entry/exit dates — none present.
- **PIT membership dates** for any family — none present.
- **Symbol-change history as data** — only a handful of manual aliases.
- **Survivorship-free universe at a historical `as_of_date`** — impossible from
  current-only sources.

### 2.4 Effort & residual bias of an internal-only build

- The PIT **framework/contract** (`Universe(as_of_date)`, schema, fixtures,
  tests) is buildable locally in ~4–8 engineering days (per the remediation
  plan) **with no vendor**.
- But that framework would have **no real historical data to resolve** — it can
  only run on fixtures/diagnostics.
- **Expected residual bias if shipped on internal-only data: HIGH / unchanged.**
  The fragility test already shows 24.99%→19.29% CAGR and 0.969→0.83 Sharpe just
  from restricting to names priced on 2014-01-02; a true PIT universe that
  *adds back delisted losers* would very likely degrade further. Internal-only
  cannot add those losers, so it cannot remove the bias. **Internal
  reconstruction is diagnostics-only and is not a substitute for a vendor.**

---

## 3. Sharadar Capability Assessment (Task 2)

Status legend: **[C]** confirmed by free-preview tests · **[D]** claimed by
public docs · **[U]** unverified until paid entitlement · **[N]** likely
unavailable / needs supplemental source.

| Capability | Status | Notes |
|---|---|---|
| Security master (TICKERS) | **[C]** | Accessible now; rich reference fields |
| Stable security identifier | **[C]** | `permaticker` present; persists across ticker changes |
| Symbol-change linkage | **[C]** | `relatedtickers` (e.g. META ↔ FB) observed |
| Active/delisted **listing** in TICKERS | **[C]** | `isdelisted`; TWTR & ATVI appear as delisted |
| Listing/delisting dates | **[C]** | `firstpricedate` / `lastpricedate` present in TICKERS |
| Active prices (SEP) | **[C]** | AAPL SEP works under preview |
| **Delisted prices (SEP)** | **[U] — preview returns EMPTY** | TWTR/ATVI SEP empty under un-entitled account; **the paywalled unknown** |
| Adjusted prices / splits-divs | **[D]** | SEP advertises EOD adjusted prices |
| Corporate actions | **[D]** | `SHARADAR/ACTIONS` (splits, dividends, delistings) |
| Fundamentals (SF1) | **[D]** | "active and delisted, point-in-time, 16,000+ companies" |
| PIT financial statements | **[D]** | SF1 dimensions (ARQ/ART/MRQ) with reporting/filing dates |
| Earnings actuals (EPS) | **[D]** | SF1 carries reported EPS (actuals) |
| **EPS surprise vs consensus / analyst estimates** | **[N]** | Sharadar carries **no analyst consensus**; Cygnus v1 surprise-vs-consensus needs a separate estimates vendor |
| **S&P 500 membership history** | **[D]** | `SHARADAR/SP500` add/remove events (bundle-dependent) |
| **S&P 400 / S&P 600 / Russell membership** | **[N]** | Not provided; small-cap (Vela) membership must use **market-cap bands** via `SHARADAR/DAILY`, or a supplemental membership source |

**Net:** Sharadar is strong-to-excellent for the **large-cap survivorship
remediation** that the audit flags, contingent on the one paywalled unknown
(delisted price completeness). It is a **partial** solution for small-cap
membership and **not** a solution for Cygnus-v1 consensus.

---

## 4. Product / Entitlement Decision (Task 3)

| Product | Likely cost (verify) | Included | PIT universe | Cygnus v1 | Future lab | Replace/supplement | Key unknown |
|---|---|---|---|---|---|---|---|
| **SF1** Core US Fundamentals | ~$49/mo personal (owner-observed) | PIT fundamentals, EPS actuals, 16k+ incl delisted | Helps fundamentals dimension; **no prices** | Partial (PIT actuals, **no consensus**) | Yes (value/quality) | Supplements | Delisted-fundamental depth; personal vs commercial tier |
| **SEP** Equity Prices | higher than SF1 (verify) | EOD adjusted prices incl delisted; pairs with ACTIONS | **Core enabler** (delisted prices) | Indirect | Yes | **Replaces** current price matrix for PIT | Delisted price completeness & history depth |
| **SFA** Core US Equities Bundle | materially higher (verify; commercial tier ≫ personal) | SF1+SEP+SF2/SF3+ACTIONS+DAILY+EVENTS+METRICS+SP500+TICKERS | **Best single buy** for full validation | Best (actuals+events) | Best | Replaces price+fundamentals; supplements membership | Bundle entitlement & licence tier; small-cap membership still absent |
| **SFP** Fund Prices | verify | ETF/fund EOD prices | Benchmarks only (IWM/SGOV/SPY) | No | Minor | Supplement | Not core to remediation |
| **SHARADAR/SP500** (table) | bundled where entitled | S&P 500 constituent add/remove events | Large-cap membership family | No | Yes | Supplement | Which tier exposes it; history start date |

**Decision:** for a one-month *throwaway validation* that resolves every use
case at once, the **SFA bundle** is the cleanest buy (avoids "bought the wrong
SKU"). If cost-minimizing, **SEP** is the minimum that resolves the critical
unknown (delisted prices); add SF1 if fundamentals/value-quality are wanted.
The **personal vs commercial/fund licence tier** is the single most important
entitlement question to settle before any annual commitment.

---

## 5. Alternatives Comparison (Task 4)

| Vendor | Cost | Delisted coverage | PIT membership | Symbol changes | Adjusted prices | Fundamentals / earnings | Impl. effort | Suitability for Caerus |
|---|---|---|---|---|---|---|---|---|
| **Sharadar** | ~$49/mo (SF1) → bundle higher | Strong (claimed; **verify SEP**) | S&P 500 yes; **600/Russell no** (use mktcap band) | permaticker/relatedtickers | Yes | Fundamentals + EPS actuals; **no consensus** | **Low** (REST/bulk; native to Mac/Linux; verifier built) | **First gate — recommended** |
| **Norgate** | ~$50–80/mo | Strong, survivorship-free | **Strong** incl S&P 600 / Russell | Strong | Yes | Limited fundamentals | **Medium/High** (Windows NDU dependency) | Best backup if Sharadar delisted/membership fails |
| **CRSP / WRDS** | High / access-gated | Excellent (delisting returns) | Excellent (PERMNO) | Excellent | Yes | Compustat link | Medium (if access) | Gold standard **iff** academic/WRDS access |
| **Polygon** | ~moderate/high | Verify | Insufficient for membership | Moderate | Yes | Limited | Medium | Price supplement, not sufficient alone |
| **Tiingo** | Low/moderate | Verify (weak on dead tickers) | Insufficient | Moderate | Yes | Some fundamentals | Low | Supplement, not canonical |
| **Nasdaq free / current** | Free | None survivorship-free | None | Weak | Current only | Limited | Low | Diagnostics only |
| **Internal reconstruction only** | $0 | **None** | Partial/fragile | Weak | Current only | Current only | High (QA burden) | **Not decision-grade** |

Sharadar wins on integration fit + cost + breadth; its gaps (small-cap
membership, consensus) are shared by most affordable options and are addressable
by market-cap bands (membership) and a separate estimates vendor (consensus).
Norgate is the only single vendor that natively provides S&P 600 membership, at
the cost of a Windows operational dependency.

---

## 6. ROI / Decision Matrix (Task 5)

**Benefits of buying Sharadar (one month):**

- Resolves the only paywalled unknown (delisted price completeness) that blocks
  the entire PIT remediation — **information value is decisive, not incremental**.
- Directly fixes the survivorship foundation for the large-cap book and enables
  Polaris/Orion/Lyra rebaseline (the audit's core defect).
- Improves research credibility (decision-grade evidence vs CONFIRMED_BIASED).
- Verifier is already built → near-zero integration cost to validate.

**Costs / risks:**

- One-month subscription (~$50–300 depending on product/tier).
- Entitlement uncertainty (personal vs commercial licence) for annual commit.
- Small-cap membership still requires market-cap-band reconstruction.
- Consensus estimates still require a separate vendor for Cygnus v1.
- Possible duplication with the existing yfinance price matrix (acceptable; SEP
  becomes the canonical PIT source, yfinance demoted to diagnostics).

**Quantified estimate:**

| Item | Estimate |
|---|---|
| One-month validation spend | ~$50–300 (order-of-$100) |
| Validation engineering time | < 1 day (verifier exists) |
| Engineering "saved" if it passes | Not hours — it is the **only** path to decision-grade delisted data short of Norgate/CRSP |
| Engineering "wasted" if it fails | < 1 day + one month's fee; then pivot to Norgate |
| Opportunity cost of internal-only | High: build a contract with no data; audit stays CONFIRMED_BIASED; no rebaseline possible |

**Asymmetry:** bounded ~$100 + <1 day downside vs unblocking the entire FR-068
remediation. Strongly favors buying one month now.

---

## 7. Recommendation (Task 6)

**Call: A) BUY_ONE_MONTH_NOW** — advisory; the owner executes the purchase (this
session spends nothing).

- **Rationale:** The free preview confirmed every reference capability; the only
  remaining unknown — delisted price completeness, the exact thing the audit
  needs — is paywalled. The verifier is built. The cheapest way to de-risk the
  whole FR-068 plan is one month of data + one verifier run. Internal-only cannot
  reach decision-grade at any cost.
- **Confidence:** HIGH (large-cap remediation), MEDIUM (small-cap membership via
  bands), LOW/NO (Cygnus v1 consensus).
- **Required product:** **SFA bundle** for a one-month all-use-case validation
  (or **SEP** minimum to resolve delisted prices; add **SF1** for fundamentals).
- **Exact purchase trigger:** purchase now, scoped to a single month, explicitly
  to run the coverage verifier and the spot checks below. Do **not** auto-renew
  or buy annual until pass/fail is reviewed.
- **Exact post-purchase verification commands** (key supplied via env file,
  never logged):

  ```bash
  # 1. small-cap delisted coverage (50-name sample, 2010-2024)
  python3 scripts/research/verify_sharadar_coverage.py \
      --env-file <path-to-sharadar-key-env> \
      --sample-size 50 --start-year 2010 --end-year 2024
  #    -> outputs/research/vela/sharadar_coverage_report.json

  # 2. preview the resolved sample without fetching prices (sanity)
  python3 scripts/research/verify_sharadar_coverage.py \
      --env-file <path-to-sharadar-key-env> --list-only

  # 3. large-cap delisted spot checks (expect non-empty full SEP history):
  #    TWTR, ATVI, SIVB, FRC, CTXS  (via the same SEP query path)

  # 4. validate machine-readable output
  python3 -m json.tool outputs/research/vela/sharadar_coverage_report.json >/dev/null
  ```

- **Pass/fail criteria (pre-registered):**
  - **PASS** if ≥ 90% of the 50 delisted small-cap sample have complete adjusted
    price history through their delisting date, AND all 5 large-cap delisted
    spot-checks return complete SEP history, AND `SHARADAR/SP500` membership
    events resolve back to ≥ 2014.
  - **MARGINAL** (re-scope / supplement) if delisted small-cap completeness is
    75–90% or membership history is shallow.
  - **FAIL** (pivot to Norgate) if delisted small-cap completeness < 75% or
    delisted prices remain empty under the paid entitlement.

---

## 8. Purchase Trigger (summary)

Buy **now**, one month, **SFA bundle** (or SEP minimum). The trigger condition is
already met: audit is HIGH-confidence biased, the PIT plan is written, and the
verifier is ready. The buy exists to convert the single paywalled unknown into a
decision. Do not commit to an annual licence until the pass/fail above is signed
off and the personal-vs-commercial tier is confirmed.

---

## 9. Post-Purchase Verification Plan (summary)

1. Run the coverage verifier (small-cap, 50 names, 2010–2024).
2. Run large-cap delisted spot checks (TWTR/ATVI/SIVB/FRC/CTXS).
3. Confirm `SHARADAR/SP500` membership depth and `SHARADAR/ACTIONS` completeness.
4. Confirm `permaticker` stability across a known symbol change (FB→META).
5. Confirm SF1 PIT fundamentals for ≥ 2 delisted names (filing dates present).
6. `json.tool` the report; record results in
   `outputs/research/vendor_value/sharadar/<date>/`.
7. Decide FR-068 go/no-go per §10.

---

## 10. FR-068 Go / No-Go Implication

FR-068 = the Point-in-Time Universe Build + Polaris/Orion/Lyra rebaseline (the
draft FR in the remediation plan). Implication of this assessment:

- **If validation PASSES:** **GO** for FR-068 with Sharadar as the canonical PIT
  source for the **large-cap** families. Small-cap (Vela) membership uses
  market-cap bands from `SHARADAR/DAILY` (no S&P 600 dependency). Cygnus v1
  remains **separately blocked** on an analyst-consensus vendor — Sharadar does
  not unblock it.
- **If validation FAILS:** **NO-GO on Sharadar**; pivot to **Norgate**
  (option D) for membership + delisted coverage, accepting the Windows
  operational cost. Build the read-only `Universe(as_of_date)` skeleton +
  fixtures meanwhile (no vendor needed) so FR-068 Phase 1 is not idle.
- Either way, FR-068 Phase 1 first task (the PIT skeleton/contract with fixtures)
  can begin immediately with no spend; the vendor decision gates only the
  *historical data hydration* and the official rebaseline.

---

## Addendum 2026-06-10b — Paid entitlement result + verifier scoring fix

The owner purchased Sharadar access and ran the verifier (`--sample-size 100`).
Two findings:

1. **Paid entitlement resolves the preview gap.** Under the paid key the verifier
   now retrieves historical prices for delisted securities (e.g. GYMB: 3,245
   price rows, 1993→2010), versus the preview's empty SEP responses. The single
   paywalled unknown from the main assessment — *does Sharadar actually deliver
   delisted prices* — is answered **yes** for the sampled names.

2. **Verifier scoring bug (found + fixed).** The first paid run reported
   `coverage_pct = null`, `expected_trading_days = null`, and
   `reaches_delist_date = false` for all 100 names despite thousands of price
   rows. Root cause: `scripts/research/verify_sharadar_coverage.py` imported
   `paper.trading_calendar`, which imports `pandas`; run under a Python without
   pandas, that import failed silently (swallowed `try/except`) and every
   calendar-derived field collapsed to null/false. Fix: scoring is now
   calendar-independent and deterministic (weekday count × 252/261), coverage is
   measured over the observed price window, and `reaches_delist_date` is a direct
   date comparison. Re-scoring the existing 100-name report with the corrected
   `reaches_delist` logic flips it **0 → 100/100** (all sampled names deliver
   prices through their exact delisting date).

**Status: still INCONCLUSIVE on full PASS/FAIL** until the verifier is re-run with
the key to populate within-window `coverage_pct` (internal-gap completeness).
Reaches-delist (100/100) is necessary but not sufficient — `complete` also
requires `coverage_pct ≥ 0.95`. **FR-068 remains NOT-GO pending the corrected
full metrics** (no GO declared on partial evidence). Owner action: re-run
`python3 scripts/research/verify_sharadar_coverage.py --api-key "$NASDAQ_DATA_LINK_API_KEY" --sample-size 100`
(key never logged) and regenerate the report; the direction is strongly positive.

---

## Addendum 2026-06-10c — Gate CLOSED PASS

Re-run with the paid key after the scoring fix (`e4b6201`):

- `--sample-size 100` → **complete_count = 100, complete_pct = 1.0,
  median_coverage_pct = 0.999.**

**FR-067 Stage 0 gate: PASS / CLOSED.** Sharadar is approved as the canonical PIT
price/security-history source for **FR-068 Phase 1**. Recommendation A
(BUY_ONE_MONTH_NOW) is realized and validated. Standing caveats: Sharadar
provides **no S&P 600/Russell membership** (small-cap membership uses a PIT
market-cap band or supplemental source) and **no analyst consensus** (Cygnus v1
EPS-surprise-vs-consensus remains separately blocked). FR-068 Phase 1 builds the
security-existence PIT foundation; strategy rebaseline follows in later phases.

---

## Constraints honored

Research-only. Local Mac Studio only. No VM/deploy, execution, model, cron, or
strategy-registry changes. No subscription purchased and no money spent. No API
keys exposed or logged. No holdout backtests run. Artifacts are deterministic.
