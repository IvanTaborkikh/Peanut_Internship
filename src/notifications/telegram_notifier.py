import logging
from decimal import Decimal

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.core.events import ExecutionDoneEvent, SignalGeneratedEvent
from src.executor.engine import ExecutorState


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, bot_ref=None):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self._arb_bot = bot_ref  # reference to ArbBot for /status, /pnl commands

        self.router = Router()
        self.router.message.register(self._cmd_start,     Command("start"))
        self.router.message.register(self._cmd_status,    Command("status"))
        self.router.message.register(self._cmd_pnl,       Command("pnl"))
        self.router.message.register(self._cmd_inventory, Command("inventory"))
        self.router.message.register(self._cmd_signals,   Command("signals"))
        self.router.message.register(self._cmd_winrate,   Command("winrate"))
        self.router.message.register(self._cmd_pause,     Command("pause"))
        self.router.message.register(self._cmd_resume,    Command("resume"))

        self.dp = Dispatcher()
        self.dp.include_router(self.router)

    # ── EventBus handlers ─────────────────────────────────────────────────────

    async def on_signal_generated(self, e: SignalGeneratedEvent):
        s = e.signal
        await self._send(
            f"Signal found\n"
            f"Pair:   {s.pair}\n"
            f"Spread: {s.spread_bps:.1f} bps\n"
            f"Score:  {s.score}"
        )

    async def on_execution_done(self, e: ExecutionDoneEvent):
        ctx = e.ctx
        if ctx.state == ExecutorState.DONE:
            pnl = ctx.actual_net_pnl or Decimal('0')
            sign = "+" if pnl >= 0 else ""
            await self._send(
                f"Trade done\n"
                f"Pair: {ctx.signal.pair}\n"
                f"PnL:  {sign}${pnl:.4f}"
            )
        elif ctx.state == ExecutorState.PARTIAL:
            await self._send(f"Partial fill — {ctx.signal.pair}\n{ctx.error}")
        else:
            await self._send(f"Trade failed — {ctx.signal.pair}\n{ctx.error}")

    async def on_circuit_breaker_open(self, pair: str):
        await self._send(f"Circuit breaker OPEN\nPair: {pair}\nTrading paused.")

    async def on_circuit_breaker_closed(self):
        await self._send("Circuit breaker CLOSED\nTrading resumed.")

    async def on_bot_started(self, pairs: list[str]):
        await self._send(f"Bot started\nPairs: {', '.join(pairs)}")

    async def on_bot_stopped(self):
        await self._send("Bot stopped.")

    # ── Telegram commands ─────────────────────────────────────────────────────

    async def _cmd_start(self, message: Message):
        await message.answer(
            "Arb bot connected.\n\n"
            "/status    — bot state\n"
            "/pnl       — PnL summary\n"
            "/inventory — balances\n"
            "/signals   — last 5 signals\n"
            "/winrate   — win rate stats\n"
            "/pause     — pause trading\n"
            "/resume    — resume trading"
        )

    async def _cmd_status(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        cb = self._arb_bot.executor.circuit_breaker
        cb_state = "OPEN" if cb.is_open() else "closed"
        win_rate = self._arb_bot.scorer.recent_results
        wins = sum(1 for _, ok in win_rate[-20:] if ok)
        total = len(win_rate[-20:])
        wr_str = f"{wins}/{total}" if total else "no trades yet"
        if not self._arb_bot.running:
            status = "stopped"
        elif self._arb_bot.paused:
            status = "paused"
        else:
            status = "running"
        await message.answer(
            f"Status: {status}\n"
            f"Circuit breaker: {cb_state}\n"
            f"Win rate (last 20): {wr_str}\n"
            f"Pairs: {', '.join(self._arb_bot.pairs)}"
        )

    async def _cmd_pnl(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        if not self._arb_bot.pnl_engine.trades:
            await message.answer("No trades recorded yet.")
            return
        s = self._arb_bot.pnl_engine.summary()
        total = s['total_pnl_usd']
        sign = "+" if total >= 0 else ""
        await message.answer(
            f"PnL summary\n"
            f"Trades: {s['total_trades']}\n"
            f"Total:  {sign}${total:.4f}"
        )

    async def _cmd_inventory(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        snap = self._arb_bot.inventory.snapshot()
        totals = snap['totals']

        # show only assets that appear in configured trading pairs
        relevant = set()
        for pair in self._arb_bot.pairs:
            base, quote = pair.split('/')
            relevant.add(base)
            relevant.add(quote)

        lines = []
        for asset in sorted(relevant):
            amt = totals.get(asset, Decimal('0'))
            lines.append(f"{asset}: {amt:.4f}")

        if not lines:
            await message.answer("No inventory data.")
            return
        await message.answer("Balances\n" + "\n".join(lines))

    async def _cmd_signals(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        log = self._arb_bot.signal_log[-5:]
        if not log:
            await message.answer("No signals yet.")
            return
        lines = []
        for s in reversed(log):
            pnl_str = f"  PnL: ${s['pnl']:.4f}" if s['pnl'] is not None else ""
            lines.append(
                f"{s['time']} {s['pair']}\n"
                f"  spread={s['spread']:.1f}bps score={s['score']} [{s['result']}]{pnl_str}"
            )
        await message.answer("Last signals\n\n" + "\n\n".join(lines))

    async def _cmd_winrate(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        results = self._arb_bot.scorer.recent_results
        if not results:
            await message.answer("No trades yet.")
            return
        lines = []
        for n in [10, 20, 50]:
            window = results[-n:]
            if not window:
                continue
            wins = sum(1 for _, ok in window if ok)
            lines.append(f"Last {n:2d}: {wins}/{len(window)} ({wins/len(window)*100:.0f}%)")
        await message.answer("Win rate\n" + "\n".join(lines))

    async def _cmd_pause(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        self._arb_bot.paused = True
        await message.answer("Bot paused. Send /resume to continue.")

    async def _cmd_resume(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        self._arb_bot.paused = False
        await message.answer("Bot resumed.")

    # ── Polling loop ──────────────────────────────────────────────────────────

    async def start_polling(self, handle_signals: bool = True):
        await self.dp.start_polling(self.bot, handle_signals=handle_signals)

    async def stop(self):
        await self.bot.session.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _send(self, text: str):
        try:
            await self.bot.send_message(self.chat_id, text)
        except Exception as e:
            logging.warning(f"Telegram send failed: {e}")
