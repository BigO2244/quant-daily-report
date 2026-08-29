# HYP-2026-004 Forward Options Proxy Infrastructure

Governance: `RESEARCH_ONLY` / `NON_EXECUTIONAL` / `STANDALONE_AUTOMATION`

Storage rule: all collection and maturation commands run on `caerus-vm` from
`/mnt/disks/alpha-lab/alpha-lab-project`. The matching Mac output tree is
rollback-only. See `../DATA_STORAGE_GOVERNANCE.md` for access and integrity
rules.

This package collects free current option-chain snapshots through yfinance and
constructs an equity-only research proxy for HYP-2026-004. It exists to decide
whether purchasing exchange-grade historical data is worth further review.

It does **not** satisfy the frozen HYP-2026-004 data contract. It has no trade
aggressor side, trade-time NBBO, exchange and condition codes, quote sizes, or
OCC deliverable history. Its artifacts cannot support an alpha, promotion, or
capital-allocation claim.

## Safety boundary

- No broker imports or API calls.
- No equity or option order objects.
- No live, paper, allocation, or execution integration.
- No production scheduler, cron, service, or deployment hook.
- No strategy-registry entry or strategy identity.
- All generated files are constrained to
  `outputs/research/alpha_lab/options_proxy_forward/`.

The approved recurrence is a Codex local automation, separate from Caerus
production cron. The active weekday observation runs at 16:20 ET; separate
read-only day-one and week-one reviews inspect accumulation and health. The
command itself remains session-gated and idempotent.

## Data flow

1. `collect` stores an immutable current-chain snapshot and manifest.
2. `build` constructs auditable proxy features and hypothetical research target
   weights. It needs a prior observation to calculate IV-skew change and fails
   closed on the first day.
3. `observe` performs steps 1 and 2 together.
4. `mature` is a separate future-return command. It fetches later unadjusted
   daily bars, evaluates a five-trading-day cohort, and writes a proxy
   scoreboard. Return data never feeds signal construction.
5. `daily` performs a locked, one-observation-per-session run, matures every
   eligible outstanding cohort, and writes a versioned health artifact.
6. `validate-boundary` writes an AST-based attestation proving that the package
   imports no production/trading modules and contains no order-submission calls.

## Commands

Use a Python environment containing the pinned dependencies in
`requirements.txt`, including `yfinance==1.2.0`.

The default configuration pauses 0.5 seconds between underlyings to reduce
pressure on the public endpoint. A failed symbol is recorded in the immutable
snapshot and lowers source coverage; it is never silently imputed.

```bash
python -m projects.alpha_lab.options_proxy.cli --repo-root . validate-boundary
python -m projects.alpha_lab.options_proxy.cli --repo-root . collect
python -m projects.alpha_lab.options_proxy.cli --repo-root . observe
python -m projects.alpha_lab.options_proxy.cli --repo-root . daily
```

To rebuild an already collected snapshot without another network request:

```bash
python -m projects.alpha_lab.options_proxy.cli --repo-root . build \
  --snapshot outputs/research/alpha_lab/options_proxy_forward/snapshots/YYYY-MM-DD/SNAPSHOT_ID/snapshot.json
```

After at least five later trading sessions are available:

```bash
python -m projects.alpha_lab.options_proxy.cli --repo-root . mature \
  --signal outputs/research/alpha_lab/options_proxy_forward/signals/YYYY-MM-DD/SNAPSHOT_ID/signal.json \
  --through-date YYYY-MM-DD
```

For an idempotent batch sweep:

```bash
python -m projects.alpha_lab.options_proxy.cli --repo-root . mature-all \
  --through-date YYYY-MM-DD

python -m projects.alpha_lab.options_proxy.cli --repo-root . maturation-status \
  --through-date YYYY-MM-DD
```

`maturation-status` reports every collected cohort's target count, observed and
remaining holding sessions, earliest maturity date, and terminal status. A
five-session horizon means a signal observed on Thursday 2026-07-16 first
becomes mature after Thursday 2026-07-23; the decision session itself is not a
forward-return session.

The daily command uses a fail-closed NYSE calendar sourced for 2026–2028. It
skips weekends and holidays, moves the decision gate to 12:45 ET on listed
1:00 p.m. early closes, retries each public-source symbol once, prevents
overlapping runs with a research-local lock, and never creates a second
adequately covered observation for the same session. The calendar must be
reviewed against the official NYSE schedule before 2029.
Calendar source: [NYSE Holidays & Trading Hours](https://www.nyse.com/trade/hours-calendars).

The scoreboard summarizes overlapping five-day cohorts with means and hit
rates. It deliberately does not compound overlapping cohort returns or label
them portfolio NAV.

## Frozen proxy construction

The infrastructure records these current-chain proxies:

- Black-Scholes-delta-weighted call-versus-put contract-volume imbalance;
- call-versus-put open-interest imbalance;
- volume-weighted call/put strike displacement relative to spot; and
- one-observation change in a 25-delta/30-day call-minus-put IV-skew proxy.

Component percentile ranks are calculated within the frozen current-sector map
in `config.json`. That map is acceptable for this forward proxy start date but
is explicitly not historical PIT sector evidence.

Only contracts with 15–60 DTE, positive non-crossed bid/ask, usable volume and
open interest, positive IV, and approximate absolute delta from 0.20–0.80 are
eligible. The proxy score uses the frozen HYP-2026-004 50/30/20 weights, while
preserving an explicit field that the underlying components are not equivalent
to trade-classified signed flow.

The top decile is written as equal-weight **research targets**, capped at ten
names and 10% per name. Residual weight remains cash. These are hypothetical
analytics fields—not orders, positions, or executable targets.

## Review interpretation

The scoreboard remains `INSUFFICIENT_OBSERVATIONS` before 60 matured cohorts.
At 60 it becomes `READY_FOR_PRELIMINARY_SPEND_REVIEW`, not `PASS`. It never
authorizes purchasing data, promotion, paper trading, or live trading.
