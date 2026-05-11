"""Build & sign DEX swap transactions and validate CEX orders.

Both builders return rich descriptors but never broadcast / submit.
Broadcast is a separate, gated step done by the Executor when dry_run=False.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
import time

from eth_account.signers.local import LocalAccount
from web3 import Web3

from src.configs.schema import ChainId
from src.configs.tokens import get_router, get_v3_router
from src.core.types import Token
from src.strategy.signal import Direction, Signal


# Minimal Uniswap V2 Router ABI — only what we use.
UNISWAP_V2_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn",     "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path",       "type": "address[]"},
            {"internalType": "address", "name": "to",           "type": "address"},
            {"internalType": "uint256", "name": "deadline",     "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


# Minimal SwapRouter02 ABI — only exactInputSingle, the single-hop variant
# used for direct CHIP/USDC, ETH/USDC, etc. Multi-hop V3 routing would need
# `exactInput` with an encoded path — out of scope here.
UNISWAP_V3_ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"internalType": "address", "name": "tokenIn",           "type": "address"},
                {"internalType": "address", "name": "tokenOut",          "type": "address"},
                {"internalType": "uint24",  "name": "fee",               "type": "uint24"},
                {"internalType": "address", "name": "recipient",         "type": "address"},
                {"internalType": "uint256", "name": "amountIn",          "type": "uint256"},
                {"internalType": "uint256", "name": "amountOutMinimum",  "type": "uint256"},
                {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
            "name": "params",
            "type": "tuple",
        }],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]


@dataclass
class PreparedDexTx:
    """A signed Uniswap V2 swap, ready to broadcast via send_raw_transaction."""
    raw_tx: str            # 0x-prefixed hex
    tx_hash: str           # predicted tx hash
    chain_id: int
    from_address: str
    to_address: str        # router
    nonce: int
    value: int
    gas: int
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    amount_in: int
    amount_out_min: int
    path: list[str]
    deadline: int
    calldata: str          # 0x-prefixed hex


@dataclass
class PreparedCexOrder:
    """Validated CEX order params, ready for create_order()."""
    exchange: str
    symbol: str
    side: str              # 'buy' | 'sell'
    type: str              # 'limit' | 'market'
    amount: Decimal
    price: Optional[Decimal]
    time_in_force: str = 'IOC'
    notional_quote: Decimal = field(default=Decimal('0'))


class TxBuilder:
    """Builds (and signs, for DEX) the exact transactions an Executor would submit.

    `dex_version` selects between Uniswap V2 (`swapExactTokensForTokens`,
    address-array path) and Uniswap V3 SwapRouter02 (`exactInputSingle`,
    fee-tier-keyed pool). For V3, `fee_tier` is required (100/500/3000/10000).
    """

    def __init__(
        self,
        web3: Web3,
        chain_id: ChainId,
        account: LocalAccount,
        exchange_client=None,
        priority_fee_gwei: int = 2,
        dex_version: str = 'v2',
        fee_tier: int = 3000,
    ):
        self.web3 = web3
        self.chain_id = chain_id
        self.account = account
        self.exchange = exchange_client
        self.priority_fee_wei = Web3.to_wei(priority_fee_gwei, 'gwei')
        self.dex_version = dex_version.lower()
        self.fee_tier = int(fee_tier)

        if self.dex_version == 'v3':
            router_addr = get_v3_router(chain_id).checksum
            self.router_address = router_addr
            self.router = web3.eth.contract(address=router_addr, abi=UNISWAP_V3_ROUTER_ABI)
        else:
            router_addr = get_router(chain_id).checksum
            self.router_address = router_addr
            self.router = web3.eth.contract(address=router_addr, abi=UNISWAP_V2_ROUTER_ABI)

    def build_dex_swap(
        self,
        token_in: Token,
        token_out: Token,
        amount_in_wei: int,
        amount_out_min_wei: int,
        deadline_seconds: int = 60,
    ) -> PreparedDexTx:
        deadline = int(time.time()) + deadline_seconds
        recipient = self.account.address
        path = [token_in.address.checksum, token_out.address.checksum]

        if self.dex_version == 'v3':
            params = (
                token_in.address.checksum,
                token_out.address.checksum,
                self.fee_tier,
                recipient,
                amount_in_wei,
                amount_out_min_wei,
                0,  # sqrtPriceLimitX96=0 disables price-limit guard; rely on amountOutMinimum.
            )
            fn = self.router.functions.exactInputSingle(params)
        else:
            fn = self.router.functions.swapExactTokensForTokens(
                amount_in_wei, amount_out_min_wei, path, recipient, deadline,
            )

        nonce = self.web3.eth.get_transaction_count(recipient)
        base_fee = self.web3.eth.gas_price
        max_priority = self.priority_fee_wei
        max_fee = base_fee + max_priority

        tx = fn.build_transaction({
            'from': recipient,
            'value': 0,
            'nonce': nonce,
            'chainId': int(self.chain_id),
            'maxFeePerGas': max_fee,
            'maxPriorityFeePerGas': max_priority,
        })

        signed = self.account.sign_transaction(tx)
        raw = signed.raw_transaction
        tx_hash = signed.hash

        return PreparedDexTx(
            raw_tx='0x' + raw.hex() if not raw.hex().startswith('0x') else raw.hex(),
            tx_hash='0x' + tx_hash.hex() if not tx_hash.hex().startswith('0x') else tx_hash.hex(),
            chain_id=int(self.chain_id),
            from_address=recipient,
            to_address=self.router_address,
            nonce=nonce,
            value=0,
            gas=int(tx['gas']),
            max_fee_per_gas=max_fee,
            max_priority_fee_per_gas=max_priority,
            amount_in=amount_in_wei,
            amount_out_min=amount_out_min_wei,
            path=path,
            deadline=deadline,
            calldata=tx['data'],
        )

    def build_cex_order(
        self,
        signal: Signal,
        slippage_bps: Decimal,
        size: Optional[Decimal] = None,
        order_type: str = 'limit',
    ) -> PreparedCexOrder:
        """Build & validate a CEX order against market metadata, but do NOT submit."""
        amount = size if size is not None else signal.size
        if signal.direction == Direction.BUY_CEX_SELL_DEX:
            side = 'buy'
            price = signal.cex_price * (Decimal('1') + slippage_bps / Decimal('10000'))
        else:
            side = 'sell'
            price = signal.cex_price * (Decimal('1') - slippage_bps / Decimal('10000'))

        notional = amount * price
        self._validate_against_market(signal.pair, amount, price, notional)

        return PreparedCexOrder(
            exchange=getattr(self.exchange, 'id', 'binance'),
            symbol=signal.pair,
            side=side,
            type=order_type,
            amount=amount,
            price=None if order_type == 'market' else price,
            time_in_force='IOC',
            notional_quote=notional,
        )

    def build_cex_market(self, pair: str, side: str, size: Decimal) -> PreparedCexOrder:
        """Plain market order builder used by unwind paths."""
        self._validate_against_market(pair, size, None, None)
        return PreparedCexOrder(
            exchange=getattr(self.exchange, 'id', 'binance'),
            symbol=pair,
            side=side,
            type='market',
            amount=size,
            price=None,
            time_in_force='IOC',
            notional_quote=Decimal('0'),
        )

    def _validate_against_market(
        self,
        pair: str,
        amount: Decimal,
        price: Optional[Decimal],
        notional: Optional[Decimal],
    ) -> None:
        if self.exchange is None:
            return
        markets = getattr(self.exchange, 'markets', None)
        if not markets or pair not in markets:
            return
        info = markets[pair]
        limits = info.get('limits', {}) or {}
        amt_limits = limits.get('amount', {}) or {}
        cost_limits = limits.get('cost', {}) or {}

        min_amount = amt_limits.get('min')
        if min_amount is not None and amount < Decimal(str(min_amount)):
            raise ValueError(f"{pair}: size {amount} below min amount {min_amount}")

        if notional is not None:
            min_cost = cost_limits.get('min')
            if min_cost is not None and notional < Decimal(str(min_cost)):
                raise ValueError(f"{pair}: notional {notional} below min cost {min_cost}")
