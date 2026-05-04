"""End-to-end tests for ArbBot ↔ safety wiring.

We mock out network-bound pieces (ExchangeClient, EventBus publishes) and
exercise the core decision flow in `_on_signal_scored` and `_on_price_tick`.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.safety import RiskLimits
from src.strategy.signal import Direction, Signal


@pytest.fixture
def bot(monkeypatch):
    """Fully constructed ArbBot with network ports + Telegram mocked.

    Critically: bot.notifier is forced to None to prevent any test from
    accidentally sending messages to a live Telegram chat. (load_dotenv() runs
    at module import so monkeypatch.delenv alone isn't enough.)
    """
    monkeypatch.delenv('TELEGRAM_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    with patch('src.exchange.client.ccxt.binance'):
        with patch('src.exchange.client.ExchangeClient._load_rate_limits'):
            with patch('src.exchange.client.ExchangeClient._check_connection'):
                from scripts.arb_bot import ArbBot
                bot = ArbBot({
                    'apiKey': 'k', 'secret': 's', 'sandbox': True,  # pragma: allowlist secret
                    'risk_limits': RiskLimits(max_trade_usd=Decimal('20')),
                    'initial_capital_usd': '100',
                    'dry_run': True,
                })
                bot.notifier = None  # never send real Telegram messages from tests
                # Replace executor.execute with a controllable mock.
                bot.executor = MagicMock()
                async def _fake_execute(sig):
                    return SimpleNamespace(
                        signal=sig,
                        state=SimpleNamespace(name='DONE'),
                        actual_net_pnl=Decimal('1.0'),
                        leg1_venue='cex', leg2_venue='dex',
                        leg1_fill_price=Decimal('2000'), leg2_fill_price=Decimal('2010'),
                        leg1_fill_size=Decimal('0.005'), leg2_fill_size=Decimal('0.005'),
                        error=None,
                    )
                bot.executor.execute = _fake_execute
                bot.executor.circuit_breaker = MagicMock(is_open=lambda: False)
                bot.executor.config = MagicMock(gas_cost_usd=Decimal('0.5'))
                # No-op publish so we can call handlers in isolation.
                bot.bus = MagicMock()
                async def _ap(*_a, **_kw):
                    return None
                bot.bus.publish = _ap
                return bot


def _signal(score=Decimal('80'), size=Decimal('0.005'), spread=Decimal('60'),
            cex_price=Decimal('2000')) -> Signal:
    return Signal.create(
        pair='ETH/USDT', direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=cex_price, dex_price=Decimal('2012'),
        spread_bps=spread, size=size,
        expected_gross_pnl=Decimal('1'), expected_fees=Decimal('0.1'),
        expected_net_pnl=Decimal('0.9'), score=score,
        expiry=10**12, inventory_ok=True, within_limits=True,
    )


# --- Kill switch in _on_price_tick ---

def test_kill_switch_active_stops_bot(bot, tmp_path):
    kill = tmp_path / 'kill'
    kill.touch()
    with patch('scripts.arb_bot.is_kill_switch_active', return_value=True):
        bot.running = True
        asyncio.run(bot._on_price_tick(SimpleNamespace(pair='ETH/USDT', size=Decimal('0.01'))))
    assert not bot.running


def test_auto_kill_below_capital_floor_stops(bot):
    bot.running = True
    bot.risk_manager.record_trade(Decimal('-60'))   # capital=40 < 50% of 100
    with patch('scripts.arb_bot.is_kill_switch_active', return_value=False):
        asyncio.run(bot._on_price_tick(SimpleNamespace(pair='ETH/USDT', size=Decimal('0.01'))))
    assert not bot.running
    assert bot.auto_kill.triggered


# --- Validator + risk + safety_check gates in _on_signal_scored ---

def test_low_score_skipped_before_risk(bot):
    sig = _signal(score=Decimal('40'))
    asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    # executor.execute must not have been called → no PnL recorded
    assert bot.risk_manager.daily_pnl == Decimal('0')


def test_validator_rejects_stale_signal(bot):
    sig = _signal()
    sig.timestamp -= 100   # force age > 5s
    asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert bot.risk_manager.daily_pnl == Decimal('0')


def test_risk_blocks_oversized_trade(bot):
    sig = _signal(size=Decimal('0.05'), cex_price=Decimal('2000'))   # $100 > max_trade_usd=20
    asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    # Risk gate triggered before executor runs
    assert bot.risk_manager.daily_pnl == Decimal('0')


def test_happy_path_runs_executor_and_records_pnl(bot):
    from src.executor.engine import ExecutorState
    sig = _signal()                                          # passes all gates
    asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    # _on_signal_scored calls executor; _on_execution_done is a separate handler.
    # Simulate the next handler by calling it manually:
    ctx = SimpleNamespace(
        signal=sig, state=ExecutorState.DONE,
        actual_net_pnl=Decimal('1.5'),
        leg1_venue='cex', leg2_venue='dex',
        leg1_fill_price=Decimal('2000'), leg2_fill_price=Decimal('2010'),
        leg1_fill_size=Decimal('0.005'), leg2_fill_size=Decimal('0.005'),
        started_at=0.0, finished_at=1.0,
        error=None,
    )
    bot.pnl_engine = MagicMock()
    bot._sync_balances = lambda: asyncio.sleep(0)
    asyncio.run(bot._on_execution_done(SimpleNamespace(ctx=ctx)))
    assert bot.risk_manager.daily_pnl == Decimal('1.5')
    assert bot.risk_manager.current_capital == Decimal('101.5')


def test_failed_execution_increments_error_count(bot):
    from src.executor.engine import ExecutorState
    sig = _signal()
    ctx = SimpleNamespace(
        signal=sig, state=ExecutorState.FAILED,
        actual_net_pnl=None,
        leg1_venue='cex', leg2_venue='dex',
        leg1_fill_price=None, leg2_fill_price=None,
        leg1_fill_size=None, leg2_fill_size=None,
        started_at=0.0, finished_at=1.0,
        error='timeout',
    )
    bot._sync_balances = lambda: asyncio.sleep(0)
    asyncio.run(bot._on_execution_done(SimpleNamespace(ctx=ctx)))
    assert bot.auto_kill.error_count_1h == 1


def test_dry_run_log_emitted(bot, caplog):
    import logging
    sig = _signal()
    with caplog.at_level(logging.INFO):
        asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert any('DRY RUN | Would trade' in rec.message for rec in caplog.records)


# --- Structured signal logging emits SIGNAL | <STATUS> | ... lines ---

def test_structured_log_rejected_score(bot, caplog):
    import logging
    sig = _signal(score=Decimal('40'))
    with caplog.at_level(logging.INFO):
        asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert any('SIGNAL | REJECTED_SCORE' in rec.message for rec in caplog.records)


def test_structured_log_rejected_validator(bot, caplog):
    import logging
    sig = _signal()
    sig.timestamp -= 100
    with caplog.at_level(logging.INFO):
        asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert any('SIGNAL | REJECTED_VALIDATOR' in rec.message for rec in caplog.records)


def test_structured_log_rejected_risk(bot, caplog):
    import logging
    sig = _signal(size=Decimal('0.05'))
    with caplog.at_level(logging.INFO):
        asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert any('SIGNAL | REJECTED_RISK' in rec.message for rec in caplog.records)


def test_structured_log_dry_run_executed(bot, caplog):
    import logging
    sig = _signal()
    with caplog.at_level(logging.INFO):
        asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert any('SIGNAL | DRY_RUN_EXECUTED' in rec.message for rec in caplog.records)


# --- Auto-pause after consecutive losses + paused bot skips execution ---

def test_auto_pause_after_consecutive_losses(bot):
    from src.executor.engine import ExecutorState
    bot.risk_manager.limits.consecutive_loss_limit = 3
    bot.pnl_engine = MagicMock()
    bot._sync_balances = lambda: asyncio.sleep(0)
    sig = _signal()
    for _ in range(3):
        ctx = SimpleNamespace(
            signal=sig, state=ExecutorState.DONE,
            actual_net_pnl=Decimal('-1.0'),
            leg1_venue='cex', leg2_venue='dex',
            leg1_fill_price=Decimal('2000'), leg2_fill_price=Decimal('2010'),
            leg1_fill_size=Decimal('0.005'), leg2_fill_size=Decimal('0.005'),
            started_at=0.0, finished_at=1.0,
            error=None,
        )
        asyncio.run(bot._on_execution_done(SimpleNamespace(ctx=ctx)))
    assert bot.pause_manager.is_paused()
    assert '3 consecutive losses' in bot.pause_manager.pause_reason


def test_paused_bot_skips_execution(bot, caplog):
    import logging
    bot.pause_manager.pause(30, "test")
    sig = _signal()
    with caplog.at_level(logging.INFO):
        asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert any('SIGNAL | REJECTED_PAUSED' in rec.message for rec in caplog.records)
    # Metrics should not increment past pause gate.
    assert bot.metrics.signals_passed_score == 0
    assert bot.metrics.trades_executed == 0


def test_metrics_increment_along_pipeline(bot):
    sig = _signal()
    asyncio.run(bot._on_signal_generated(SimpleNamespace(signal=sig)))
    # _on_signal_generated runs the real scorer; force a passing score for the
    # downstream test rather than coupling to scorer heuristics.
    sig.score = Decimal('80')
    asyncio.run(bot._on_signal_scored(SimpleNamespace(signal=sig)))
    assert bot.metrics.signals_generated == 1
    assert bot.metrics.signals_passed_score == 1
    assert bot.metrics.signals_passed_validator == 1
    assert bot.metrics.signals_passed_risk == 1
    assert bot.metrics.trades_executed == 1


# --- API key health check wiring ---

def test_preflight_aborts_on_invalid_key(bot):
    bot._key_health = MagicMock()
    bot._key_health.check.return_value = SimpleNamespace(
        valid=False, expires_at=None, days_remaining=None,
        error_msg="auth: -2014", ip_restricted=None,
        is_expiring_soon=lambda: False,
    )
    ok = asyncio.run(bot._preflight_api_key())
    assert ok is False


def test_preflight_aborts_on_already_expired_key(bot):
    bot._key_health = MagicMock()
    bot._key_health.check.return_value = SimpleNamespace(
        valid=True, expires_at=None, days_remaining=-2.0,
        error_msg=None, ip_restricted=False,
        is_expiring_soon=lambda: True,
    )
    ok = asyncio.run(bot._preflight_api_key())
    assert ok is False


def test_preflight_warns_on_soon_expiration(bot, caplog):
    import logging
    bot._key_health = MagicMock()
    bot._key_health.check.return_value = SimpleNamespace(
        valid=True, expires_at=None, days_remaining=3.0,
        error_msg=None, ip_restricted=False,
        is_expiring_soon=lambda: True,
    )
    with caplog.at_level(logging.WARNING):
        ok = asyncio.run(bot._preflight_api_key())
    assert ok is True
    assert any('expires in 3.0 days' in rec.message for rec in caplog.records)


def test_preflight_passes_for_ip_whitelisted_key(bot):
    bot._key_health = MagicMock()
    bot._key_health.check.return_value = SimpleNamespace(
        valid=True, expires_at=None, days_remaining=None,
        error_msg=None, ip_restricted=True,
        is_expiring_soon=lambda: False,
    )
    ok = asyncio.run(bot._preflight_api_key())
    assert ok is True


def test_pool_refresh_loop_calls_refresh_for_each_loaded_pool(bot):
    """One iteration of the refresh loop should call refresh_pool for every
    address in pricing_engine.pools."""
    bot.pricing_engine = MagicMock()
    bot.pricing_engine.pools = {'0xpool1': object(), '0xpool2': object()}
    bot.running = True

    async def _one_iteration():
        # Run loop with tiny interval, then stop.
        task = asyncio.create_task(bot._pool_refresh_loop(interval_sec=0.01))
        await asyncio.sleep(0.05)
        bot.running = False
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_one_iteration())
    refreshed = {c.args[0] for c in bot.pricing_engine.refresh_pool.call_args_list}
    assert '0xpool1' in refreshed
    assert '0xpool2' in refreshed


def test_pool_refresh_loop_noop_when_no_pricing_engine(bot):
    bot.pricing_engine = None
    bot.running = True
    # Should return immediately, not raise.
    asyncio.run(bot._pool_refresh_loop(interval_sec=0.01))


def test_failed_execution_records_error_tracker(bot):
    from src.executor.engine import ExecutorState
    sig = _signal()
    ctx = SimpleNamespace(
        signal=sig, state=ExecutorState.FAILED,
        actual_net_pnl=None,
        leg1_venue='cex', leg2_venue='dex',
        leg1_fill_price=None, leg2_fill_price=None,
        leg1_fill_size=None, leg2_fill_size=None,
        started_at=0.0, finished_at=1.0,
        error='connection timeout',
    )
    bot._sync_balances = lambda: asyncio.sleep(0)
    asyncio.run(bot._on_execution_done(SimpleNamespace(ctx=ctx)))
    summary = bot.error_tracker.summary()
    assert summary == {'timeout': 1}
    assert bot.metrics.trades_failed == 1


# --- from_config plumbs risk + dry_run ---

# --- V3 + StablecoinConverter wiring through from_config ---

def test_from_config_v3_instantiates_v3_pricing_engine(monkeypatch):
    monkeypatch.delenv('TELEGRAM_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    from src.configs.loader import load_config_from_dict
    cfg = load_config_from_dict({
        'mode': 'prod', 'dry_run': True,
        'cex': {'api_key': 'k', 'secret': 's', 'testnet': False},  # pragma: allowlist secret
        'wallet': {'private_key': '0x' + '1' * 64},  # pragma: allowlist secret
        'chains': [{'chain_id': 42161, 'rpc_url': 'https://arb1.arbitrum.io/rpc'}],
        'pairs': [{'pair': 'CHIP/USDT', 'chain_id': 42161, 'trade_size': '250'}],
        'dex': {'version': 'v3', 'fee_tier': 100},
        'dex_quote_override': {'USDT': 'USDC'},
        'stablecoin_converter': True,
        'risk': {'max_trade_usd': '15', 'initial_capital_usd': '100'},
    })
    with patch('src.exchange.client.ccxt.binance'), \
         patch('src.exchange.client.ExchangeClient._load_rate_limits'), \
         patch('src.exchange.client.ExchangeClient._check_connection'), \
         patch('src.chain.client.ChainClient.__init__', return_value=None):
        from scripts.arb_bot import ArbBot
        bot = ArbBot.from_config(cfg)
        bot.notifier = None
        from src.pricing.v3_pricing_engine import UniswapV3PricingEngine
        assert isinstance(bot.pricing_engine, UniswapV3PricingEngine)
        assert bot.pricing_engine.fee_tier == 100
        assert bot.stablecoin_converter is not None
        assert bot.generator.dex_quote_override == {'USDT': 'USDC'}
        assert bot.generator.stablecoin_converter is bot.stablecoin_converter


def test_from_config_v2_default_preserves_legacy_engine(monkeypatch):
    """Without `dex` field, defaults stay v2 — no regression in existing configs."""
    monkeypatch.delenv('TELEGRAM_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    from src.configs.loader import load_config_from_dict
    cfg = load_config_from_dict({
        'mode': 'test', 'dry_run': True,
        'cex': {'api_key': 'k', 'secret': 's'},  # pragma: allowlist secret
        'wallet': {'private_key': '0x' + '1' * 64},  # pragma: allowlist secret
        'chains': [{'chain_id': 1, 'rpc_url': 'https://x'}],
        'pairs': [{'pair': 'ETH/USDT', 'chain_id': 1, 'trade_size': '0.1'}],
        'risk': {'max_trade_usd': '15', 'initial_capital_usd': '100'},
    })
    with patch('src.exchange.client.ccxt.binance'), \
         patch('src.exchange.client.ExchangeClient._load_rate_limits'), \
         patch('src.exchange.client.ExchangeClient._check_connection'), \
         patch('src.chain.client.ChainClient.__init__', return_value=None), \
         patch('src.pricing.pricing_engine.PricingEngine.__init__', return_value=None), \
         patch('src.pricing.pricing_engine.PricingEngine.load_pools'):
        from scripts.arb_bot import ArbBot
        from src.pricing.pricing_engine import PricingEngine
        from src.pricing.v3_pricing_engine import UniswapV3PricingEngine
        bot = ArbBot.from_config(cfg)
        bot.notifier = None
        assert isinstance(bot.pricing_engine, PricingEngine)
        assert not isinstance(bot.pricing_engine, UniswapV3PricingEngine)
        assert bot.stablecoin_converter is None


def test_generator_applies_stablecoin_ratio_to_dex_prices(bot):
    """Patch generator with override + converter; verify dex prices multiplied
    by ratio for USDT→USDC override case."""
    from types import SimpleNamespace
    bot.generator.dex_quote_override = {'USDT': 'USDC'}
    bot.generator.stablecoin_converter = MagicMock()
    bot.generator.stablecoin_converter.current_ratio.return_value = Decimal('1.001')
    bot.generator.pricing = MagicMock()
    # 1 ETH → 2000 USDC out (=> dex_sell would be 2000); buy 1 ETH from 2000 USDC
    bot.generator.pricing.get_quote.side_effect = [
        SimpleNamespace(expected_output=2000 * 10**6),  # sell quote → 2000 USDC
        SimpleNamespace(expected_output=int(1 * 10**18)),  # buy quote → 1 ETH
    ]
    bot.exchange.fetch_order_book = MagicMock(return_value={
        'bids': [[Decimal('2000'), 10]],
        'asks': [[Decimal('2001'), 10]],
    })

    prices = bot.generator._fetch_prices('ETH/USDT', Decimal('1'))
    # dex_sell should equal 2000 * 1.001 = 2002 (USDT-denominated)
    assert prices['dex_sell'] == Decimal('2000') * Decimal('1.001')


def test_from_config_passes_risk_and_dry_run():
    from src.configs.loader import load_config_from_dict
    cfg = load_config_from_dict({
        'mode': 'test',
        'cex': {'api_key': 'k', 'secret': 's'},  # pragma: allowlist secret
        'wallet': {'private_key': '0x' + '1' * 64},  # pragma: allowlist secret
        'chains': [{'chain_id': 1, 'rpc_url': 'https://x'}],
        'pairs': [{'pair': 'ETH/USDC', 'chain_id': 1, 'trade_size': '0.1'}],
        'dry_run': True,
        'risk': {'max_trade_usd': '15', 'initial_capital_usd': '100'},
    })

    captured = {}
    from scripts import arb_bot as ab_mod
    def fake_init(self, config):
        captured.update(config)
    with patch.object(ab_mod.ArbBot, '__init__', fake_init):
        ab_mod.ArbBot.from_config(cfg)

    assert captured['dry_run'] is True
    assert captured['production'] is False
    assert captured['initial_capital_usd'] == '100'
    assert captured['risk_limits'].max_trade_usd == Decimal('15')
