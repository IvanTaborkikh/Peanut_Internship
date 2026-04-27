"""tx_builder + unwind tests: signed DEX tx via mock web3, unwind planning, dry-run building."""
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest
from eth_account import Account

from src.configs.schema import ChainId
from src.configs.tokens import get_token
from src.executor.tx_builder import TxBuilder, PreparedCexOrder, PreparedDexTx
from src.executor.unwind import (
    UnwindStrategy,
    plan_unwind,
)
from src.strategy.signal import Direction, Signal


# --- Helpers ---

def _signal(direction=Direction.BUY_CEX_SELL_DEX, size='0.1', cex_price='2000', dex_price='2010') -> Signal:
    return Signal.create(
        pair='ETH/USDT',
        direction=direction,
        cex_price=Decimal(cex_price),
        dex_price=Decimal(dex_price),
        spread_bps=Decimal('50'),
        size=Decimal(size),
        expected_gross_pnl=Decimal('1'),
        expected_fees=Decimal('0.1'),
        expected_net_pnl=Decimal('0.9'),
        score=Decimal('80'),
        expiry=10**12,
        inventory_ok=True,
        within_limits=True,
    )


def _mock_web3_for_signing():
    """Mock web3 that lets contract.functions.X.build_transaction work end-to-end.

    build_transaction returns a tx dict whose 'from' echoes the caller's params,
    so eth_account sign_transaction accepts it.
    """
    w3 = MagicMock()
    w3.eth.get_transaction_count.return_value = 7
    w3.eth.gas_price = 1_000_000_000  # 1 gwei
    fn_call = MagicMock()

    def _build_tx(params):
        return {
            'from':  params['from'],
            'to':    '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',
            'data':  '0x38ed1739' + '00' * 100,
            'value': params.get('value', 0),
            'gas':   180_000,
            'maxFeePerGas':         params.get('maxFeePerGas', 3_000_000_000),
            'maxPriorityFeePerGas': params.get('maxPriorityFeePerGas', 2_000_000_000),
            'nonce':   params['nonce'],
            'chainId': params['chainId'],
        }

    fn_call.build_transaction.side_effect = _build_tx
    contract = MagicMock()
    contract.functions.swapExactTokensForTokens.return_value = fn_call
    w3.eth.contract.return_value = contract
    return w3


# --- TxBuilder ---

def test_tx_builder_signs_dex_swap():
    w3 = _mock_web3_for_signing()
    account = Account.from_key('0x' + '11' * 32)
    builder = TxBuilder(w3, ChainId.ETH_MAINNET, account)

    weth = get_token(ChainId.ETH_MAINNET, 'WETH')
    usdt = get_token(ChainId.ETH_MAINNET, 'USDT')

    prepared = builder.build_dex_swap(weth, usdt, 10**17, 195 * 10**6)

    assert isinstance(prepared, PreparedDexTx)
    assert prepared.raw_tx.startswith('0x')
    assert prepared.tx_hash.startswith('0x')
    assert prepared.from_address == account.address
    assert prepared.chain_id == 1
    assert prepared.nonce == 7
    assert prepared.amount_in == 10**17
    assert prepared.amount_out_min == 195 * 10**6
    assert len(prepared.path) == 2
    assert prepared.gas == 180_000


def test_tx_builder_cex_order_buy():
    w3 = _mock_web3_for_signing()
    account = Account.from_key('0x' + '22' * 32)
    builder = TxBuilder(w3, ChainId.ETH_MAINNET, account)

    sig = _signal(direction=Direction.BUY_CEX_SELL_DEX)
    order = builder.build_cex_order(sig, slippage_bps=Decimal('30'))

    assert order.side == 'buy'
    assert order.amount == sig.size
    # buy slippage: price > cex_price
    assert order.price > sig.cex_price
    assert order.notional_quote == order.amount * order.price


def test_tx_builder_cex_order_sell():
    w3 = _mock_web3_for_signing()
    account = Account.from_key('0x' + '33' * 32)
    builder = TxBuilder(w3, ChainId.ETH_MAINNET, account)

    sig = _signal(direction=Direction.BUY_DEX_SELL_CEX)
    order = builder.build_cex_order(sig, slippage_bps=Decimal('30'))

    assert order.side == 'sell'
    # sell slippage: price < cex_price
    assert order.price < sig.cex_price


def test_tx_builder_market_order():
    w3 = _mock_web3_for_signing()
    account = Account.from_key('0x' + '44' * 32)
    builder = TxBuilder(w3, ChainId.ETH_MAINNET, account)

    order = builder.build_cex_market('ETH/USDT', 'sell', Decimal('0.1'))
    assert order.type == 'market'
    assert order.price is None
    assert order.side == 'sell'


def test_tx_builder_validates_min_amount():
    w3 = _mock_web3_for_signing()
    account = Account.from_key('0x' + '55' * 32)
    exchange = SimpleNamespace(
        id='binance',
        markets={'ETH/USDT': {'limits': {'amount': {'min': '1.0'}, 'cost': {'min': '10'}}}},
    )
    builder = TxBuilder(w3, ChainId.ETH_MAINNET, account, exchange_client=exchange)
    sig = _signal(size='0.001')   # below 1.0 min
    with pytest.raises(ValueError, match="below min amount"):
        builder.build_cex_order(sig, slippage_bps=Decimal('30'))


# --- Unwind plan ---

def _ctx(direction, leg1_venue, leg1_fill_size='0.1'):
    sig = _signal(direction=direction)
    return SimpleNamespace(
        signal=sig,
        leg1_venue=leg1_venue,
        leg1_fill_size=Decimal(leg1_fill_size),
    )


def test_plan_unwind_cex_first_buy_direction():
    plan = plan_unwind(_ctx(Direction.BUY_CEX_SELL_DEX, 'cex'))
    assert plan.venue == 'cex'
    assert plan.side == 'sell'
    assert plan.strategy == UnwindStrategy.MARKET


def test_plan_unwind_dex_first_buy_direction():
    plan = plan_unwind(_ctx(Direction.BUY_CEX_SELL_DEX, 'dex'))
    assert plan.venue == 'dex'
    assert plan.side == 'buy'   # leg-1 was sell on DEX → buy back


def test_plan_unwind_cex_first_sell_direction():
    plan = plan_unwind(_ctx(Direction.BUY_DEX_SELL_CEX, 'cex'))
    assert plan.venue == 'cex'
    assert plan.side == 'buy'   # leg-1 sold on CEX → buy back


def test_plan_unwind_no_fill_returns_none():
    plan = plan_unwind(_ctx(Direction.BUY_CEX_SELL_DEX, 'cex', leg1_fill_size='0'))
    assert plan is None


# --- execute_unwind in dry_run ---

def test_execute_unwind_cex_dry_run_returns_prepared_order():
    from src.executor.unwind import execute_unwind, UnwindPlan
    plan = UnwindPlan(venue='cex', side='sell', size=Decimal('0.1'), pair='ETH/USDT')
    result = asyncio.run(execute_unwind(
        plan, tx_builder=None, exchange_client=SimpleNamespace(id='binance'), dry_run=True,
    ))
    assert result.success is True
    assert isinstance(result.prepared_order, PreparedCexOrder)
    assert result.prepared_order.type == 'market'
    assert result.prepared_order.side == 'sell'


def test_execute_unwind_dex_dry_run_returns_prepared_tx():
    from src.executor.unwind import execute_unwind, UnwindPlan
    w3 = _mock_web3_for_signing()
    account = Account.from_key('0x' + '66' * 32)
    builder = TxBuilder(w3, ChainId.ETH_MAINNET, account)
    plan = UnwindPlan(venue='dex', side='sell', size=Decimal('0.1'), pair='ETH/USDT')

    result = asyncio.run(execute_unwind(
        plan, builder, exchange_client=None, chain_id=ChainId.ETH_MAINNET, dry_run=True,
    ))
    assert result.success is True
    assert isinstance(result.prepared_tx, PreparedDexTx)
    assert result.prepared_tx.amount_in > 0


def test_execute_unwind_dex_without_builder_fails():
    from src.executor.unwind import execute_unwind, UnwindPlan
    plan = UnwindPlan(venue='dex', side='sell', size=Decimal('0.1'), pair='ETH/USDT')
    result = asyncio.run(execute_unwind(
        plan, tx_builder=None, exchange_client=None, dry_run=True,
    ))
    assert result.success is False
    assert 'tx_builder' in (result.error or '')
