import sys
from pathlib import Path
from src.core.events import EventBus, PriceTickEvent, SignalGeneratedEvent, SignalScoredEvent, ExecutionDoneEvent

from src.strategy.signal import Direction
from src.notifications.telegram_notifier import TelegramNotifier
from src.exchange.client import ExchangeClient
from src.inventory.pnl import ArbRecord, PnLEngine, TradeLeg
from src.inventory.tracker import InventoryTracker, Venue
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.executor.engine import Executor, ExecutionContext, ExecutorConfig, ExecutorState
from src.safety import (
    ApiKeyHealthCheck,
    AutoKillSwitch,
    ErrorTracker,
    ExecutionMetrics,
    PreTradeValidator,
    RiskLimits,
    RiskManager,
    TradingPauseManager,
    is_kill_switch_active,
    safety_check,
    write_heartbeat,
)
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv
load_dotenv()




def log_signal_event(signal, status: str, reason: str = "") -> None:
    """Structured one-line log for grep-based pipeline analysis.

    Format: ``SIGNAL | <STATUS> | pair=... | direction=... | size=... | spread=...bps | expected_pnl=$... [| reason=...]``.
    Statuses used by the pipeline: GENERATED, REJECTED_PAUSED,
    REJECTED_SCORE, REJECTED_VALIDATOR, REJECTED_RISK, REJECTED_SAFETY,
    EXECUTED, DRY_RUN_EXECUTED.
    """
    line = (
        f"SIGNAL | {status} | pair={signal.pair} | "
        f"direction={signal.direction.value} | size={float(signal.size):.4f} | "
        f"spread={float(signal.spread_bps):.1f}bps | "
        f"expected_pnl=${float(signal.expected_net_pnl):.2f}"
    )
    if reason:
        line += f" | reason={reason}"
    logging.info(line)


class ArbBot:
    def __init__(self, config: dict):
        self.exchange = ExchangeClient(config)

        if config.get('rpc_url'):
            from src.chain.client import ChainClient
            self.chain_client = ChainClient([config['rpc_url']])

            dex_version = str(config.get('dex_version', 'v2')).lower()
            if dex_version == 'v3':
                from src.pricing.v3_pricing_engine import UniswapV3PricingEngine
                self.pricing_engine = UniswapV3PricingEngine(
                    self.chain_client,
                    chain_id=int(config.get('chain_id') or 42161),
                    fee_tier=int(config.get('dex_fee_tier', 3000)),
                )
                # V3 doesn't pre-load pools (Quoter handles routing). Calling
                # load_pools() is a no-op on V3, kept here for symmetry with V2.
                self.pricing_engine.load_pools(config.get('pool_addresses') or [])
                logging.info("DEX engine: Uniswap V3 (fee_tier=%s)",
                             self.pricing_engine.fee_tier)
            else:
                from src.pricing.pricing_engine import PricingEngine
                # ForkSimulator's getAmountsOut is a view call — works on any
                # mainnet RPC, no actual fork needed.
                fork_url = config.get('fork_url') or config.get('rpc_url', '')
                self.pricing_engine = PricingEngine(
                    self.chain_client, fork_url, config.get('ws_url', '')
                )
                pool_addresses = config.get('pool_addresses') or []
                if pool_addresses:
                    from src.core.types import Address
                    self.pricing_engine.load_pools([Address(a) for a in pool_addresses])
                    logging.info("Loaded %d DEX pool(s) into pricing engine",
                                 len(pool_addresses))
                else:
                    logging.warning(
                        "No DEX pool addresses configured — generator will fail "
                        "to fetch DEX quotes. Add `pool_address` to each pair in YAML."
                    )
        else:
            self.chain_client = None
            self.pricing_engine = None

        self.inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
        self.pnl_engine = PnLEngine()

        self.fees = FeeStructure(
            gas_cost_usd=Decimal(str(config.get('gas_cost_usd', '0.5')))
        )

        # StablecoinConverter activates only when YAML opts in.
        self.stablecoin_converter = None
        if config.get('stablecoin_converter'):
            from src.exchange.stablecoin_converter import StablecoinConverter
            self.stablecoin_converter = StablecoinConverter(self.exchange)
            logging.info("StablecoinConverter enabled (live USDC/USDT ratio via Binance)")

        gen_kwargs = {}
        if 'chain_id' in config and config['chain_id'] is not None:
            gen_kwargs['chain_id'] = config['chain_id']
        if config.get('dex_quote_override'):
            gen_kwargs['dex_quote_override'] = config['dex_quote_override']
        if self.stablecoin_converter is not None:
            gen_kwargs['stablecoin_converter'] = self.stablecoin_converter
        self.generator = SignalGenerator(
            self.exchange, self.pricing_engine, self.inventory, self.fees,
            config.get('signal_config', {}),
            **gen_kwargs,
        )
        self.scorer = SignalScorer()

        # Optional TxBuilder wiring — present only when we have an Arbitrum-
        # compatible wallet (PRIVATE_KEY in env) AND a chain client. With it,
        # the executor moves off legacy simulation_mode and onto the
        # build-and-(maybe)-broadcast code path. The bot-level dry_run flag
        # still gates real broadcasting.
        self.tx_builder = None
        if config.get('rpc_url') and os.getenv('PRIVATE_KEY'):
            try:
                from web3 import Web3
                from eth_account import Account
                from src.executor.tx_builder import TxBuilder
                from src.configs.schema import ChainId
                w3 = Web3(Web3.HTTPProvider(config['rpc_url']))
                account = Account.from_key(os.getenv('PRIVATE_KEY'))
                self.tx_builder = TxBuilder(
                    web3=w3,
                    chain_id=ChainId(int(config.get('chain_id') or 42161)),
                    account=account,
                    exchange_client=self.exchange._exchange if hasattr(self.exchange, '_exchange') else None,
                    dex_version=str(config.get('dex_version', 'v2')).lower(),
                    fee_tier=int(config.get('dex_fee_tier', 3000)),
                )
                logging.info("TxBuilder wired: address=%s dex=%s fee_tier=%s",
                             account.address, config.get('dex_version', 'v2'),
                             config.get('dex_fee_tier', 3000))
            except Exception as e:
                logging.warning("TxBuilder NOT wired (will use legacy simulation): %s", e)
                self.tx_builder = None

        # When TxBuilder is wired, simulation_mode must be False so the
        # executor takes the builder path (and respects dry_run for broadcast).
        # When unwired, we fall back to the legacy sim path.
        sim_mode = config.get('simulation', True) if self.tx_builder is None else False
        executor_chain_id = None
        if self.tx_builder is not None and config.get('chain_id'):
            from src.configs.schema import ChainId as _ChainId
            executor_chain_id = _ChainId(int(config['chain_id']))
        # Read min_profit_usd from signal_config so executor's is_valid() check
        # uses the same threshold as the signal generator's filter.
        signal_cfg = config.get('signal_config') or {}
        executor_min_profit = Decimal(str(signal_cfg.get('min_profit_usd', '0')))
        self.executor = Executor(
            self.exchange, self.pricing_engine, self.inventory,
            ExecutorConfig(
                simulation_mode=sim_mode,
                use_flashbots=config.get('use_flashbots', False),
                gas_cost_usd=Decimal(str(config.get('gas_cost_usd', '0.5'))),
                dry_run=bool(config.get('dry_run', True)),
                slippage_bps=Decimal(str(config.get('slippage_bps', '50'))),
                min_profit_usd=executor_min_profit,
            ),
            tx_builder=self.tx_builder,
            chain_id=executor_chain_id,
        )

        self.pairs = config.get('pairs', ['ETH/USDT'])
        self.trade_size = Decimal(str(config.get('trade_size', '0.1')))
        self.running = False
        self.paused = False
        self._cb_was_open = False
        self.signal_log: list[dict] = []  # last 20 signals with outcome

        # Bot-level dry_run: log "would trade" line, still call executor (which
        # in turn prepares-but-doesn't-broadcast). Both layers must be true for
        # any real broadcast to occur.
        self.dry_run = bool(config.get('dry_run', config.get('simulation', True)))
        self.production_mode = bool(config.get('production', False))
        self.min_score = Decimal(str(config.get('min_score', 60)))

        # Safety: risk limits, validator, auto kill switch, pause, metrics.
        risk_limits: RiskLimits = config.get('risk_limits') or RiskLimits()
        initial_capital = Decimal(str(config.get('initial_capital_usd', '100')))
        self.risk_manager = RiskManager(risk_limits, initial_capital)
        self.validator = PreTradeValidator()
        self.auto_kill = AutoKillSwitch()
        self.pause_manager = TradingPauseManager()
        self.metrics = ExecutionMetrics()
        self.error_tracker = ErrorTracker(window_minutes=60)
        # Pending real-trading switch — set by /enable_real_trading, consumed
        # by /confirm_real_trading. (timestamp_seconds, ttl_seconds).
        self._real_trading_pending_at: float | None = None
        self._real_trading_pending_ttl: float = 60.0

        tg_token = config.get('telegram_token') or os.getenv('TELEGRAM_TOKEN')
        tg_chat  = config.get('telegram_chat_id') or os.getenv('TELEGRAM_CHAT_ID')
        self.notifier = TelegramNotifier(tg_token, tg_chat, bot_ref=self) if tg_token and tg_chat else None

        self.bus = EventBus()
        self._wire_handlers()

    @classmethod
    def from_config(cls, cfg) -> "ArbBot":
        """Build ArbBot from a validated `BotConfig` (src.configs.schema.BotConfig).

        Translates the typed config into the dict shape the existing __init__
        expects. This is the recommended entry-point for new code; the dict
        constructor stays for backward compatibility with existing scripts.
        """
        pair_strs = [p.pair for p in cfg.pairs]
        primary_chain_id = cfg.pairs[0].chain_id if cfg.pairs else None
        chain_cfg = cfg.get_chain(primary_chain_id) if primary_chain_id is not None else None

        from src.configs.schema import Mode
        pool_addresses = [p.pool_address for p in cfg.pairs if p.pool_address]
        return cls({
            'apiKey':  cfg.cex.api_key.get_secret_value(),
            'secret':  cfg.cex.secret.get_secret_value(),
            'sandbox': cfg.cex.testnet,
            'rpc_url': chain_cfg.rpc_url if chain_cfg else '',
            'ws_url':  (chain_cfg.ws_url or '') if chain_cfg else '',
            'pairs':   pair_strs,
            'chain_id':       primary_chain_id,
            'pool_addresses': pool_addresses,
            'dex_version':    cfg.dex.version,
            'dex_fee_tier':   cfg.dex.fee_tier,
            'dex_quote_override':   dict(cfg.dex_quote_override),
            'stablecoin_converter': cfg.stablecoin_converter,
            'trade_size': str(cfg.pairs[0].trade_size) if cfg.pairs else '0.1',
            'simulation':    False,  # ArbBot.__init__ ignores this when tx_builder wires up; left as fallback only when PRIVATE_KEY is missing
            'dry_run':       cfg.dry_run,
            'production':    cfg.mode == Mode.PROD,
            'use_flashbots': cfg.executor.use_flashbots,
            'gas_cost_usd':  str(cfg.fees.gas_cost_usd_default),
            'slippage_bps':  str(cfg.executor.slippage_bps),
            'risk_limits':         cfg.risk.to_runtime(),
            'initial_capital_usd': str(cfg.risk.initial_capital_usd),
            'signal_config': {
                'min_spread_bps':     str(cfg.signal.min_spread_bps),
                'min_profit_usd':     str(cfg.signal.min_profit_usd),
                'max_position_usd':   str(cfg.signal.max_position_usd),
                'signal_ttl_seconds': cfg.signal.signal_ttl_seconds,
                'cooldown_seconds':   cfg.signal.cooldown_seconds,
            },
            'min_score': str(cfg.signal.min_score),
        })

    def _wire_handlers(self):
        self.bus.subscribe(PriceTickEvent, self._on_price_tick)
        self.bus.subscribe(SignalGeneratedEvent, self._on_signal_generated)
        self.bus.subscribe(SignalScoredEvent, self._on_signal_scored)
        self.bus.subscribe(ExecutionDoneEvent, self._on_execution_done)
        if self.notifier:
            self.bus.subscribe(SignalGeneratedEvent, self.notifier.on_signal_generated)
            self.bus.subscribe(ExecutionDoneEvent, self.notifier.on_execution_done)

    async def run(self):
        # Stale kill-switch is a common foot-gun (touched once for testing,
        # never removed). Exit immediately with a clear hint.
        from src.safety import KILL_SWITCH_FILE
        if is_kill_switch_active():
            logging.critical(
                "Kill switch file %s still exists from a previous run. "
                "Remove it before starting:  rm %s   (or: make unkill)",
                KILL_SWITCH_FILE, KILL_SWITCH_FILE,
            )
            return

        self.running = True
        if self.production_mode and not self.dry_run:
            logging.warning("⚠️  PRODUCTION MODE — REAL MONEY ⚠️")
        elif self.production_mode:
            logging.warning("PRODUCTION MODE (dry_run=True — no real trades)")
        else:
            logging.info("Test mode | dry_run=%s", self.dry_run)
        logging.info("Bot starting | pairs=%s | risk=%s",
                     self.pairs, self.risk_manager.snapshot())

        # Pre-flight: refuse to start if API key is invalid or already expired.
        self._key_health = ApiKeyHealthCheck(self.exchange.client_for_health_check())
        if not await self._preflight_api_key():
            self.running = False
            return

        await self._sync_balances()
        if self.notifier:
            await self.notifier.on_bot_started(self.pairs)

        loop_task = asyncio.create_task(self._trading_loop())
        hb_task = asyncio.create_task(self._heartbeat_loop())
        summary_task = asyncio.create_task(self._hourly_summary_loop())
        key_task = asyncio.create_task(self._api_key_health_loop())
        # Telegram polling wrapped in a self-restarting task: if it raises (e.g.
        # transient ServerDisconnectedError), we log and continue rather than
        # bringing the whole bot down via FIRST_COMPLETED.
        poll_task = asyncio.create_task(self._telegram_polling_task()) if self.notifier else None

        # DEX-pool freshness: periodic re-fetch (every block) as baseline,
        # mempool WebSocket as event-driven realtime layer. Both refresh the
        # same `pricing_engine.pools` dict — last write wins. Without these,
        # quoted DEX prices are frozen at startup.
        refresh_task = (asyncio.create_task(self._pool_refresh_loop())
                        if self.pricing_engine else None)
        mempool_task = (asyncio.create_task(self._mempool_monitor_task())
                        if self.pricing_engine else None)

        all_tasks = ({loop_task, hb_task, summary_task, key_task}
                     | ({poll_task} if poll_task else set())
                     | ({refresh_task} if refresh_task else set())
                     | ({mempool_task} if mempool_task else set()))
        # `primary_tasks` are the ones whose completion triggers shutdown.
        # Ancillary tasks (polling, heartbeat, summary, refresh) can fail
        # transiently without taking the whole bot down.
        primary_tasks = {loop_task, key_task}

        # Install SIGINT/SIGTERM handlers — aiogram polling otherwise traps
        # SIGINT and Ctrl+C just stops working. We force-cancel every task.
        import signal as _signal
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _request_stop(sig_name: str):
            if not stop_event.is_set():
                logging.warning("Received %s — shutting down.", sig_name)
                stop_event.set()
                self.running = False
                for t in all_tasks:
                    t.cancel()

        for sig in (_signal.SIGINT, _signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop, sig.name)
            except (NotImplementedError, RuntimeError):
                pass  # Windows / nested loops — fall back to KeyboardInterrupt path

        try:
            # Only PRIMARY tasks (trading loop, key health) trigger shutdown
            # on completion. Ancillary failures are logged but the bot keeps
            # running. stop_event being set (via SIGINT/SIGTERM/shutdown) also
            # exits this wait via the canceled primary tasks.
            await asyncio.wait(primary_tasks, return_when=asyncio.FIRST_COMPLETED)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.running = False
            for t in all_tasks:
                t.cancel()
            # Drain cancellations so finally-blocks of each task get to run.
            await asyncio.gather(*all_tasks, return_exceptions=True)
            if self.notifier:
                try:
                    await self.notifier.on_bot_stopped()
                except Exception:
                    pass
                await self.notifier.stop()
            logging.info("Bot stopped.")

    async def _preflight_api_key(self) -> bool:
        """Return True iff API key is valid and not already expired.
        Logs and (if notifier present) Telegram-alerts on any abort path.
        """
        status = await asyncio.to_thread(self._key_health.check)
        if not status.valid:
            logging.critical("API KEY INVALID — refusing to start: %s", status.error_msg)
            if self.notifier:
                await self.notifier.send_text(
                    f"🚨 Bot startup ABORTED — API key invalid:\n{status.error_msg}\n"
                    "Rotate the key in Binance UI before restarting."
                )
            return False
        if status.days_remaining is not None and status.days_remaining <= 0:
            logging.critical("API KEY EXPIRED %.1f days ago — refusing to start",
                             -status.days_remaining)
            if self.notifier:
                await self.notifier.send_text(
                    f"🚨 Bot startup ABORTED — API key expired "
                    f"{-status.days_remaining:.1f} days ago. Rotate now."
                )
            return False
        if status.is_expiring_soon():
            logging.warning("API key expires in %.1f days — rotate ASAP",
                            status.days_remaining)
            if self.notifier:
                await self.notifier.send_text(
                    f"⚠️ API key expires in {status.days_remaining:.1f} days. "
                    "Rotate via Binance UI or extend with IP whitelist."
                )
        elif status.expires_at:
            logging.info("API key expires %s (%.1f days remaining)",
                         status.expires_at.isoformat(timespec='minutes'),
                         status.days_remaining)
        elif status.ip_restricted:
            logging.info("API key is IP-whitelisted — no expiration.")
        else:
            logging.info("API key is valid (expiration unknown).")
        return True

    async def _api_key_health_loop(self, interval_sec: int = 900):
        """Re-check API key validity every 15min. Kill bot on revocation;
        Telegram-warn once when entering the 7-day expiration window."""
        warned_within_7d = False
        while self.running:
            await asyncio.sleep(interval_sec)
            if not self.running:
                break
            try:
                status = await asyncio.to_thread(self._key_health.check)
            except Exception as e:
                logging.warning("API key health check raised (will retry): %s", e)
                continue
            if not status.valid:
                logging.critical("API KEY REVOKED mid-flight — stopping bot: %s",
                                 status.error_msg)
                if self.notifier:
                    await self.notifier.send_text(
                        f"🚨 API KEY REVOKED — bot STOPPED.\n{status.error_msg}\n"
                        "Rotate key in Binance UI, then restart."
                    )
                self.auto_kill.trigger(f"API key revoked: {status.error_msg}")
                self.stop()
                return
            if status.is_expiring_soon() and not warned_within_7d:
                warned_within_7d = True
                logging.warning("API key now within 7d of expiry: %.1fd remaining",
                                status.days_remaining)
                if self.notifier:
                    await self.notifier.send_text(
                        f"⚠️ API key expires in {status.days_remaining:.1f} days. "
                        "Plan rotation."
                    )

    async def _heartbeat_loop(self):
        """Write a heartbeat file every 30s. External watchdog reads it."""
        while self.running:
            write_heartbeat()
            await asyncio.sleep(30)

    async def _pool_refresh_loop(self, interval_sec: float = 12.0):
        """Periodically re-fetch reserves for every loaded DEX pool.

        Eth mainnet block time ≈ 12s, so this catches every confirmed swap.
        Acts as the safety net: if mempool monitor is down or a swap was
        missed, this guarantees `pricing_engine.pools` doesn't drift more
        than `interval_sec` seconds behind chain truth.
        """
        if not self.pricing_engine:
            return
        while self.running:
            await asyncio.sleep(interval_sec)
            if not self.running:
                break
            refreshed = []
            for addr in list(self.pricing_engine.pools.keys()):
                try:
                    await asyncio.to_thread(self.pricing_engine.refresh_pool, addr)
                    pool = self.pricing_engine.pools[addr]
                    refreshed.append(
                        f"{str(addr)[:8]}…={pool.reserve0}/{pool.reserve1}"
                    )
                except Exception as e:
                    logging.warning("pool refresh failed for %s: %s", addr, e)
            if refreshed:
                logging.info("POOL_REFRESH | %s", " | ".join(refreshed))

    async def _telegram_polling_task(self):
        """Self-restarting wrapper around aiogram polling. Network blips and
        ServerDisconnectedError no longer kill the whole bot — we log and retry
        with exponential backoff capped at 60s."""
        if not self.notifier:
            return
        backoff = 1.0
        while self.running:
            try:
                await self.notifier.start_polling(handle_signals=False)
                # If start_polling returns cleanly, treat as graceful exit.
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.warning("Telegram polling failed (will retry in %.0fs): %s",
                                backoff, e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _mempool_monitor_task(self):
        """Subscribe to pending swaps via WebSocket and refresh affected pools
        in real time (sub-second freshness). Falls back silently if the WS
        endpoint is unavailable — `_pool_refresh_loop` keeps things sane."""
        if not self.pricing_engine:
            return
        ws_url = getattr(self.pricing_engine.monitor, 'ws_url', '')
        if not ws_url:
            logging.info("Mempool monitor: no WS_RPC_URL — skipping (periodic refresh only)")
            return
        logging.info("Mempool monitor: starting WebSocket subscription to %s",
                     ws_url[:60] + "…" if len(ws_url) > 60 else ws_url)
        try:
            await self.pricing_engine.start_monitoring()
        except Exception as e:
            logging.warning(
                "Mempool monitor unavailable (relying on periodic refresh): %s", e
            )

    async def _hourly_summary_loop(self):
        """Emit pipeline counters and recent error summary once per hour."""
        while self.running:
            await asyncio.sleep(3600)
            if not self.running:
                break
            m = self.metrics.snapshot()
            err = self.error_tracker.summary()
            logging.info(
                "HOURLY_SUMMARY | signals=%d | passed_score=%d | passed_validator=%d | "
                "passed_risk=%d | trades=%d | success=%d | failed=%d | "
                "execution_rate=%.1f%% | errors=%s",
                m['signals_generated'], m['signals_passed_score'],
                m['signals_passed_validator'], m['signals_passed_risk'],
                m['trades_executed'], m['trades_successful'], m['trades_failed'],
                m['execution_rate_pct'], err or '{}',
            )

    async def _trading_loop(self):
        while self.running:
            if self.paused:
                await asyncio.sleep(1)
                continue
            try:
                for pair in self.pairs:
                    await self.bus.publish(PriceTickEvent(pair, self.trade_size))
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"Loop error: {e}")
                await asyncio.sleep(5)

    async def _on_price_tick(self, event: PriceTickEvent):
        # Layer 1: file-based kill switch — operator override, beats every other check.
        if is_kill_switch_active():
            logging.critical("KILL SWITCH ACTIVE — stopping bot")
            self.stop()
            return

        # Layer 2: programmatic auto-kill switch (capital floor, error rate).
        if self.auto_kill.check(self.risk_manager):
            logging.critical("AUTO KILL SWITCH: %s — stopping bot", self.auto_kill.reason)
            self.stop()
            return

        # Layer 3: circuit breaker (failure burst).
        is_open = self.executor.circuit_breaker.is_open()
        if is_open and not self._cb_was_open:
            self._cb_was_open = True
            if self.notifier:
                await self.notifier.on_circuit_breaker_open(event.pair)
        elif not is_open and self._cb_was_open:
            self._cb_was_open = False
            if self.notifier:
                await self.notifier.on_circuit_breaker_closed()
        if is_open:
            logging.info("Circuit breaker open")
            return

        signal = await asyncio.to_thread(self.generator.generate, event.pair, event.size)
        if signal:
            await self.bus.publish(SignalGeneratedEvent(signal))

    async def _on_signal_generated(self, event: SignalGeneratedEvent):
        signal = event.signal
        self.metrics.signals_generated += 1
        log_signal_event(signal, 'GENERATED')
        # pass empty skews when wallet not configured — avoids false rebalance penalty
        skews = self.inventory.get_skews() if self.chain_client else []
        signal.score = self.scorer.score(signal, skews)
        await self.bus.publish(SignalScoredEvent(signal))

    async def _on_signal_scored(self, event: SignalScoredEvent):
        signal = event.signal

        # Pause check first — paused bot generates+scores signals but does not execute.
        if self.pause_manager.is_paused():
            log_signal_event(
                signal, 'REJECTED_PAUSED',
                f"{self.pause_manager.pause_reason} | resume_in={self.pause_manager.time_remaining():.0f}s",
            )
            self._log_signal(signal, 'paused')
            return

        if signal.score < self.min_score:
            log_signal_event(signal, 'REJECTED_SCORE',
                             f"score={signal.score} < {self.min_score}")
            self._log_signal(signal, 'skipped')
            return
        self.metrics.signals_passed_score += 1

        # Layer 4: pre-trade sanity (spread/age/price validity).
        ok, reason = self.validator.validate_signal(signal)
        if not ok:
            log_signal_event(signal, 'REJECTED_VALIDATOR', reason)
            logging.warning("Validator rejected %s: %s", signal.signal_id, reason)
            self._log_signal(signal, f'invalid:{reason}')
            return
        self.metrics.signals_passed_validator += 1

        # Layer 5: configurable risk limits (per-trade, daily, drawdown, ...).
        ok, reason = self.risk_manager.check_pre_trade(signal)
        if not ok:
            log_signal_event(signal, 'REJECTED_RISK', reason)
            logging.warning("Risk check rejected %s: %s", signal.signal_id, reason)
            self._log_signal(signal, f'risk:{reason}')
            return
        self.metrics.signals_passed_risk += 1

        # Layer 6: ABSOLUTE_* hardcoded ceilings — final non-negotiable gate.
        trade_value = signal.size * signal.cex_price
        ok, reason = safety_check(
            trade_usd=trade_value,
            daily_pnl=self.risk_manager.daily_pnl,
            total_capital=self.risk_manager.current_capital,
            trades_this_hour=self.risk_manager.trades_this_hour,
        )
        if not ok:
            log_signal_event(signal, 'REJECTED_SAFETY', reason)
            logging.critical("SAFETY breach %s: %s", signal.signal_id, reason)
            self._log_signal(signal, f'safety:{reason}')
            return

        logging.info(f"Signal: {signal.pair} spread={signal.spread_bps:.1f}bps score={signal.score}")

        if self.dry_run:
            log_signal_event(signal, 'DRY_RUN_EXECUTED')
            logging.info(
                "DRY RUN | Would trade: %s %s size=%.4f spread=%.1fbps expected_pnl=$%.2f",
                signal.pair, signal.direction.value, signal.size,
                signal.spread_bps, signal.expected_net_pnl,
            )
        else:
            log_signal_event(signal, 'EXECUTED')

        self.metrics.trades_executed += 1
        ctx = await self.executor.execute(signal)
        await self.bus.publish(ExecutionDoneEvent(ctx))

    async def _on_execution_done(self, event: ExecutionDoneEvent):
        ctx = event.ctx
        self.scorer.record_result(ctx.signal.pair, ctx.state == ExecutorState.DONE)
        if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
            self.metrics.trades_successful += 1
            self.pnl_engine.record(execution_to_arb_record(ctx, self.executor.config.gas_cost_usd))
            self.risk_manager.record_trade(ctx.actual_net_pnl)
            logging.info(
                "SUCCESS: PnL=$%.2f | risk=%s",
                ctx.actual_net_pnl, self.risk_manager.snapshot(),
            )
            # Auto-pause after consecutive losses (pause-based, not full stop).
            if (self.risk_manager.consecutive_losses
                    >= self.risk_manager.limits.consecutive_loss_limit):
                limit = self.risk_manager.limits.consecutive_loss_limit
                self.pause_manager.pause(
                    duration_minutes=30,
                    reason=f"{limit} consecutive losses",
                )
                if self.notifier:
                    await self.notifier.send_text(
                        f"⏸️ Trading paused 30min — {limit} losses in a row"
                    )
        else:
            self.metrics.trades_failed += 1
            err_type = self._classify_error(ctx.error)
            self.error_tracker.add(err_type)
            logging.warning(f"FAILED: {ctx.error}")
            self.auto_kill.record_error()
        self._log_signal(ctx.signal, ctx.state.name, ctx.actual_net_pnl)
        await self._sync_balances()

    @staticmethod
    def _classify_error(err) -> str:
        """Bucket raw error into a coarse type for ErrorTracker.summary()."""
        if err is None:
            return 'unknown'
        msg = str(err).lower()
        if 'timeout' in msg:
            return 'timeout'
        if 'reject' in msg or 'invalid' in msg:
            return 'rejected'
        if 'slippage' in msg:
            return 'slippage'
        if 'insufficient' in msg or 'balance' in msg:
            return 'balance'
        if 'network' in msg or 'connection' in msg:
            return 'network'
        return 'other'

    def _log_signal(self, signal, result: str, pnl=None):
        self.signal_log.append({
            'time':   datetime.now().strftime('%H:%M:%S'),
            'pair':   signal.pair,
            'spread': signal.spread_bps,
            'score':  signal.score,
            'result': result,
            'pnl':    pnl,
        })
        self.signal_log = self.signal_log[-20:]

    async def _sync_balances(self):
        cex_balances = await asyncio.to_thread(self.exchange.fetch_balance)
        self.inventory.update_from_cex(Venue.BINANCE, cex_balances)
        if self.chain_client:
            wallet_balances = await asyncio.to_thread(self._fetch_wallet_balances)
            self.inventory.update_from_wallet(Venue.WALLET, wallet_balances)

    async def verify_balances(self, asset: str = 'ETH', tolerance: Decimal = Decimal('0.001')) -> bool:
        """Compare expected (cached) vs actual fresh balances. Returns True iff
        within tolerance. Logs critical and increments error count on mismatch.

        Call after a real (non-dry-run) execution. In dry-run nothing changed
        on-chain, so this should always succeed — useful as a sanity probe too.
        """
        try:
            actual_cex = await asyncio.to_thread(self.exchange.fetch_balance)
            actual_cex_qty = Decimal(str(actual_cex.get(asset, {}).get('free', 0)))
        except Exception as e:
            logging.warning("verify_balances: CEX fetch failed: %s", e)
            return True   # don't kill the bot for a transient API hiccup

        expected_cex = self.inventory.get_available(Venue.BINANCE, asset)
        cex_diff = abs(actual_cex_qty - expected_cex)

        wallet_diff = Decimal('0')
        if self.chain_client:
            try:
                wallet = await asyncio.to_thread(self._fetch_wallet_balances)
                actual_wallet_qty = Decimal(str(wallet.get(asset, 0)))
                expected_wallet = self.inventory.get_available(Venue.WALLET, asset)
                wallet_diff = abs(actual_wallet_qty - expected_wallet)
            except Exception as e:
                logging.warning("verify_balances: wallet fetch failed: %s", e)

        if cex_diff > tolerance or wallet_diff > tolerance:
            logging.critical(
                "BALANCE MISMATCH | %s | CEX diff=%s | wallet diff=%s — stopping",
                asset, cex_diff, wallet_diff,
            )
            self.auto_kill.record_error()
            self.stop()
            return False
        return True

    def _fetch_wallet_balances(self) -> dict:
        """Read on-chain ETH + ERC20 balances for tokens used by configured pairs.

        Returns {asset_symbol: amount_as_float}. Tokens not in the registry are
        skipped (best-effort — caller treats missing as zero).
        """
        if self.tx_builder is None:
            return {}
        from src.configs.tokens import get_token

        web3 = self.tx_builder.web3
        chain_id = self.tx_builder.chain_id
        address = self.tx_builder.account.address

        erc20_abi = [
            {"name": "balanceOf", "type": "function", "stateMutability": "view",
             "inputs": [{"type": "address", "name": "owner"}],
             "outputs": [{"type": "uint256"}]},
        ]

        balances = {}
        try:
            balances['ETH'] = float(Decimal(str(web3.eth.get_balance(address))) / Decimal('1000000000000000000'))
        except Exception as e:
            logging.warning(f"_fetch_wallet_balances: ETH fetch failed: {e}")

        symbols = set()
        for pair in self.pairs:
            base, quote = pair.split('/')
            symbols.add(base)
            symbols.add(quote)

        for sym in symbols:
            if sym in ('ETH',):
                continue
            try:
                token = get_token(chain_id, sym)
                contract = web3.eth.contract(address=token.address.checksum, abi=erc20_abi)
                raw = contract.functions.balanceOf(address).call()
                balances[sym] = float(Decimal(str(raw)) / Decimal(10 ** token.decimals))
            except KeyError:
                logging.debug(f"_fetch_wallet_balances: {sym} not in token registry, skipping")
            except Exception as e:
                logging.warning(f"_fetch_wallet_balances: {sym} fetch failed: {e}")

        logging.info(f"Wallet balances: {balances}")
        return balances

    def stop(self):
        self.running = False


def execution_to_arb_record(
    ctx: ExecutionContext,
    gas_cost_usd: Decimal = Decimal('0.5'),
    cex_taker_bps: Decimal = Decimal('10'),
    dex_swap_bps: Decimal = Decimal('30'),
) -> ArbRecord:
    signal = ctx.signal
    size = ctx.leg1_fill_size or Decimal('0')
    cex_fill_price = (ctx.leg1_fill_price if ctx.leg1_venue == 'cex' else ctx.leg2_fill_price) or Decimal('0')
    dex_fill_price = (ctx.leg1_fill_price if ctx.leg1_venue == 'dex' else ctx.leg2_fill_price) or Decimal('0')
    cex_fee = size * cex_fill_price * (cex_taker_bps / Decimal('10000'))
    dex_fee = size * dex_fill_price * (dex_swap_bps / Decimal('10000')) + gas_cost_usd
    quote_asset = signal.pair.split('/')[1]

    if signal.direction == Direction.BUY_CEX_SELL_DEX:
        buy_fee, sell_fee = cex_fee, dex_fee
        buy_venue  = Venue.BINANCE if ctx.leg1_venue == 'cex' else Venue.WALLET
        sell_venue = Venue.WALLET  if ctx.leg2_venue == 'dex' else Venue.BINANCE
    else:
        buy_fee, sell_fee = dex_fee, cex_fee
        buy_venue  = Venue.WALLET  if ctx.leg1_venue == 'dex' else Venue.BINANCE
        sell_venue = Venue.BINANCE if ctx.leg2_venue == 'cex' else Venue.WALLET

    buy_leg = TradeLeg(
        id=f"{signal.signal_id}_buy",
        timestamp=datetime.fromtimestamp(ctx.started_at),
        venue=buy_venue,
        symbol=signal.pair,
        side='buy',
        amount=ctx.leg1_fill_size or Decimal('0'),
        price=ctx.leg1_fill_price or Decimal('0'),
        fee=buy_fee,
        fee_asset=quote_asset,
    )
    sell_leg = TradeLeg(
        id=f"{signal.signal_id}_sell",
        timestamp=datetime.fromtimestamp(ctx.finished_at or ctx.started_at),
        venue=sell_venue,
        symbol=signal.pair,
        side='sell',
        amount=ctx.leg2_fill_size or Decimal('0'),
        price=ctx.leg2_fill_price or Decimal('0'),
        fee=sell_fee,
        fee_asset=quote_asset,
    )
    return ArbRecord(
        id=signal.signal_id,
        timestamp=datetime.fromtimestamp(ctx.started_at),
        buy_leg=buy_leg,
        sell_leg=sell_leg,
    )


def setup_file_logging(log_dir: str = 'logs') -> str:
    """Add a file handler alongside console output. Returns the log path."""
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(exist_ok=True)
    log_path = log_dir_path / f"bot_{datetime.now():%Y%m%d_%H%M%S}.log"
    fh = logging.FileHandler(str(log_path))
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logging.getLogger().addHandler(fh)
    return str(log_path)


if __name__ == '__main__':
    import argparse
    from src.configs.loader import ConfigError, load_config

    parser = argparse.ArgumentParser(description="Run the arbitrage bot.")
    parser.add_argument(
        '-c', '--config', default='configs/test.yaml',
        help="Path to YAML config (default: configs/test.yaml — Binance testnet).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
    )
    log_path = setup_file_logging()
    logging.info("Logging to file: %s", log_path)
    logging.info("Loading config: %s", args.config)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        logging.error("Config error: %s", e)
        sys.exit(2)

    bot = ArbBot.from_config(cfg)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
