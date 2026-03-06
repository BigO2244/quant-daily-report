# Quant Research Agent

Nightly research digest agent. Ingests arXiv papers, earnings/macro data,
and news feeds — scores each item against live strategy context using Claude —
delivers a structured email digest via Outlook (Microsoft Graph API).

## Isolation guarantee

This project has zero imports from the production Caerus codebase.
It is a standalone agent that runs independently. Its GitHub Actions workflow
(`research-digest.yml`) uses a dedicated set of secrets prefixed with `DIGEST_`
and will never impact the trading execution workflows.

---

## Setup

```bash
cd quant_research_agent
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your API keys (see Environment Variables below)
```

## Configuration

Edit `config/strategy_context.yaml` to define your watchlist, investment themes,
and scoring priorities. The agent passes this context to Claude on every run.

---

## Usage

```bash
# Full nightly run (ingest → analyze → email)
python main.py

# Dry run: ingest and score, print digest, do not send email
python main.py --dry-run

# Send a test email only — verifies Graph credentials without a full pipeline run
python main.py --send-test

# Save digest HTML + JSON to a directory (in addition to sending)
python main.py --output-dir /path/to/outputs

# Single ingestor only
python main.py --source arxiv
python main.py --source earnings
python main.py --source macro
python main.py --source news

# Skip deduplication (re-surfaces all items)
python main.py --no-dedup
```

### Smoke test (standalone)

To verify email credentials without touching the full pipeline:

```bash
python scripts/smoke_test_email.py             # sends a real test email
python scripts/smoke_test_email.py --dry-run   # validates creds format only
python scripts/smoke_test_email.py --to you@example.com
```

---

## Architecture

```
config/strategy_context.yaml   <- investment themes, watchlist, scoring weights
        |
        v
ingestors/                     <- arxiv, earnings, macro, news
        |
        v (List[ResearchItem])
agent/analyzer.py              <- Claude scores each item vs strategy context
        |
        v (List[ScoredItem])
agent/email_formatter.py       <- renders HTML digest + plain-text fallback
        |
        v (DigestEmail)
delivery/graph_email.py        <- sends multipart/alternative MIME via MS Graph API
        |
        v
store/dedup_store.py           <- seen-IDs JSON, prevents re-surfacing same items
```

**Email format:** HTML body (embedded, not attached) with a plain-text fallback
in the same MIME envelope. All modern email clients render the HTML; text-only
clients fall back to the plain version.

**Deduplication:** `store/dedup_store.py` tracks seen item IDs in a local JSON
file so re-runs never surface the same item twice. In GitHub Actions the store
is cached using `actions/cache` with a rolling 7-day key.

---

## Running tests

```bash
pytest tests/ -v
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `FRED_API_KEY` | Yes | FRED macroeconomic data |
| `MS_GRAPH_CLIENT_ID` | Yes (email) | Azure app client ID |
| `MS_GRAPH_CLIENT_SECRET` | Yes (email) | Azure app client secret |
| `MS_GRAPH_TENANT_ID` | Yes (email) | Azure tenant ID |
| `MS_GRAPH_USER_EMAIL` | Yes (email) | Sender/recipient mailbox (same address for app-only auth) |

---

## Phase 2 — Microsoft Graph Setup (step-by-step)

### 1. Register an Azure App

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**
2. Name it `quant-research-agent` (single tenant)
3. Note the **Application (client) ID** and **Directory (tenant) ID**

### 2. Grant API permissions

In the app registration → **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**:
- `Mail.Send`

Then **Grant admin consent** for your tenant.

### 3. Create a client secret

**Certificates & secrets** → **New client secret** → copy the **Value** (only shown once).

### 4. Fill in `.env`

```env
ANTHROPIC_API_KEY=sk-ant-...
FRED_API_KEY=your_fred_key
MS_GRAPH_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_GRAPH_CLIENT_SECRET=your_secret_value
MS_GRAPH_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_GRAPH_USER_EMAIL=you@yourdomain.com
```

> **Important:** Add `.env` to `.gitignore`. Never commit secrets.

### 5. Verify

```bash
python main.py --send-test
# or
python scripts/smoke_test_email.py
```

### Troubleshooting

| Error | Fix |
|---|---|
| `MS Graph credentials incomplete` | One or more env vars is empty — check `.env` |
| `Graph /sendMail failed [401]` | Client secret expired or wrong, or app lacks `Mail.Send` permission |
| `Graph /sendMail failed [403]` | Admin consent not granted for `Mail.Send` |
| `Graph /sendMail failed [404]` | `MS_GRAPH_USER_EMAIL` mailbox not found in tenant |
| `Failed to obtain MS Graph token` | `TENANT_ID` or `CLIENT_ID` wrong, or tenant firewall blocking OAuth endpoint |

---

## Phase 3 — Nightly GitHub Actions

The workflow file is at `.github/workflows/research-digest.yml`.

### Secrets to add (repo Settings → Secrets and variables → Actions)

Use the `DIGEST_` prefix to keep these completely separate from trading secrets:

| Secret name | Value |
|---|---|
| `DIGEST_ANTHROPIC_API_KEY` | Your Anthropic API key |
| `DIGEST_FRED_API_KEY` | Your FRED API key |
| `DIGEST_MS_GRAPH_CLIENT_ID` | Azure app client ID |
| `DIGEST_MS_GRAPH_CLIENT_SECRET` | Azure app client secret |
| `DIGEST_MS_GRAPH_TENANT_ID` | Azure tenant ID |
| `DIGEST_MS_GRAPH_USER_EMAIL` | Sender/recipient email |

### Schedule

The workflow runs Monday–Friday at **6:00 AM ET** (dual cron entries handle DST).
You can also trigger it manually from the GitHub Actions UI (`workflow_dispatch`).

### Artifacts

Each run saves to `outputs/runs/<run_id>/`:
- `digest_<date>.html` — the full rendered HTML digest
- `digest_<date>.json` — JSON summary (item count, scores, sources)
- `digest.log` — full pipeline log
- `meta.json` — run ID, date, git SHA, workflow URL

Artifacts are retained for **30 days** and downloadable from the Actions tab.

### Dedup cache

The `seen_ids.json` dedup store is cached with a rolling 7-day `actions/cache`
key so the agent never re-surfaces the same arXiv paper or earnings item across
runs. The cache is written back after each successful run.

### Isolation

The digest workflow uses `continue-on-error: true` at both the job and key step
level. A digest failure produces a warning annotation in GitHub — it never
touches the trading workflow outputs or secrets.

---

## Local cron / launchd (alternative to GitHub Actions)

If you prefer running locally:

```bash
# macOS launchd plist — save as ~/Library/LaunchAgents/com.caerus.research-digest.plist
# Then: launchctl load ~/Library/LaunchAgents/com.caerus.research-digest.plist
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.caerus.research-digest</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/quant_research_agent/.venv/bin/python</string>
    <string>/path/to/quant_research_agent/main.py</string>
    <string>--output-dir</string>
    <string>/path/to/outputs/digest</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>6</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/research-digest.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/research-digest.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key><string>sk-ant-...</string>
    <key>FRED_API_KEY</key><string>your_fred_key</string>
    <key>MS_GRAPH_CLIENT_ID</key><string>your_client_id</string>
    <key>MS_GRAPH_CLIENT_SECRET</key><string>your_secret</string>
    <key>MS_GRAPH_TENANT_ID</key><string>your_tenant_id</string>
    <key>MS_GRAPH_USER_EMAIL</key><string>you@domain.com</string>
  </dict>
</dict>
</plist>
```
