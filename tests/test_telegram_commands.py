"""Tests for Telegram inbound command handlers (control plane)."""
import asyncio
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.safety import (
    ErrorTracker,
    ExecutionMetrics,
    RiskLimits,
    RiskManager,
    TradingPauseManager,
)


@pytest.fixture
def notifier_and_bot():
    """TelegramNotifier wired to a fake ArbBot stub. Bot client is mocked."""
    with patch('src.notifications.telegram_notifier.Bot'):
        from src.notifications.telegram_notifier import TelegramNotifier

        risk_manager = RiskManager(RiskLimits(), Decimal('100'))
        executor_cfg = SimpleNamespace(simulation_mode=True, dry_run=True)
        executor = SimpleNamespace(config=executor_cfg)
        fake_bot = SimpleNamespace(
            risk_manager=risk_manager,
            pause_manager=TradingPauseManager(),
            metrics=ExecutionMetrics(),
            error_tracker=ErrorTracker(),
            dry_run=True,
            executor=executor,
            paused=False,
            _real_trading_pending_at=None,
            _real_trading_pending_ttl=60.0,
        )
        notifier = TelegramNotifier('tok', 'chat', bot_ref=fake_bot)
        return notifier, fake_bot


def _msg(text: str):
    m = MagicMock()
    m.text = text
    m.answer = AsyncMock()
    return m


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_limits_command(notifier_and_bot):
    n, _ = notifier_and_bot
    msg = _msg('/limits')
    asyncio.run(n._cmd_limits(msg))
    out = msg.answer.call_args[0][0]
    assert 'Risk limits' in out
    assert 'max_trade_usd' in out


def test_set_max_trade_ok(notifier_and_bot):
    n, b = notifier_and_bot
    msg = _msg('/set_max_trade 12')
    asyncio.run(n._cmd_set_max_trade(msg))
    out = msg.answer.call_args[0][0]
    assert out.startswith('✅')
    assert b.risk_manager.limits.max_trade_usd == Decimal('12')


def test_set_max_trade_above_absolute_rejected(notifier_and_bot):
    n, b = notifier_and_bot
    msg = _msg('/set_max_trade 999')
    asyncio.run(n._cmd_set_max_trade(msg))
    out = msg.answer.call_args[0][0]
    assert out.startswith('❌')
    assert b.risk_manager.limits.max_trade_usd != Decimal('999')


def test_set_max_trade_no_arg(notifier_and_bot):
    n, _ = notifier_and_bot
    msg = _msg('/set_max_trade')
    asyncio.run(n._cmd_set_max_trade(msg))
    out = msg.answer.call_args[0][0]
    assert 'Usage' in out


def test_pause_with_minutes(notifier_and_bot):
    n, b = notifier_and_bot
    msg = _msg('/pause 5')
    asyncio.run(n._cmd_pause(msg))
    assert b.pause_manager.is_paused()
    assert msg.answer.call_args[0][0].startswith('⏸️')


def test_pause_no_arg_indefinite(notifier_and_bot):
    n, b = notifier_and_bot
    msg = _msg('/pause')
    asyncio.run(n._cmd_pause(msg))
    assert b.paused is True


def test_resume_clears_pause(notifier_and_bot):
    n, b = notifier_and_bot
    b.pause_manager.pause(10, 'test')
    b.paused = True
    msg = _msg('/resume')
    asyncio.run(n._cmd_resume(msg))
    assert b.paused is False
    assert b.pause_manager.is_paused() is False


def test_metrics_command(notifier_and_bot):
    n, b = notifier_and_bot
    b.metrics.signals_generated = 5
    b.metrics.trades_executed = 2
    msg = _msg('/metrics')
    asyncio.run(n._cmd_metrics(msg))
    out = msg.answer.call_args[0][0]
    assert 'signals_generated' in out
    assert '5' in out
    assert '40.0%' in out


def test_errors_empty(notifier_and_bot):
    n, _ = notifier_and_bot
    msg = _msg('/errors')
    asyncio.run(n._cmd_errors(msg))
    out = msg.answer.call_args[0][0]
    assert 'No errors' in out


def test_errors_grouped(notifier_and_bot):
    n, b = notifier_and_bot
    b.error_tracker.add('timeout')
    b.error_tracker.add('timeout')
    b.error_tracker.add('rejected')
    msg = _msg('/errors')
    asyncio.run(n._cmd_errors(msg))
    out = msg.answer.call_args[0][0]
    assert 'timeout: 2' in out
    assert 'rejected: 1' in out


def test_enable_real_trading_starts_pending(notifier_and_bot):
    n, b = notifier_and_bot
    msg = _msg('/enable_real_trading')
    asyncio.run(n._cmd_enable_real_trading(msg))
    assert b._real_trading_pending_at is not None
    out = msg.answer.call_args[0][0]
    assert 'Confirm' in out


def test_enable_real_trading_already_real(notifier_and_bot):
    n, b = notifier_and_bot
    b.dry_run = False
    msg = _msg('/enable_real_trading')
    asyncio.run(n._cmd_enable_real_trading(msg))
    out = msg.answer.call_args[0][0]
    assert 'Already' in out


def test_confirm_without_pending_rejected(notifier_and_bot):
    n, _ = notifier_and_bot
    msg = _msg('/confirm_real_trading')
    asyncio.run(n._cmd_confirm_real_trading(msg))
    out = msg.answer.call_args[0][0]
    assert 'No pending' in out


def test_confirm_expired(notifier_and_bot):
    n, b = notifier_and_bot
    b._real_trading_pending_at = time.time() - 120
    b._real_trading_pending_ttl = 60
    msg = _msg('/confirm_real_trading')
    asyncio.run(n._cmd_confirm_real_trading(msg))
    out = msg.answer.call_args[0][0]
    assert 'expired' in out
    assert b.dry_run is True   # not flipped


def test_shutdown_signals_termination(notifier_and_bot):
    n, b = notifier_and_bot
    b.running = True
    msg = _msg('/shutdown')
    with patch('os.kill') as mock_kill:
        asyncio.run(n._cmd_shutdown(msg))
    out = msg.answer.call_args[0][0]
    assert 'Shutting down' in out
    assert b.running is False
    mock_kill.assert_called_once()


def test_confirm_flips_both_layers(notifier_and_bot):
    n, b = notifier_and_bot
    b._real_trading_pending_at = time.time()
    msg = _msg('/confirm_real_trading')
    asyncio.run(n._cmd_confirm_real_trading(msg))
    assert b.dry_run is False
    assert b.executor.config.simulation_mode is False
    assert b.executor.config.dry_run is False
    out = msg.answer.call_args[0][0]
    assert 'REAL TRADING ENABLED' in out
