import asyncio
import logging
import time
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
        # Control plane added in Week 5 hardening.
        self.router.message.register(self._cmd_limits,                Command("limits"))
        self.router.message.register(self._cmd_set_max_trade,         Command("set_max_trade"))
        self.router.message.register(self._cmd_set_max_daily_loss,    Command("set_max_daily_loss"))
        self.router.message.register(self._cmd_set_consecutive_limit, Command("set_consecutive_limit"))
        self.router.message.register(self._cmd_metrics,               Command("metrics"))
        self.router.message.register(self._cmd_errors,                Command("errors"))
        self.router.message.register(self._cmd_enable_real_trading,   Command("enable_real_trading"))
        self.router.message.register(self._cmd_confirm_real_trading,  Command("confirm_real_trading"))
        self.router.message.register(self._cmd_key_status,            Command("key_status"))
        self.router.message.register(self._cmd_shutdown,              Command("shutdown"))

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
            "Read-only:\n"
            "/status    — bot state\n"
            "/pnl       — PnL summary\n"
            "/inventory — balances\n"
            "/signals   — last 5 signals\n"
            "/winrate   — win rate stats\n"
            "/limits    — current risk limits\n"
            "/metrics   — pipeline counters\n"
            "/errors    — recent errors by type\n"
            "/key_status — Binance API key validity & expiration\n\n"
            "Control:\n"
            "/pause [min]              — pause N min (no arg = until /resume)\n"
            "/resume                   — resume now\n"
            "/set_max_trade <usd>      — update limit\n"
            "/set_max_daily_loss <usd> — update limit\n"
            "/set_consecutive_limit <n>— update limit\n"
            "/enable_real_trading      — start DRY→REAL flip\n"
            "/confirm_real_trading     — confirm within 60s\n"
            "/shutdown                 — fully terminate the bot"
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
        arg = _arg_after_command(message.text)
        if arg:
            try:
                minutes = int(arg)
                if minutes <= 0:
                    raise ValueError
            except ValueError:
                await message.answer("Usage: /pause <minutes>  (positive int) or /pause")
                return
            self._arb_bot.pause_manager.pause(minutes, "manual via Telegram")
            await message.answer(f"⏸️ Trading paused {minutes}min.")
        else:
            self._arb_bot.paused = True
            await message.answer("Bot paused (indefinite). Send /resume to continue.")

    async def _cmd_resume(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        self._arb_bot.paused = False
        self._arb_bot.pause_manager.cancel()
        await message.answer("✅ Trading resumed.")

    # ── Control plane (limits, metrics, real-trading flip) ────────────────────

    async def _cmd_limits(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        lim = self._arb_bot.risk_manager.limits
        await message.answer(
            "📋 Risk limits:\n"
            f"max_trade_usd:          ${lim.max_trade_usd}\n"
            f"max_trade_pct:          {float(lim.max_trade_pct)*100:.0f}%\n"
            f"max_loss_per_trade:     ${lim.max_loss_per_trade}\n"
            f"max_daily_loss:         ${lim.max_daily_loss}\n"
            f"max_drawdown_pct:       {float(lim.max_drawdown_pct)*100:.0f}%\n"
            f"max_trades_per_hour:    {lim.max_trades_per_hour}\n"
            f"consecutive_loss_limit: {lim.consecutive_loss_limit}"
        )

    async def _cmd_set_max_trade(self, message: Message):
        await self._update_limit(message, 'max_trade_usd')

    async def _cmd_set_max_daily_loss(self, message: Message):
        await self._update_limit(message, 'max_daily_loss')

    async def _cmd_set_consecutive_limit(self, message: Message):
        await self._update_limit(message, 'consecutive_loss_limit')

    async def _update_limit(self, message: Message, name: str):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        arg = _arg_after_command(message.text)
        if not arg:
            await message.answer(f"Usage: /set_{name.replace('_usd','')} <value>")
            return
        ok, msg = self._arb_bot.risk_manager.update_limit(name, arg)
        await message.answer(("✅ " if ok else "❌ ") + msg)

    async def _cmd_metrics(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        m = self._arb_bot.metrics
        await message.answer(
            "📈 Metrics (session):\n"
            f"signals_generated:        {m.signals_generated}\n"
            f"signals_passed_score:     {m.signals_passed_score}\n"
            f"signals_passed_validator: {m.signals_passed_validator}\n"
            f"signals_passed_risk:      {m.signals_passed_risk}\n"
            f"trades_executed:          {m.trades_executed}\n"
            f"trades_successful:        {m.trades_successful}\n"
            f"trades_failed:            {m.trades_failed}\n"
            f"execution_rate:           {m.execution_rate:.1f}%"
        )

    async def _cmd_errors(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        summary = self._arb_bot.error_tracker.summary()
        if not summary:
            await message.answer("✅ No errors in last hour.")
            return
        lines = [f"{k}: {v}" for k, v in sorted(summary.items())]
        await message.answer("❌ Errors (last hour):\n" + "\n".join(lines))

    async def _cmd_enable_real_trading(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        if not self._arb_bot.dry_run:
            await message.answer("✅ Already in REAL TRADING mode.")
            return
        self._arb_bot._real_trading_pending_at = time.time()
        ttl = int(self._arb_bot._real_trading_pending_ttl)
        await message.answer(
            f"❓ Switch to REAL MONEY mode? Confirm within {ttl}s with:\n"
            "/confirm_real_trading"
        )

    async def _cmd_confirm_real_trading(self, message: Message):
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        bot = self._arb_bot
        pending = bot._real_trading_pending_at
        if pending is None:
            await message.answer(
                "❌ No pending switch. Send /enable_real_trading first."
            )
            return
        if time.time() - pending > bot._real_trading_pending_ttl:
            bot._real_trading_pending_at = None
            await message.answer("❌ Confirmation expired. Resend /enable_real_trading.")
            return

        bot.dry_run = False
        # Both layers must flip — executor enforces its own dry-run gate.
        try:
            bot.executor.config.simulation_mode = False
        except AttributeError:
            pass
        try:
            if hasattr(bot.executor.config, 'dry_run'):
                bot.executor.config.dry_run = False
        except AttributeError:
            pass
        bot._real_trading_pending_at = None
        logging.critical("🚨 REAL TRADING ENABLED 🚨 — DRY_RUN flipped via Telegram")
        await message.answer(
            "🚨 REAL TRADING ENABLED 🚨\n"
            "Bot will now execute real trades."
        )

    async def _cmd_shutdown(self, message: Message):
        """Fully terminate the bot. Sends SIGTERM to the current process so the
        signal handler in ArbBot.run() cancels every task (trading loop,
        polling, heartbeat) and the finally-block runs cleanly."""
        if self._arb_bot is None:
            await message.answer("Bot reference not set.")
            return
        await message.answer("🛑 Shutting down — bot will exit in a moment.")
        self._arb_bot.running = False
        import os
        import signal as _signal
        os.kill(os.getpid(), _signal.SIGTERM)

    async def _cmd_key_status(self, message: Message):
        if self._arb_bot is None or not getattr(self._arb_bot, '_key_health', None):
            await message.answer("Key health check not initialized.")
            return
        status = await asyncio.to_thread(self._arb_bot._key_health.check)
        if not status.valid:
            await message.answer(f"❌ Invalid: {status.error_msg}")
            return
        if status.expires_at:
            await message.answer(
                f"✅ Valid\nExpires: {status.expires_at.isoformat(timespec='minutes')}\n"
                f"Days remaining: {status.days_remaining:.1f}"
            )
        elif status.ip_restricted:
            await message.answer("✅ Valid (IP-whitelisted, no expiration)")
        else:
            await message.answer("✅ Valid (expiration unknown)")

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

    async def send_text(self, text: str):
        """Public outbound helper (used by ArbBot for ad-hoc alerts)."""
        await self._send(text)


def _arg_after_command(text: str | None) -> str:
    """Extract the argument substring after `/cmd `. Empty string if none."""
    if not text:
        return ""
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""
