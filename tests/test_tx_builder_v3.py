"""TxBuilder V3 path: exactInputSingle calldata is built and signed."""
from unittest.mock import MagicMock

from eth_account import Account

from src.configs.schema import ChainId
from src.configs.tokens import UNISWAP_V3_ROUTERS, get_token
from src.executor.tx_builder import TxBuilder, PreparedDexTx


def _mock_web3_for_v3_signing():
    """Mock web3 that lets contract.functions.exactInputSingle.build_transaction
    return a sign-able tx dict echoed from the params."""
    w3 = MagicMock()
    w3.eth.get_transaction_count.return_value = 11
    w3.eth.gas_price = 100_000_000  # 0.1 gwei (Arbitrum-typical)
    fn_call = MagicMock()

    def _build_tx(params):
        return {
            'from':  params['from'],
            'to':    UNISWAP_V3_ROUTERS[ChainId.ARBITRUM].checksum,
            'data':  '0x04e45aaf' + '00' * 200,  # exactInputSingle selector
            'value': params.get('value', 0),
            'gas':   200_000,
            'maxFeePerGas':         params.get('maxFeePerGas', 200_000_000),
            'maxPriorityFeePerGas': params.get('maxPriorityFeePerGas', 100_000_000),
            'nonce':   params['nonce'],
            'chainId': params['chainId'],
        }

    fn_call.build_transaction.side_effect = _build_tx
    contract = MagicMock()
    contract.functions.exactInputSingle.return_value = fn_call
    w3.eth.contract.return_value = contract
    return w3, contract


def test_v3_builder_uses_v3_router_address():
    w3, _ = _mock_web3_for_v3_signing()
    account = Account.from_key('0x' + 'aa' * 32)
    builder = TxBuilder(w3, ChainId.ARBITRUM, account, dex_version='v3', fee_tier=100)

    assert builder.dex_version == 'v3'
    assert builder.fee_tier == 100
    assert builder.router_address == UNISWAP_V3_ROUTERS[ChainId.ARBITRUM].checksum


def test_v3_builder_calls_exact_input_single():
    w3, contract = _mock_web3_for_v3_signing()
    account = Account.from_key('0x' + 'bb' * 32)
    builder = TxBuilder(w3, ChainId.ARBITRUM, account, dex_version='v3', fee_tier=100)

    usdc = get_token(ChainId.ARBITRUM, 'USDC')
    chip = get_token(ChainId.ARBITRUM, 'CHIP')

    prepared = builder.build_dex_swap(usdc, chip, 25 * 10**6, 400 * 10**18)

    assert isinstance(prepared, PreparedDexTx)
    assert prepared.raw_tx.startswith('0x')
    assert prepared.amount_in == 25 * 10**6
    assert prepared.amount_out_min == 400 * 10**18
    contract.functions.exactInputSingle.assert_called_once()
    # Verify the params tuple shape: (tokenIn, tokenOut, fee, recipient, amountIn, amountOutMin, sqrtPriceLimit)
    call_args = contract.functions.exactInputSingle.call_args[0][0]
    assert len(call_args) == 7
    assert call_args[0] == usdc.address.checksum
    assert call_args[1] == chip.address.checksum
    assert call_args[2] == 100  # fee tier
    assert call_args[3] == account.address  # recipient
    assert call_args[4] == 25 * 10**6
    assert call_args[5] == 400 * 10**18
    assert call_args[6] == 0  # sqrtPriceLimitX96


def test_v2_default_still_uses_swapExactTokensForTokens():
    """Regression: existing V2 callers (no dex_version) keep old behaviour."""
    w3 = MagicMock()
    w3.eth.get_transaction_count.return_value = 5
    w3.eth.gas_price = 1_000_000_000

    fn_call = MagicMock()

    def _build_tx(params):
        return {
            'from':  params['from'],
            'to':    '0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24',
            'data':  '0x38ed1739' + '00' * 100,
            'value': 0,
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

    account = Account.from_key('0x' + 'cc' * 32)
    builder = TxBuilder(w3, ChainId.ARBITRUM, account)  # no dex_version → 'v2'

    assert builder.dex_version == 'v2'
    weth = get_token(ChainId.ARBITRUM, 'WETH')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')
    builder.build_dex_swap(weth, usdc, 10**17, 195 * 10**6)
    contract.functions.swapExactTokensForTokens.assert_called_once()
