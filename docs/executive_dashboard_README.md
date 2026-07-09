# Caerus Executive Dashboard + Live Kill Switch

Built 2026-07-08. Staged in the repo — **nothing has been deployed to the VM.** Review, then
follow "Deploy" below.

## What this is

A new operator-first dashboard that answers *"what's my risk right now, and what do I do if
something breaks?"* — the question the current evidence/reporting page buries. It reuses the
existing data pipeline unchanged: it reads the same `dashboard-data.json` that
`scripts/refresh_quant_dashboard.py` (DashboardV1Builder) already writes every ~5 min, so it
auto-refreshes with no backend work. No new data dependencies, no external JS/CDN.

### New / changed files
| File | Purpose |
|---|---|
| `web/dashboard/executive.html` | The dashboard. Self-contained (inline CSS/JS, hand-rolled SVG charts). Reads `dashboard-data.json` from the same directory. |
| `scripts/killswitch_service.py` | Server-side kill-switch authority (stdlib only). Writes `CAERUS_LIVE_PILOT_KILL_SWITCH` to `~/.caerus/live_pilot.env`. |
| `deploy/caerus-killswitch.service` | systemd unit for the kill-switch service. |
| `deploy/caerus-killswitch.nginx` | nginx `location` block to expose the API behind basic auth + token injection. |

`web/dashboard/index.html` (the existing detailed "Portfolio Command" view) is **untouched**;
the executive page links to it as the audit/detail view.

## What it shows

**Account toggle (top-left): Paper Book ⇄ Live Pilot.** Both accounts are already in one payload —
paper is `nav`/`positions`/`performance_history`/`terminal.*`; live is `sections.live_pilot.*`.
The whole page re-renders for the selected account.

**Executive KPI strip** (changes per account): NAV + day P&L; since-inception vs SPY (paper);
net exposure; current drawdown with the circuit-breaker level; positions vs regime max; and for
live: equity, capital cap, open/blocking orders, fill rate, reconciliation state.

**Risk & Limits** — the piece that was missing: exposure-vs-cap, drawdown-vs-circuit-breaker,
positions-vs-max, concentration, portfolio scale, each as a "how close to a guardrail" bar. The
thresholds are the *real* ones from the code, not invented:
- `core/risk_controls.py`: `CIRCUIT_BREAKER_PCT = 0.15` → scales exposure to 50% at −15% drawdown.
- `core/growth_engine_v4.py`: `CIRCUIT_BREAKER_DRAWDOWN = −0.25` (live sleeve floor).
- Concentration/single-name levels are labelled **"watch (soft)"** — they are display guides you
  can tune in the `RISK` object at the top of the `<script>`, not enforced limits.

**Performance** (paper): equity indexed vs SPY + drawdown curve. **Positions**, **Operator
Actions** (what needs a decision, blockers flagged), **Governance & Regime**, and live
**Execution & Health** (orders, reconciliation, comparability).

Everything is bound to real fields; missing values render `—` (never fabricated).

## The kill switch

**It is a HALT, not a liquidation.** Engaging writes `CAERUS_LIVE_PILOT_KILL_SWITCH=1`, which
`scripts/cron_live_pilot_execute.sh` checks at the top of every run (`== "1"` → abort + write
blocked gate-state). So the **next scheduled live execution submits no orders.** Existing
positions are left alone — see "Flatten" below.

**The button has no power; the server does.** The dashboard only calls an endpoint. Authority
lives server-side with the trading, so it still works if the browser is flaky.

**Security model (all three required):**
1. Service binds `127.0.0.1` only — never expose port 8787.
2. Engage/disengage **require a bearer token**. Token-less POSTs are refused (401), so a malicious
   page in your browser can't POST to `127.0.0.1:8787` and re-arm/halt trading (it can't set the
   Authorization header cross-origin). This was a real hole in the first draft; it's closed.
3. nginx enforces the existing dashboard basic-auth and **injects the token** so the browser never
   holds it. Requests must go authenticated browser → nginx → service.

State changes also require a typed confirmation (`{"confirm":"ENGAGE"}`) both in the UI and
server-side. Reads are **fail-closed**: an unrecognized flag value displays ENGAGED so the
dashboard can never under-report a halt.

### Flatten (deliberately not automated)
Auto-liquidation is intentionally **not** built in — flattening at the wrong moment can be worse
than halting, and it moves real money. Recommended: engage the kill switch (stops new orders),
then flatten manually in the Alpaca dashboard, or run a separate, explicitly-guarded flatten
script you invoke by hand. If you want, I can draft that as a second gated endpoint (armed only
while the kill switch is engaged, its own confirmation) — but it should be a conscious follow-up,
not a default.

## Preview locally (no deploy)
```bash
cd web/dashboard
python3 -m http.server 8080      # then open http://localhost:8080/executive.html
```
(The kill-switch control shows read-only / disabled until the service is running — expected.)

## Deploy (when you're ready)

1. **Ship the page** (rides the existing dashboard deploy path to `/var/www/caerus-dashboard/`).
   Make it the default view by either linking to `executive.html` or pointing nginx `index` at it;
   `index.html` stays as the detail view.

2. **Kill-switch token**
   ```bash
   mkdir -p ~/.caerus && ( umask 077; echo "CAERUS_KILLSWITCH_TOKEN=$(openssl rand -hex 24)" > ~/.caerus/killswitch.env )
   ```

3. **Service**
   ```bash
   sudo cp deploy/caerus-killswitch.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now caerus-killswitch
   ```

4. **nginx** — paste the token into `deploy/caerus-killswitch.nginx` (the
   `proxy_set_header Authorization "Bearer …"` line), add the `location` block inside the existing
   server in `caerus-dashboard.nginx`, then `sudo nginx -t && sudo systemctl reload nginx`.

5. **Verify**: load `/dashboard/executive.html`, switch to Live, confirm the kill state reads
   "ARMED — trading allowed", engage it (type ENGAGE), confirm `~/.caerus/live_pilot.env` now has
   `CAERUS_LIVE_PILOT_KILL_SWITCH=1` and the banner shows ENGAGED, then disengage.

> Security note: the dashboard is on a bare `http://` IP with basic auth. The kill-switch token +
> localhost binding are safe against the browser-CSRF vector, but putting the whole thing behind
> TLS is the right end-state — this dovetails with the VM hardening already on your roadmap.

## Verification already done
- `executive.html` JS + `killswitch_service.py` syntax-checked.
- Rendered end-to-end against the real `dashboard-data.json` (headless DOM): both accounts, the
  toggle, charts, and every panel bind correctly with **zero runtime errors**; missing fields show
  `—`.
- Kill-switch service tested live: fail-closed reads; other env lines preserved on write; token-less
  POST → 401 (CSRF blocked); wrong token → 401; missing confirm → 400; valid engage → 200 and the
  flag flips; bad route → 404.
- Adversarial code review pass; the issues it found (token fail-open, unvalidated body, XSS on the
  reconciliation field, optimistic post-state) are fixed.

Not done: a pixel screenshot (the sandbox couldn't run a browser engine). Please eyeball the layout
in the local preview before deploying.
