# Caerus — Credential Sourcing & Code-Review Findings

**Created:** 2026-06-24 · **Scope:** how credentials and data are loaded, and the
redundancy that lets them drift. Triggered by the dashboard-refresh auth failure
(stale secret in a duplicate env file).

---

## 1. Credential source map (verified)

The *reader* is already canonical: `brokers/alpaca_broker.py:load_alpaca_env()` reads only
**environment variables** (`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_PAPER`,
`ALPACA_BASE_URL`). The fragmentation is in **what populates those variables**.

| File | Used by | Status |
|------|---------|--------|
| `~/quant-daily-report/.env` (repo root) | All cron wrappers (`set -a; source .env`), `run_precomputed_alpaca_execution.py`, the `_load_dotenv()` copies, `backfill_portfolio_history.py` | **CANONICAL (paper)** — valid keys |
| `~/.caerus/alpaca.env` | Dashboard refresh systemd unit only (`--env-file`) | **OUTLIER — drifted, caused the 401.** Now repointed; slated for removal |
| `~/.caerus/live_pilot.env` | `run_monday_live_pilot.sh` (`ALPACA_PAPER=0`, live endpoint) | **KEEP SEPARATE** — real-money keys, deliberate isolation |
| `quant_research_agent/.env` | `quant_research_agent/main.py` (`load_dotenv`) | Separate concern (research/SMTP/Anthropic); likely overlaps repo `.env`. Lower risk — no broker keys |

**Root cause of the outage:** two paper-credential files (`repo/.env` and `~/.caerus/alpaca.env`)
held different secrets. Trading reads the first (worked); the dashboard refresh read the second
(401, froze the dashboard on the prior day). Classic drift between duplicate sources.

## 2. Loading mechanisms (all populate the env differently)

- `set -a; source "${REPO_ROOT}/.env"` — every `cron_*.sh` wrapper (canonical path).
- `scripts/export_alpaca_broker_snapshot.py:load_env_file(path)` — file loader for `--env-file`.
- `dotenv.load_dotenv(<hardcoded path>)` — `quant_research_agent/main.py`, `…/scripts/test_run.py`,
  `…/scripts/smoke_test_email.py`.
- Bespoke `_load_dotenv(repo_root)` reimplemented in: `send_shadow_cio_report.py`,
  `send_post_close_research_digest.py`, `send_portfolio_learning_review.py`,
  `certify_execution_readiness.py` — **four near-identical copies**, all reading `repo_root/.env`.
- Hand-rolled `open(.env)` parse loops: `run_precomputed_alpaca_execution.py`, `diag_regime_engine.py`.

Important: the four `_load_dotenv` copies and the hand-rolled parsers all read **repo `.env`** — so
they're duplicated *code*, not divergent *sources*. They don't cause drift today, but every copy is a
place a future edit can quietly diverge.

## 3. Data sourcing (the "even data" question) — milder, same shape

`scripts/research/build_dashboard_v1.py` resolves several inputs with "latest" fallbacks rather than
one declared source, e.g. broker snapshot from `outputs/broker/broker_snapshot_latest.json` **or** the
newest `outputs/broker_snapshot/broker_snapshot_*.json`. Whichever exists wins, so a stale or missing
artifact is read without erroring — which is how the cockpit could silently show stale/empty panels.
Not urgent, but the builder's input resolution should be made explicit and fail-loud on staleness.

---

## 4. Changes already applied in the repo (review the diff)

- `deploy/caerus-dashboard-refresh.service` — `--env-file` changed from
  `~/.caerus/alpaca.env` → `~/quant-daily-report/.env` (canonical). One source of truth for paper.
- `scripts/deploy_dashboard_vm.sh` —
  - removed the `~/.caerus/alpaca.env` push (`REMOTE_ENV` + scp block): deploy no longer ships creds.
  - removed the artifact-sync block (was pushing local `outputs/*` to the VM). **The VM produces those
    via its own cron**, so the sync was backwards and would overwrite fresh production data.

These are repo edits only — nothing has been applied to the VM yet.

## 5. How to apply (and the ordering caveat)

**Caveat first:** `deploy_dashboard_vm.sh` overwrites VM scripts with the Mac repo's versions. Do **not**
run a full redeploy until the git-drift check confirms the VM has no uncommitted hand-edits:
`git -C ~/quant-daily-report status --short && git -C ~/quant-daily-report rev-parse --short HEAD`.

**To apply the env-file repoint now, surgically (no full redeploy):**
```
ssh brettolson@alpha-stack-scheduler '
sudo sed -i "s#--env-file /home/brettolson/.caerus/alpaca.env#--env-file /home/brettolson/quant-daily-report/.env#" /etc/systemd/system/caerus-dashboard-refresh.service
sudo systemctl daemon-reload
sudo systemctl start caerus-dashboard-refresh.service
systemctl show caerus-dashboard-refresh.service -p Result -p ExecMainStatus
'
```
Expect `Result=success`, `ExecMainStatus=0`. After that holds for a few cycles, the duplicate file can
be retired: `mv ~/.caerus/alpaca.env ~/.caerus/alpaca.env.retired` (keep the live_pilot.env file).

## 6. Recommended follow-ups (risk-ranked, not yet done)

1. **Retire `~/.caerus/alpaca.env`** once §5 is confirmed stable. (Low risk, high value — removes the
   drift source permanently.)
2. **Single env loader.** Add one helper (e.g. `core/env.py: load_repo_env()`) and replace the four
   `_load_dotenv` copies + two hand-rolled parsers with it. Pure dedup, same source — do it with tests.
   (Low risk; medium effort. Touches execution scripts, so review carefully.)
3. **Point the research agent at the canonical `.env`** (or document why `quant_research_agent/.env`
   is intentionally separate). (Low risk.)
4. **Make `build_dashboard_v1.py` input resolution explicit + fail-loud** on stale/missing artifacts
   instead of silent "latest" fallbacks. (Medium effort; prevents silent stale cockpit data.)
5. **Keep `live_pilot.env` separate** — this is correct and should be preserved through any cleanup.

## 7. Open
- Git-drift check result (VM vs repo) — pending.
- Broader redundancy sweep beyond credentials (duplicate snapshot/glob logic, repeated artifact path
  construction) — flagged, not yet enumerated.
