# Trading Email Governance Policy

## Overview

The trading platform sends exactly **3 operator-facing emails** per trading day:

1. **Market Conditions** — Overnight context, market regime, volatility backdrop
2. **Pre-Trade Analysis** — Proposed trades, execution status (READY/HALTED/NO_ACTION), risk metadata
3. **Trading Confirmation** — Execution completion summary (orders submitted, filled, rejected)

All internal execution states (PLANNED, READY, HALTED, MISSING_EXECUTION_PAYLOAD) are:
- Recorded in structured artifacts/logs
- **Never** sent as standalone operator emails
- Included in pre-trade-analysis email for context

## Email Types

### Market Conditions Email
- **Trigger**: Scheduled, overnight before market open
- **Content**: Economic news, VIX, sector rotation, market backdrop
- **Audience**: Portfolio managers, risk management
- **Config**: `EMAIL_MARKET_CONDITIONS=1` (default: enabled)

### Pre-Trade Analysis Email
- **Trigger**: Before order execution
- **Content**:
  - Execution status (NO_ACTION | READY | HALTED, with reason if halted)
  - Proposed trades (count, sizing, notional)
  - Risk metrics (turnover, concentration, sleeve allocations)
  - Execution status: all internal states embedded in email body, not separate
- **Audience**: Traders, operations
- **Config**: `EMAIL_PRETRADE=1` (default: enabled)
- **Note**: This is the ONLY email sent for execution events

### Trading Confirmation Email
- **Trigger**: After orders submitted to broker
- **Content**:
  - Orders submitted (qty, symbol, price, status)
  - Fills/rejections/cancellations
  - Net delta to portfolio
- **Audience**: Traders, settlement
- **Config**: `EMAIL_TRADING_CONFIRMATION=1` (default: enabled)

## Execution Status Model

The execution engine returns status reflecting execution readiness:

### NO_ACTION
- No trades proposed after filtering
- OR execution disabled (market closed, weekend, plan-only mode)
- OR all proposed trades filtered out (min notional, zero shares, etc.)
- **Email behavior**: Included in pre-trade-analysis, no separate email

### READY
- Executable trades exist
- Market is open
- Portfolio constraints satisfied
- **Email behavior**: Included in pre-trade-analysis email before execution

### HALTED
- Cannot execute due to blocker
- Examples: market closed, price stale, risk breach, reconciliation mismatch
- **Email behavior**: Included in pre-trade-analysis with halt reason inline
- **Note**: HALTED is NEVER a standalone email — always embedded in pre-trade-analysis

## Suppressed States

The following internal states **NEVER** generate standalone emails:

| State | Reason |
|-------|--------|
| PLANNED | Pre-execution planning mode; not ready to operationalize |
| READY | Internal execution signal; operationalized in pre-trade-analysis email |
| HALTED | Blocker state; included in pre-trade-analysis with reason |
| MISSING_EXECUTION_PAYLOAD | System error; logged in artifact, not escalated to email |
| SKIPPED_WEEKEND | Operational timing; expected behavior |
| DROPPED_ZERO_SHARES | Filter outcome; logged in execution summary |
| DROPPED_MIN_NOTIONAL | Filter outcome; logged in execution summary |

## Configuration

### Environment Variables

```bash
# Email type enablement (default: all 1)
EMAIL_MARKET_CONDITIONS=1          # Enable market conditions email
EMAIL_PRETRADE=1                   # Enable pre-trade analysis email
EMAIL_TRADING_CONFIRMATION=1       # Enable trading confirmation email
EMAIL_INTERNAL_DEBUG=0             # Enable internal diagnostic emails (testing only)

# Global email control
ENABLE_EMAIL=1                     # Master email switch; 0 disables all outbound
EMAIL_DRY_RUN=0                    # 1 = write artifacts only, no SMTP
EMAIL_STRICT=0                     # 1 = SMTP errors are fatal; 0 = warn only

# Email credentials
EMAIL_SENDER=operations@company.com
EMAIL_APP_PASSWORD=<oauth_token>
EMAIL_RECIPIENT=traders@company.com
```

### Code-Based Configuration

Email governance decisions are made in `core/email_governance.py`:

```python
from core.email_governance import should_email_pre_trade_status, suppress_internal_state_email

# Check if this state should be suppressed
if suppress_internal_state_email("PLANNED"):
    # Don't send email; record in artifact only
    logger.info("Suppressed internal state email: PLANNED")
    return

# Check if email type is enabled
if not should_email_pre_trade_status("READY"):
    logger.info("Email governance: pre-trade analysis email disabled")
    return
```

## Artifact Contracts

### Execution Payload (`outputs/execution_email/{TRADE_DATE}.json`)

Contains all execution metadata. Email templates read this to generate subject/body.

```json
{
  "trade_date": "2026-03-09",
  "run_id": "20260309T093456Z_paper_1",
  "mode": "PAPER",
  "execution_status": "NO_ACTION|READY|HALTED",
  "halt_reason": "null|MARKET_CLOSED|PRICE_STALE|...",
  "proposed_trades": [
    {"symbol": "AAPL", "side": "BUY", "shares": 100, "price": 120.50, "notional": 12050}
  ],
  "trades": [...],
  "order_ids": [],
  "pricing_asof": "2026-03-07",
  "market_status": "CLOSED|PRE|OPEN|CLOSED",
  "risk_metadata": {...}
}
```

### Latest Run Pointer (`outputs/latest_run.json`)

Canonical pointer to current trading day's run artifacts. Both reporting and execution read this before operating.

```json
{
  "run_id": "20260309T093456Z_paper_1",
  "trade_date": "2026-03-09",
  "mode": "PAPER",
  "run_root": "outputs/runs/20260309T093456Z_paper_1/",
  "status": "success",
  "created_at": "2026-03-09T13:34:56Z"
}
```

## Implementation Details

### Email Governance Layer

**File**: `core/email_governance.py`

Key classes and functions:
- `EmailConfig`: Loads configuration from environment
- `EmailEvent`: Represents an email with decision logic
- `suppress_internal_state_email(state: str) -> bool`: Returns True if state should NOT generate email
- `should_email_pre_trade_status(status: str, reason: str | None) -> bool`: Checks if email is enabled
- `normalize_pre_trade_status(...) -> str`: Maps execution conditions to NO_ACTION/READY/HALTED

### Execution Email Sender

**File**: `daily_trade_execution_email.py`

Flow:
1. Load execution payload from `outputs/execution_email/{TRADE_DATE}.json`
2. If missing, create HALTED artifact (not sent as email)
3. Build email subject/body from payload
4. **Governance check**: If `suppress_internal_state_email(status)` → return early, no email
5. **Email check**: If `not should_email_pre_trade_status(status)` → return early, no email
6. Send email via SMTP (or dry-run if configured)

### Canonical Run Coordination

**File**: `core/run_pointer.py`

Functions:
- `write_latest_run_pointer(run_id, trade_date, mode, run_root, status)`: Write pointer
- `read_latest_run_pointer()`: Read current run metadata
- `get_canonical_run_root()`: Extract run path
- `is_pointer_fresh(trade_date)`: Check pointer validity

## Migration Path

### Phase 1: Email Governance (Current)
- ✅ Email governance module deployed
- ✅ Execution email sender gated by governance
- ✅ Configuration via environment variables
- ⏳ Testing and validation

### Phase 2: Canonical Run Coordination
- Create unified latest_run.json across all subsystems
- Update daily_quant_report.py to write pointer on completion
- Update reporting to read latest_run.json before generating report
- Verify both execution and reporting use same artifact source

### Phase 3: Workflow Automation
- Update GitHub Actions workflow to handle email governance
- Add summary lines to workflow logs: [PRETRADE_SUMMARY], [EXECUTION_SUMMARY]
- Integrate email governance env vars into workflow

### Phase 4: Monitoring and Documentation
- Add monitoring for suppressed emails
- Document email governance policy for operations
- Create runbook for email troubleshooting
- Update incident response procedures

## Troubleshooting

### Email not being sent but expected

1. Check `ENABLE_EMAIL` environment variable
   ```bash
   echo $ENABLE_EMAIL  # Should be 1
   ```

2. Check specific email type setting
   ```bash
   echo $EMAIL_PRETRADE  # For pre-trade analysis
   ```

3. Check suppression logic
   ```bash
   # Look for log line:
   # [EXECUTION_EMAIL] suppressed internal state email: status=PLANNED
   ```

4. Check governance configuration
   ```python
   from core.email_governance import EmailConfig
   config = EmailConfig()
   print(config)  # Should show enabled=True for your email type
   ```

### Email being sent unexpectedly

1. Check execution status in payload
   ```bash
   cat outputs/execution_email/$(date +%Y-%m-%d).json | jq .execution_status
   ```

2. Verify it's not a suppressed state
   ```python
   from core.email_governance import suppress_internal_state_email
   print(suppress_internal_state_email("YOUR_STATUS"))  # Should be False to allow email
   ```

3. Check email configuration
   ```bash
   env | grep EMAIL  # All email-related env vars
   ```

## References

- **File**: `core/email_governance.py` — Configuration and decision logic
- **File**: `core/run_pointer.py` — Canonical run coordination
- **File**: `daily_trade_execution_email.py` — Execution email sender
- **File**: `.github/workflows/daily-alpaca-paper.yml` — Workflow integration
- **File**: `Tests/test_email_governance.py` — Governance tests
- **File**: `Tests/test_run_pointer.py` — Run pointer tests
