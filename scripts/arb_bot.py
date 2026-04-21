import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal

from src.exchange.client import ExchangeClient
from src.inventory.pnl import ArbRecord, PnLEngine, TradeLeg
from src.inventory.tracker import InventoryTracker, Venue
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.executor.engine import Executor, ExecutionContext, ExecutorConfig, ExecutorState


class ArbBot:
    def __init__(self, config: dict):
        # Initialize all modules from Weeks 1-4
        self.exchange = ExchangeClient(config)

        # Week 2: Pricing engine (pass None if not available yet)
        if config.get('rpc_url'):
            from src.chain.client import ChainClient
            from src.pricing.PricingEngine import PricingEngine
            self.chain_client = ChainClient([config['rpc_url']])
            self.pricing_engine = PricingEngine(
                self.chain_client, config.get('fork_url', ''), config.get('ws_url', '')
            )
        else:
            self.pricing_engine = None

        # Week 3: Inventory and PnL
        self.inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
        self.pnl_engine = PnLEngine()

        self.fees = FeeStructure()
        self.generator = SignalGenerator(
            self.exchange, self.pricing_engine, self.inventory, self.fees,
            config.get('signal_config', {})
        )
        self.scorer = SignalScorer()
        self.executor = Executor(
            self.exchange, self.pricing_engine, self.inventory,
            ExecutorConfig(simulation_mode=config.get('simulation', True))
        )

        self.pairs = config.get('pairs', ['ETH/USDT'])
        self.trade_size = config.get('trade_size', 0.1)
        self.running = False

    async def run(self):
        self.running = True
        logging.info("Bot starting...")
        await self._sync_balances()

        while self.running:
            try:
                await self._tick()
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"Tick error:{e}")
                await asyncio.sleep(5)

    async def _tick(self):
        if self.executor.circuit_breaker.is_open():
            logging.info("Circuit breaker open")
            return

        for pair in self.pairs:
            signal = self.generator.generate(pair, self.trade_size)
            if signal is None:
                continue

            # Score signal using inventory skews from Week 3
            signal.score = self.scorer.score(signal, self.inventory.get_skews())

            if signal.score < 60:
                continue

            logging.info(f"Signal:{pair} spread={signal.spread_bps:.1f}bps score={signal.score}")

            # Execute
            ctx = await self.executor.execute(signal)

            # Record result in scorer history
            self.scorer.record_result(pair, ctx.state == ExecutorState.DONE)

            # Record in PnL engine (bridge ExecutionContext -> ArbRecord)
            if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
                arb_record = execution_to_arb_record(ctx)
                self.pnl_engine.record(arb_record)
                logging.info(f"SUCCESS: PnL=${ctx.actual_net_pnl:.2f}")
            else:
                logging.warning(f"FAILED:{ctx.error}")

            await self._sync_balances()

    async def _sync_balances(self):
        """Sync balances from both CEX and on-chain wallet."""
        # CEX balances via ExchangeClient (Week 3)
        cex_balances = self.exchange.fetch_balance()
        self.inventory.update_from_cex(Venue.BINANCE, cex_balances)

        # On-chain wallet balances (requires ChainClient from Week 1)
        if self.chain_client:
            wallet_balances = self._fetch_wallet_balances()
            self.inventory.update_from_wallet(Venue.WALLET, wallet_balances)

    def _fetch_wallet_balances(self) -> dict:
        """Query on-chain balances. Implement using ChainClient.get_balance()."""
        ...

    def stop(self):
        self.running = False


def execution_to_arb_record(ctx: ExecutionContext) -> ArbRecord:
    """
    Bridge between Week 4's ExecutionContext and Week 3's ArbRecord.
    Converts executor result into a format the PnL engine can track.
    """
    signal = ctx.signal
    buy_leg = TradeLeg(
        id=f"{signal.signal_id}_buy",
        timestamp=datetime.fromtimestamp(ctx.started_at),
        venue=Venue.BINANCE if ctx.leg1_venue == 'cex' else Venue.WALLET,
        symbol=signal.pair,
        side='buy',
        amount=Decimal(str(ctx.leg1_fill_size or 0)),
        price=Decimal(str(ctx.leg1_fill_price or 0)),
        fee=Decimal('0'),  # Calculate from actual fill
        fee_asset=signal.pair.split('/')[1],
    )
    sell_leg = TradeLeg(
        id=f"{signal.signal_id}_sell",
        timestamp=datetime.fromtimestamp(ctx.finished_at or ctx.started_at),
        venue=Venue.WALLET if ctx.leg2_venue == 'dex' else Venue.BINANCE,
        symbol=signal.pair,
        side='sell',
        amount=Decimal(str(ctx.leg2_fill_size or 0)),
        price=Decimal(str(ctx.leg2_fill_price or 0)),
        fee=Decimal('0'),
        fee_asset=signal.pair.split('/')[1],
    )
    return ArbRecord(
        id=signal.signal_id,
        timestamp=datetime.fromtimestamp(ctx.started_at),
        buy_leg=buy_leg,
        sell_leg=sell_leg,
    )


# Entry point
if __name__ == '__main__':
    config = {
        'apiKey': os.getenv('BINANCE_TESTNET_API_KEY'),
        'secret': os.getenv('BINANCE_TESTNET_SECRET'),
        'sandbox': True,
        'rpc_url': os.getenv('ETH_RPC_URL', ''),
        'pairs': ['ETH/USDT'],
        'trade_size': 0.1,
        'simulation': True,
    }
    bot = ArbBot(config)
    asyncio.run(bot.run())