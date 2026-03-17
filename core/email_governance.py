"""
Email governance for daily trading workflow.

Controls which events generate operator-facing emails and which are suppressed.
Three approved operator-facing emails only:
  1. Market Conditions — overnight context, regime, volatility
  2. Pre-Trade Analysis — proposed trades + final status (READY|HALTED|NO_ACTION)
  3. Trading Confirmation — execution result after orders submitted

All internal states (PLANNED, READY, HALTED) are written to structured
artifacts/logs instead of generating standalone emails.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class EmailEvent:
    """Represents an email event in the workflow."""
    event_type: str  # 'market_conditions' | 'pre_trade_analysis' | 'trading_confirmation' | 'internal_debug'
    subject: str
    body_text: str
    body_html: Optional[str] = None
    payload: Optional[dict] = None
    
    def should_send(self) -> bool:
        """Return True if this email should be sent based on configuration."""
        config = EmailConfig()
        
        if self.event_type == 'market_conditions':
            return config.send_market_conditions
        elif self.event_type == 'pre_trade_analysis':
            return config.send_pre_trade_analysis
        elif self.event_type == 'trading_confirmation':
            return config.send_trading_confirmation
        elif self.event_type == 'internal_debug':
            return config.send_internal_debug
        return False


class EmailConfig:
    """Email configuration from environment variables."""
    
    def __init__(self):
        """Load email config from environment with documented defaults."""
        # Default production behavior: 3 operator-facing emails, no internal debug
        self.send_market_conditions = self._get_bool('EMAIL_MARKET_CONDITIONS', default=True)
        self.send_pre_trade_analysis = self._get_bool('EMAIL_PRETRADE', default=True)
        self.send_trading_confirmation = self._get_bool('EMAIL_TRADING_CONFIRMATION', default=True)
        self.send_internal_debug = self._get_bool('EMAIL_INTERNAL_DEBUG', default=False)
        
        # Email can still be globally disabled for testing
        self.enabled = self._get_bool('ENABLE_EMAIL', default=True)
    
    @staticmethod
    def _get_bool(env_var: str, default: bool = False) -> bool:
        """Parse boolean environment variable."""
        value = os.getenv(env_var, '').strip().lower()
        if value in ('1', 'true', 'yes', 'y', 'on'):
            return True
        if value in ('0', 'false', 'no', 'n', 'off'):
            return False
        if value == '':
            return default
        return default
    
    def __repr__(self) -> str:
        return (
            f"EmailConfig("
            f"enabled={self.enabled}, "
            f"market_conditions={self.send_market_conditions}, "
            f"pre_trade={self.send_pre_trade_analysis}, "
            f"confirmation={self.send_trading_confirmation}, "
            f"debug={self.send_internal_debug})"
        )


def should_email_pre_trade_status(
    execution_status: str,
    halt_reason: Optional[str] = None,
) -> bool:
    """
    Check if pre-trade status should generate an email.
    
    Only pre_trade_analysis event type should be emailed (once).
    Internal states (PLANNED, READY, HALTED) should NOT generate emails—
    they are recorded in structured artifacts instead.
    
    Args:
        execution_status: One of READY, PLANNED, HALTED, SKIPPED_WEEKEND, etc.
        halt_reason: Reason for halt if status is HALTED
    
    Returns:
        True if pre-trade analysis email should be sent.
    """
    config = EmailConfig()
    if not config.enabled or not config.send_pre_trade_analysis:
        return False
    
    # All statuses go into the single pre-trade-analysis email
    # Internal state differences are reflected in the email body
    return True


def suppress_internal_state_email(state: str) -> bool:
    """
    Return True if this internal state should NOT generate an email.
    
    Internal executor states like PLANNED, READY, HALTED, MISSING_EXECUTION_PAYLOAD
    are never emailed directly. Instead, they are:
    - Recorded in execution_payload.json
    - Reflected in pre-trade-analysis email (once)
    - Written to structured logs/artifacts
    
    Args:
        state: Internal execution state string
    
    Returns:
        True if this state should be suppressed (no email generated)
    """
    suppressed_states = {
        'PLANNED',
        'READY',
        'HALTED',
        'MISSING_EXECUTION_PAYLOAD',
        'SKIPPED_WEEKEND',
        'DROPPED_ZERO_SHARES',
        'DROPPED_MIN_NOTIONAL',
    }
    return state.upper() in suppressed_states


def normalize_pre_trade_status(
    execution_enabled: bool,
    proposed_order_count: int,
    executable_order_count: int,
    halt_reason: Optional[str] = None,
    execution_status: str = 'UNKNOWN',
) -> str:
    """
    Normalize internal execution_status to operator-facing pre-trade status.
    
    Maps execution states to three outcomes:
      NO_ACTION   — no trades are proposed after constraints
      READY       — trades exist and can be executed
      HALTED      — execution cannot proceed (blocker or conditions)
    
    Args:
        execution_enabled: Whether execution is allowed (market open, not weekend, etc.)
        proposed_order_count: Number of proposed orders before filters
        executable_order_count: Number of executable orders after filters
        halt_reason: Why execution was halted (if applicable)
        execution_status: Internal status from run_paper_day
    
    Returns:
        One of 'NO_ACTION', 'READY', 'HALTED'
    """
    # If explicitly halted with a reason, report HALTED
    if execution_status == 'HALTED' or halt_reason:
        return 'HALTED'
    
    # If no proposed orders, it's NO_ACTION
    if proposed_order_count == 0:
        return 'NO_ACTION'
    
    # If proposed exist but none are executable, it's NO_ACTION
    if executable_order_count == 0:
        return 'NO_ACTION'
    
    # If execution is disabled (market closed, weekend, plan-only), it's NO_ACTION
    if not execution_enabled:
        return 'NO_ACTION'
    
    # Otherwise, trades exist and can be executed
    return 'READY'


def get_email_summary_line(
    run_id: str,
    pre_trade_status: str,
    proposed_orders: int,
    executable_orders: int,
    halt_reason: Optional[str] = None,
) -> str:
    """
    Generate a machine-readable summary line for logging.
    
    Example:
        [PRETRADE_SUMMARY] run_id=20260309T123456Z mode=PAPER status=READY
                          proposed=5 executable=5
    """
    reason_suffix = f' reason={halt_reason}' if halt_reason else ''
    return (
        f'[PRETRADE_SUMMARY] run_id={run_id} status={pre_trade_status} '
        f'proposed={proposed_orders} executable={executable_orders}{reason_suffix}'
    )
