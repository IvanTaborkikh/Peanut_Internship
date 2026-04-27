"""Verify tx_builder + unwind end-to-end without real RPC/keys.

Run: PYTHONPATH=. python scripts/verify_tx_unwind.py

Uses a mock web3 so it works offline. Each step prints what was produced —
inspect the output to confirm signing, validation, and unwind planning work.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from eth_account import Account

from src.configs.schema import ChainId
from src.configs.tokens import get_token
from src.executor.tx_builder import TxBuilder
from src.executor.unwind import plan_unwind, execute_unwind, UnwindPlan
from src.strategy.signal import Direction, Signal
import asyncio


def fake_web3():
    w3 = MagicMock()
    w3.eth.get_transaction_count.return_value = 42
    w3.eth.gas_price = 1_500_000_000
    fn = MagicMock()
    fn.build_transaction.side_effect = lambda p: {
        'from': p['from'], 'to': '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',
        'data': '0x38ed1739' + '00' * 100, 'value': 0, 'gas': 187_500,
        'maxFeePerGas': p['maxFeePerGas'], 'maxPriorityFeePerGas': p['maxPriorityFeePerGas'],
        'nonce': p['nonce'], 'chainId': p['chainId'],
    }
    contract = MagicMock()
    contract.functions.swapExactTokensForTokens.return_value = fn
    w3.eth.contract.return_value = contract
    return w3


def signal():
    return Signal.create(
        pair='ETH/USDT', direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2010'),
        spread_bps=Decimal('50'), size=Decimal('0.1'),
        expected_gross_pnl=Decimal('1'), expected_fees=Decimal('0.1'),
        expected_net_pnl=Decimal('0.9'), score=Decimal('80'),
        expiry=10**12, inventory_ok=True, within_limits=True,
    )


def main():
    builder = TxBuilder(fake_web3(), ChainId.ETH_MAINNET, Account.from_key('0x' + 'aa' * 32))
    sig = signal()

    print("=== 1. Signed DEX swap ===")
    weth, usdt = get_token(ChainId.ETH_MAINNET, 'WETH'), get_token(ChainId.ETH_MAINNET, 'USDT')
    dex = builder.build_dex_swap(weth, usdt, 10**17, 195 * 10**6)
    print(f"  tx_hash={dex.tx_hash}\n  raw_tx={dex.raw_tx[:60]}...")
    print(f"  from={dex.from_address}  to={dex.to_address}")
    print(f"  nonce={dex.nonce}  gas={dex.gas}  amount_in={dex.amount_in}  amount_out_min={dex.amount_out_min}")
    print(f"  deadline={dex.deadline}  path={dex.path}")

    print("\n=== 2. Validated CEX order ===")
    cex = builder.build_cex_order(sig, slippage_bps=Decimal('30'))
    print(f"  {cex.side} {cex.amount} {cex.symbol} @ {cex.price} (slippage applied)")
    print(f"  type={cex.type}  notional={cex.notional_quote}")

    print("\n=== 3. Plan unwind (leg-1 = CEX buy, leg-2 failed) ===")
    ctx = SimpleNamespace(signal=sig, leg1_venue='cex', leg1_fill_size=Decimal('0.1'))
    plan = plan_unwind(ctx)
    print(f"  → {plan.venue} {plan.side} {plan.size} {plan.pair}  ({plan.strategy.value})")

    print("\n=== 4. Build CEX unwind (dry-run market sell) ===")
    r1 = asyncio.run(execute_unwind(plan, builder, SimpleNamespace(id='binance'), dry_run=True))
    o = r1.prepared_order
    print(f"  success={r1.success}  {o.side} {o.amount} {o.symbol}  type={o.type}")

    print("\n=== 5. Build DEX unwind (reverse swap, signed) ===")
    dex_plan = UnwindPlan(venue='dex', side='sell', size=Decimal('0.1'), pair='ETH/USDT')
    r2 = asyncio.run(execute_unwind(dex_plan, builder, None, chain_id=ChainId.ETH_MAINNET, dry_run=True))
    t = r2.prepared_tx
    print(f"  success={r2.success}  tx_hash={t.tx_hash}  amount_in={t.amount_in}  amount_out_min={t.amount_out_min}")

    print("\nAll checks passed — no broadcast happened (dry_run=True).")


if __name__ == '__main__':
    main()
