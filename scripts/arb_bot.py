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
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv
load_dotenv()




class ArbBot:
    def __init__(self, config: dict):
        self.exchange = ExchangeClient(config)

        if config.get('rpc_url'):
            from src.chain.client import ChainClient
            from src.pricing.pricing_engine import PricingEngine
            self.chain_client = ChainClient([config['rpc_url']])
            self.pricing_engine = PricingEngine(
                self.chain_client, config.get('fork_url', ''), config.get('ws_url', '')
            )
        else:
            self.chain_client = None
            self.pricing_engine = None

        self.inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
        self.pnl_engine = PnLEngine()

        self.fees = FeeStructure(
            gas_cost_usd=Decimal(str(config.get('gas_cost_usd', '0.5')))
        )
        self.generator = SignalGenerator(
            self.exchange, self.pricing_engine, self.inventory, self.fees,
            config.get('signal_config', {})
        )
        self.scorer = SignalScorer()
        self.executor = Executor(
            self.exchange, self.pricing_engine, self.inventory,
            ExecutorConfig(
                simulation_mode=config.get('simulation', True),
                use_flashbots=config.get('use_flashbots', False),
                gas_cost_usd=Decimal(str(config.get('gas_cost_usd', '0.5'))),
            )
        )

        self.pairs = config.get('pairs', ['ETH/USDT'])
        self.trade_size = Decimal(str(config.get('trade_size', '0.1')))
        self.running = False
        self.paused = False
        self._cb_was_open = False
        self.signal_log: list[dict] = []  # last 20 signals with outcome

        tg_token = config.get('telegram_token') or os.getenv('TELEGRAM_TOKEN')
        tg_chat  = config.get('telegram_chat_id') or os.getenv('TELEGRAM_CHAT_ID')
        self.notifier = TelegramNotifier(tg_token, tg_chat, bot_ref=self) if tg_token and tg_chat else None

        self.bus = EventBus()
        self._wire_handlers()

    def _wire_handlers(self):
        self.bus.subscribe(PriceTickEvent, self._on_price_tick)
        self.bus.subscribe(SignalGeneratedEvent, self._on_signal_generated)
        self.bus.subscribe(SignalScoredEvent, self._on_signal_scored)
        self.bus.subscribe(ExecutionDoneEvent, self._on_execution_done)
        if self.notifier:
            self.bus.subscribe(ExecutionDoneEvent, self.notifier.on_execution_done)

    async def run(self):
        self.running = True
        logging.info("Bot starting...")
        await self._sync_balances()
        if self.notifier:
            await self.notifier.on_bot_started(self.pairs)

        loop_task = asyncio.create_task(self._trading_loop())
        poll_task = asyncio.create_task(
            self.notifier.start_polling(handle_signals=False)
        ) if self.notifier else None

        all_tasks = {loop_task} | ({poll_task} if poll_task else set())
        try:
            await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.running = False
            for t in all_tasks:
                t.cancel()
            if self.notifier:
                await self.notifier.on_bot_stopped()
                await self.notifier.stop()
            logging.info("Bot stopped.")

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
        # pass empty skews when wallet not configured — avoids false rebalance penalty
        skews = self.inventory.get_skews() if self.chain_client else []
        signal.score = self.scorer.score(signal, skews)
        await self.bus.publish(SignalScoredEvent(signal))

    async def _on_signal_scored(self, event: SignalScoredEvent):
        signal = event.signal
        if signal.score < 60:
            self._log_signal(signal, 'skipped')
            return
        logging.info(f"Signal: {signal.pair} spread={signal.spread_bps:.1f}bps score={signal.score}")
        ctx = await self.executor.execute(signal)
        await self.bus.publish(ExecutionDoneEvent(ctx))

    async def _on_execution_done(self, event: ExecutionDoneEvent):
        ctx = event.ctx
        self.scorer.record_result(ctx.signal.pair, ctx.state == ExecutorState.DONE)
        if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
            self.pnl_engine.record(execution_to_arb_record(ctx, self.executor.config.gas_cost_usd))
            logging.info(f"SUCCESS: PnL=${ctx.actual_net_pnl:.2f}")
        else:
            logging.warning(f"FAILED: {ctx.error}")
        self._log_signal(ctx.signal, ctx.state.name, ctx.actual_net_pnl)
        await self._sync_balances()

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

    def _fetch_wallet_balances(self) -> dict:
        if self.chain_client is None:
            return {}
        try:
            return self.chain_client.get_wallet_balances()
        except Exception as e:
            logging.warning(f"Failed to fetch wallet balances: {e}")
            return {}

    def stop(self):
        self.running = False


def execution_to_arb_record(ctx: ExecutionContext, gas_cost_usd: Decimal = Decimal('0.5')) -> ArbRecord:
    signal = ctx.signal
    size = ctx.leg1_fill_size or Decimal('0')
    cex_fill_price = (ctx.leg1_fill_price if ctx.leg1_venue == 'cex' else ctx.leg2_fill_price) or Decimal('0')
    dex_fill_price = (ctx.leg1_fill_price if ctx.leg1_venue == 'dex' else ctx.leg2_fill_price) or Decimal('0')
    cex_fee = size * cex_fill_price * Decimal('0.001')          # 10 bps taker
    dex_fee = size * dex_fill_price * Decimal('0.003') + gas_cost_usd  # 30 bps swap + gas
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


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    config = {
        'apiKey': os.getenv('BINANCE_TESTNET_API_KEY'),
        'secret': os.getenv('BINANCE_TESTNET_SECRET'),
        'sandbox': True,
        'rpc_url': os.getenv('ETH_RPC_URL', ''),
        'pairs': ['ETH/USDT'],
        'trade_size': 0.1,
        'simulation': True,
        'gas_cost_usd': '0.5',
        'signal_config': {
            'min_profit_usd': 0.1,
            'cooldown_seconds': 5,
        },
    }
    bot = ArbBot(config)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
