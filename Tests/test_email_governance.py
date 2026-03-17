"""
Tests for email governance layer.

Validates that:
1. Internal states (PLANNED, READY, HALTED) do NOT generate emails
2. Only pre-trade-analysis event type should send emails for execution status
3. Email configuration controls behavior via environment variables
"""

import os
import pytest
from core.email_governance import (
    EmailConfig,
    EmailEvent,
    should_email_pre_trade_status,
    suppress_internal_state_email,
    normalize_pre_trade_status,
    get_email_summary_line,
)


class TestEmailConfig:
    """Test email configuration loading from environment."""
    
    def test_default_all_emails_enabled(self):
        """By default, all three operator-facing emails should be enabled."""
        # Clear any env vars that might interfere
        for key in ['EMAIL_MARKET_CONDITIONS', 'EMAIL_PRETRADE', 'EMAIL_TRADING_CONFIRMATION', 'EMAIL_INTERNAL_DEBUG']:
            os.environ.pop(key, None)
        
        config = EmailConfig()
        assert config.enabled is True
        assert config.send_market_conditions is True
        assert config.send_pre_trade_analysis is True
        assert config.send_trading_confirmation is True
        assert config.send_internal_debug is False
    
    def test_disable_specific_email_type(self):
        """Should be able to disable individual email types."""
        os.environ['EMAIL_PRETRADE'] = '0'
        try:
            config = EmailConfig()
            assert config.send_pre_trade_analysis is False
            assert config.send_market_conditions is True  # Others still enabled
        finally:
            os.environ.pop('EMAIL_PRETRADE', None)
    
    def test_enable_debug_email(self):
        """Should be able to enable internal debug emails for testing."""
        os.environ['EMAIL_INTERNAL_DEBUG'] = '1'
        try:
            config = EmailConfig()
            assert config.send_internal_debug is True
        finally:
            os.environ.pop('EMAIL_INTERNAL_DEBUG', None)

    @pytest.mark.parametrize("raw_value", ["1", "true", "TRUE", "yes", "on", "y"])
    def test_get_bool_true_tokens_enable_flag(self, raw_value):
        os.environ['EMAIL_PRETRADE'] = raw_value
        try:
            config = EmailConfig()
            assert config.send_pre_trade_analysis is True
        finally:
            os.environ.pop('EMAIL_PRETRADE', None)

    @pytest.mark.parametrize("raw_value", ["0", "false", "FALSE", "no", "off", "n"])
    def test_get_bool_false_tokens_disable_flag(self, raw_value):
        os.environ['EMAIL_PRETRADE'] = raw_value
        try:
            config = EmailConfig()
            assert config.send_pre_trade_analysis is False
        finally:
            os.environ.pop('EMAIL_PRETRADE', None)


class TestEmailEvent:
    """Test EmailEvent decision logic."""
    
    def test_pretrade_event_sends_when_enabled(self):
        """Pre-trade event should queue for send when governance allows."""
        os.environ['EMAIL_PRETRADE'] = '1'
        try:
            event = EmailEvent(
                event_type='pre_trade_analysis',
                subject='Test',
                body_text='Test body',
            )
            assert event.should_send() is True
        finally:
            os.environ.pop('EMAIL_PRETRADE', None)
    
    def test_pretrade_event_suppressed_when_disabled(self):
        """Pre-trade event should be suppressed when disabled."""
        os.environ['EMAIL_PRETRADE'] = '0'
        try:
            event = EmailEvent(
                event_type='pre_trade_analysis',
                subject='Test',
                body_text='Test body',
            )
            assert event.should_send() is False
        finally:
            os.environ.pop('EMAIL_PRETRADE', None)


class TestSuppressinternalStateEmail:
    """Test detection of internal states that should not generate emails."""
    
    def test_suppressed_states(self):
        """These states should be suppressed from email sending."""
        suppressed = ['PLANNED', 'READY', 'HALTED', 'MISSING_EXECUTION_PAYLOAD']
        for state in suppressed:
            assert suppress_internal_state_email(state) is True
            assert suppress_internal_state_email(state.lower()) is True
    
    def test_custom_states_not_suppressed(self):
        """Unknown states should not be automatically suppressed."""
        # This allows future extensions
        assert suppress_internal_state_email('CUSTOM_STATE') is False


class TestNormalizePreTradeStatus:
    """Test mapping of execution states to operator-facing pre-trade status."""
    
    def test_halt_blocks_execution(self):
        """When halted with reason, status should be HALTED."""
        status = normalize_pre_trade_status(
            execution_enabled=True,
            proposed_order_count=5,
            executable_order_count=5,
            halt_reason='MARKET CLOSED',
        )
        assert status == 'HALTED'
    
    def test_no_proposed_orders_is_no_action(self):
        """When no orders proposed, status is NO_ACTION."""
        status = normalize_pre_trade_status(
            execution_enabled=True,
            proposed_order_count=0,
            executable_order_count=0,
        )
        assert status == 'NO_ACTION'
    
    def test_proposed_but_not_executable_is_no_action(self):
        """When orders proposed but none executable after filters, NO_ACTION."""
        status = normalize_pre_trade_status(
            execution_enabled=True,
            proposed_order_count=5,
            executable_order_count=0,
        )
        assert status == 'NO_ACTION'
    
    def test_execution_disabled_is_no_action(self):
        """When execution disabled (market closed, weekend, etc.), NO_ACTION."""
        status = normalize_pre_trade_status(
            execution_enabled=False,
            proposed_order_count=5,
            executable_order_count=5,
        )
        assert status == 'NO_ACTION'
    
    def test_executable_orders_is_ready(self):
        """When executable orders exist and enabled, status is READY."""
        status = normalize_pre_trade_status(
            execution_enabled=True,
            proposed_order_count=5,
            executable_order_count=5,
        )
        assert status == 'READY'


class TestEmailSummaryLine:
    """Test generation of machine-readable summary lines."""
    
    def test_summary_with_ready_status(self):
        """Summary line should include all relevant metadata."""
        line = get_email_summary_line(
            run_id='20260309T093456Z',
            pre_trade_status='READY',
            proposed_orders=5,
            executable_orders=5,
        )
        assert '[PRETRADE_SUMMARY]' in line
        assert 'run_id=20260309T093456Z' in line
        assert 'status=READY' in line
        assert 'proposed=5' in line
        assert 'executable=5' in line
    
    def test_summary_with_halt_reason(self):
        """Summary should include halt reason when present."""
        line = get_email_summary_line(
            run_id='20260309T093456Z',
            pre_trade_status='HALTED',
            proposed_orders=3,
            executable_orders=0,
            halt_reason='MARKET_CLOSED',
        )
        assert 'reason=MARKET_CLOSED' in line


class TestShouldEmailPreTradeStatus:
    """Test the main governance function for pre-trade emails."""
    
    def test_enabled_by_default(self):
        """Pre-trade email should be enabled by default."""
        for key in ['EMAIL_PRETRADE', 'EMAIL_MARKET_CONDITIONS', 'EMAIL_TRADING_CONFIRMATION']:
            os.environ.pop(key, None)
        
        result = should_email_pre_trade_status('READY')
        assert result is True
    
    def test_respects_global_disable(self):
        """If ENABLE_EMAIL=0, no emails should send."""
        os.environ['ENABLE_EMAIL'] = '0'
        try:
            result = should_email_pre_trade_status('READY')
            assert result is False
        finally:
            os.environ.pop('ENABLE_EMAIL', None)
    
    def test_respects_pretrade_setting(self):
        """Should check EMAIL_PRETRADE setting."""
        os.environ['EMAIL_PRETRADE'] = '0'
        try:
            result = should_email_pre_trade_status('READY')
            assert result is False
        finally:
            os.environ.pop('EMAIL_PRETRADE', None)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
