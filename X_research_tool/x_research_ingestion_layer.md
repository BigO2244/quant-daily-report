# X Research Ingestion Layer

## Overview

A Python-based tool that monitors X (Twitter) for posts matching configured investment themes, then ingests and structures them as Markdown files in your IDE workspace. The goal is to create a lightweight, local research feed you can annotate, query, and build on — without leaving your development environment.

---

## Architecture

```
┌─────────────────────┐
│  Config / Themes    │  (topics.yaml)
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   X API v2 Client   │  Bearer Token auth, Search endpoint
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Ingestion Engine  │  Deduplicate, filter, score
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Markdown Writer   │  One .md file per theme per run
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  IDE Workspace      │  research/YYYY-MM-DD/<theme>.md
└─────────────────────┘
```

---

## Components

### 1. `topics.yaml` — Theme Configuration

Defines the investment themes to track. Each theme has a name, search query, and optional filters.

```yaml
themes:
  - name: ai_infrastructure
    query: "(AI infrastructure OR GPU cloud OR inference compute) -is:retweet lang:en"
    min_likes: 10
    min_followers: 500

  - name: energy_transition
    query: "(nuclear energy OR grid storage OR LNG OR offshore wind) -is:retweet lang:en"
    min_likes: 5
    min_followers: 200

  - name: biotech_catalysts
    query: "(FDA approval OR phase 3 OR PDUFA OR clinical trial) biotech -is:retweet lang:en"
    min_likes: 20
    min_followers: 1000
```

**Supported filter fields:**
- `min_likes` — minimum like count to include a post
- `min_followers` — minimum author follower count
- `min_retweets` — minimum retweet count
- `exclude_accounts` — list of handles to ignore

---

### 2. `ingestion_engine.py` — Core Fetcher

Handles X API v2 calls using the `tweepy` library.

**Key behaviours:**
- Uses `GET /2/tweets/search/recent` (7-day window, free tier) or `search/all` (Academic/Pro)
- Stores the newest `since_id` per theme in `state.json` to avoid re-ingesting seen posts
- Pulls tweet fields: `id`, `text`, `author_id`, `created_at`, `public_metrics`, `entities`
- Expands author info via `expansions=author_id` for handle, name, follower count
- Rate limit aware: respects `x-rate-limit-remaining` header and sleeps accordingly

**State file (`state.json`):**
```json
{
  "ai_infrastructure": { "since_id": "1234567890123456789", "last_run": "2026-03-01T18:00:00Z" },
  "energy_transition": { "since_id": "9876543210987654321", "last_run": "2026-03-01T18:00:00Z" }
}
```

---

### 3. `markdown_writer.py` — Output Formatter

Writes one Markdown file per theme per run into the configured output directory.

**Output path pattern:**
```
research/
  2026-03-02/
    ai_infrastructure.md
    energy_transition.md
    biotech_catalysts.md
```

**Markdown file structure:**

```markdown
# AI Infrastructure — X Research Digest
> Ingested: 2026-03-02 09:00 UTC | Posts: 14 | Query: `(AI infrastructure OR GPU cloud...)`

---

## [@sama](https://x.com/sama) · Sam Altman
**2026-03-01 22:14 UTC** | ♥ 4,312 | 🔁 892 | 👥 3.2M followers

> The cost of inference is going to zero faster than anyone expected. This changes everything about how you build products.

🔗 https://x.com/sama/status/...

---

## [@jimcramer](https://x.com/jimcramer) · Jim Cramer
...
```

Each post block contains:
- Author handle + display name (linked to profile)
- Timestamp, likes, retweets, follower count
- Full post text in a blockquote
- Direct link to the original post

---

### 4. `main.py` — Entry Point

Orchestrates a full ingestion run.

```python
python main.py                    # ingest all themes
python main.py --theme ai_infra  # single theme
python main.py --dry-run          # print without writing files
python main.py --since 2026-03-01 # override since date
```

---

## File & Folder Structure

```
x_research_ingestion/
├── main.py
├── ingestion_engine.py
├── markdown_writer.py
├── config/
│   └── topics.yaml
├── state/
│   └── state.json          # auto-managed, do not edit manually
├── research/               # ← output lands here, open in IDE
│   └── YYYY-MM-DD/
│       └── <theme>.md
├── requirements.txt
└── .env                    # X API credentials (gitignored)
```

---

## X API Credentials

Store in `.env` (never commit to git):

```env
X_BEARER_TOKEN=your_bearer_token_here
X_API_KEY=your_api_key
X_API_SECRET=your_api_secret
X_ACCESS_TOKEN=your_access_token
X_ACCESS_TOKEN_SECRET=your_access_token_secret
```

Load via `python-dotenv`. Bearer Token alone is sufficient for read-only search.

**API Tier requirements:**
- **Basic (free):** `search/recent` only — last 7 days, 10 req/15 min
- **Pro ($100/mo):** `search/all` — full archive, higher rate limits

---

## Dependencies (`requirements.txt`)

```
tweepy>=4.14.0
python-dotenv>=1.0.0
pyyaml>=6.0.1
```

Install: `pip install -r requirements.txt`

---

## Deduplication Strategy

- Post IDs are tracked in `state.json` via `since_id` (X's native cursor)
- On each run, only posts newer than `since_id` are fetched — no duplicate writes
- If a theme's `.md` file already exists for today, new posts are **appended**, not overwritten

---

## Scoring / Ranking (Optional Enhancement)

Posts within each digest can be ranked by an engagement-weighted score:

```
score = likes + (retweets * 3) + (replies * 2)
```

The Markdown writer sorts by score descending so the highest-signal posts appear first.

---

## Running on a Schedule

To auto-ingest every morning, add a cron job (Mac/Linux):

```cron
0 8 * * * cd /path/to/x_research_ingestion && python main.py >> logs/cron.log 2>&1
```

Or use the Cowork **schedule skill** to set this up via Claude.

---

## Implementation Plan

Build order for a working v1:

1. **`config/topics.yaml`** — define 2–3 starter themes
2. **`.env`** — add Bearer Token
3. **`ingestion_engine.py`** — implement single-theme fetch with tweepy, print results
4. **`state/state.json`** — initialise empty, wire up `since_id` tracking
5. **`markdown_writer.py`** — render one theme's posts to a `.md` file
6. **`main.py`** — loop over all themes, call engine + writer, handle `--theme` flag
7. **Test run** — `python main.py --dry-run` to verify output
8. **Cron / schedule** — automate daily ingestion

---

## Future Enhancements

- **Sentiment tagging** — run a local LLM or call Claude API to label each post (bullish / bearish / neutral)
- **Ticker extraction** — regex or NLP to pull `$TSLA`, `$NVDA` etc. from post text and index by symbol
- **Watchlist integration** — cross-reference mentioned tickers against a personal watchlist
- **Digest summary** — Claude-generated one-paragraph summary at the top of each theme file
- **Slack / email push** — send digest to yourself on ingest completion
- **SQLite backing store** — persist all posts for historical querying alongside the Markdown view
