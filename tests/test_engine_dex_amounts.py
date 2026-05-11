"""Verify _execute_dex_leg computes correct amount_in/amount_out for both
directions. Bug #6 incident on 2026-05-09 used `size * 10^token_in.decimals`
unconditionally, which is wrong for BUY_DEX_SELL_CEX (token_in is the QUOTE
asset, not the BASE)."""
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

from src.executor.engine import Executor, ExecutorConfig
from src.strategy.signal import Direction, Signal
from src.configs.schema import ChainId


def _signal(direction, size='130', dex_price='0.067', cex_price='0.067'):
    return Signal.create(
        pair='CHIP/USDC',
        direction=direction,
        cex_price=Decimal(cex_price),
        dex_price=Decimal(dex_price),
        spread_bps=Decimal('40'),
        size=Decimal(size),
        expected_gross_pnl=Decimal('0.05'),
        expected_fees=Decimal('0.04'),
        expected_net_pnl=Decimal('0.01'),
        score=Decimal('50'),
        expiry=10**12,
        inventory_ok=True,
        within_limits=True,
    )


def _executor_with_capturing_builder(direction):
    """Wire an Executor whose tx_builder.build_dex_swap captures its args."""
    captured = {}

    def _capture(token_in, token_out, amount_in_wei, amount_out_min_wei, deadline_seconds=60):
        captured['token_in'] = token_in
        captured['token_out'] = token_out
        captured['amount_in_wei'] = amount_in_wei
        captured['amount_out_min_wei'] = amount_out_min_wei
        prepared = MagicMock()
        prepared.tx_hash = '0x' + '00' * 32
        prepared.gas = 200_000
        prepared.amount_out_min = amount_out_min_wei
        prepared.amount_in = amount_in_wei
        prepared.raw_tx = '0x'
        return prepared

    builder = MagicMock()
    builder.build_dex_swap.side_effect = _capture
    builder.web3 = MagicMock()

    cfg = ExecutorConfig(simulation_mode=False, dry_run=True, slippage_bps=Decimal('50'))
    ex = Executor(MagicMock(), MagicMock(), MagicMock(), config=cfg,
                  tx_builder=builder, chain_id=ChainId.ARBITRUM)
    return ex, captured


def test_dex_amounts_buy_cex_sell_dex():
    """CHIP→USDC on DEX: amount_in is in CHIP, amount_out_min is in USDC."""
    ex, captured = _executor_with_capturing_builder(Direction.BUY_CEX_SELL_DEX)
    sig = _signal(Direction.BUY_CEX_SELL_DEX)

    asyncio.run(ex._execute_dex_leg(sig, sig.size))

    # CHIP has 18 decimals
    assert captured['amount_in_wei'] == 130 * 10**18
    # USDC has 6 decimals; out_min = 130 * 0.067 * 0.995 ≈ 8.67 USDC = 8669449 wei
    assert captured['amount_out_min_wei'] == int(Decimal('130') * Decimal('0.067') * 10**6 * Decimal('0.995'))


def test_dex_amounts_buy_dex_sell_cex():
    """USDC→CHIP on DEX: amount_in is in USDC, amount_out_min is in CHIP.

    Bug #6 regression: pre-fix code computed amount_in_wei = size * 10^USDC_decimals
    = 130 * 10^6 = 130 USDC, instead of size * dex_price * 10^USDC_decimals = 8.71 USDC.
    """
    ex, captured = _executor_with_capturing_builder(Direction.BUY_DEX_SELL_CEX)
    sig = _signal(Direction.BUY_DEX_SELL_CEX)

    asyncio.run(ex._execute_dex_leg(sig, sig.size))

    # USDC has 6 decimals; in = 130 * 0.067 = 8.71 USDC = 8710000 wei
    expected_in = int(Decimal('130') * Decimal('0.067') * 10**6)
    assert captured['amount_in_wei'] == expected_in, \
        f"Bug #6 regression: should send {expected_in} (8.71 USDC), got {captured['amount_in_wei']}"
    # Specifically NOT 130 * 10^6 = 130 USDC
    assert captured['amount_in_wei'] != 130 * 10**6

    # CHIP has 18 decimals; out_min = 130 * 0.995 = 129.35 CHIP
    assert captured['amount_out_min_wei'] == int(Decimal('130') * 10**18 * Decimal('0.995'))
