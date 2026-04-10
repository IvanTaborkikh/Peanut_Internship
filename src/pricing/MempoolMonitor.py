from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from src.core.types import Address


@dataclass
class ParsedSwap:
    """Parsed swap transaction from the mempool."""

    tx_hash: str
    router: str
    dex: str
    method: str
    token_in: Optional[Address]
    token_out: Optional[Address]
    amount_in: int
    min_amount_out: int
    deadline: int
    sender: Address
    gas_price: int

    @property
    def slippage_tolerance(self) -> Decimal:
        """
        Calculate implied slippage tolerance from amount_in and min_amount_out.
        slippage = (amount_in - min_amount_out) / amount_in
        """
        if self.amount_in == 0:
            return Decimal(0)
        return (Decimal(self.amount_in) - Decimal(self.min_amount_out)) / Decimal(self.amount_in)


class MempoolMonitor:
    """
    Monitors pending transactions for swap activity via WebSocket.
    """

    SWAP_SELECTORS = {
        "0x38ed1739": ("UniswapV2", "swapExactTokensForTokens"),
        "0x7ff36ab5": ("UniswapV2", "swapExactETHForTokens"),
        "0x18cbafe5": ("UniswapV2", "swapExactTokensForETH"),
        "0x5ae401dc": ("UniswapV3", "multicall"),
    }

    def __init__(self, ws_url: str, callback: Callable[[ParsedSwap], None]):
        """
        ws_url:   WebSocket RPC endpoint (wss://...)
        callback: called with ParsedSwap for each detected swap
        """
        self.ws_url = ws_url
        self.callback = callback

    async def start(self) -> None:
        """
        Start monitoring pending transactions.
        Subscribes to newPendingTransactions and processes each one.
        Uses websockets directly for compatibility across web3.py versions.
        """
        import json
        try:
            import websockets
        except ImportError:
            raise RuntimeError("websockets package required: pip install websockets")

        subscribe_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": ["newPendingTransactions"],
        })

        async with websockets.connect(self.ws_url) as ws:
            await ws.send(subscribe_msg)
            await ws.recv()  # subscription confirmation

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    tx_hash = msg.get("params", {}).get("result")
                    if not tx_hash:
                        continue

                    # Fetch full transaction
                    fetch_msg = json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "eth_getTransactionByHash",
                        "params": [tx_hash],
                    })
                    await ws.send(fetch_msg)
                    tx_raw = await ws.recv()
                    tx_data = json.loads(tx_raw).get("result")
                    if not tx_data:
                        continue

                    swap = self.parse_transaction(tx_data)
                    if swap is not None:
                        self.callback(swap)
                except Exception:
                    continue

    def parse_transaction(self, tx: dict) -> Optional["ParsedSwap"]:
        """
        Parse a raw transaction dict.
        Returns None if not a recognised swap.
        """
        input_data = tx.get("input") or tx.get("data")
        if not input_data or input_data == "0x" or len(input_data) < 10:
            return None

        selector = input_data[:10].lower()
        if selector not in self.SWAP_SELECTORS:
            return None

        dex, method = self.SWAP_SELECTORS[selector]
        calldata = bytes.fromhex(input_data[2:])
        params = self.decode_swap_params(selector, calldata)

        sender_raw = tx.get("from", "0x0000000000000000000000000000000000000000")
        gas_price_raw = tx.get("gasPrice") or tx.get("maxFeePerGas") or "0x0"
        gas_price = int(gas_price_raw, 16) if isinstance(gas_price_raw, str) else int(gas_price_raw)

        # For ETH-in swaps, read value from tx.value
        if params.get("amount_in", 0) == 0 and selector == "0x7ff36ab5":
            value_raw = tx.get("value", "0x0")
            params["amount_in"] = int(value_raw, 16) if isinstance(value_raw, str) else int(value_raw)

        token_in = Address(params["token_in"]) if params.get("token_in") else None
        token_out = Address(params["token_out"]) if params.get("token_out") else None

        return ParsedSwap(
            tx_hash=tx.get("hash", ""),
            router=tx.get("to", ""),
            dex=dex,
            method=method,
            token_in=token_in,
            token_out=token_out,
            amount_in=params.get("amount_in", 0),
            min_amount_out=params.get("min_amount_out", 0),
            deadline=params.get("deadline", 0),
            sender=Address(sender_raw),
            gas_price=gas_price,
        )

    def decode_swap_params(self, selector: str, data: bytes) -> dict:
        """
        Decode swap parameters from calldata for a given selector.
        Best-effort ABI decoding without external dependencies.
        """
        # Skip the 4-byte selector
        payload = data[4:]

        def decode_uint256(offset: int) -> int:
            return int.from_bytes(payload[offset: offset + 32], "big")

        def decode_address(offset: int) -> str:
            return "0x" + payload[offset + 12: offset + 32].hex()

        result: dict = {}

        try:
            if selector in ("0x38ed1739", "0x18cbafe5"):
                # swapExactTokensForTokens / swapExactTokensForETH
                # (uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
                result["amount_in"] = decode_uint256(0)
                result["min_amount_out"] = decode_uint256(32)
                path_offset = decode_uint256(64)
                path_len = decode_uint256(path_offset)
                if path_len >= 2:
                    result["token_in"] = decode_address(path_offset + 32)
                    result["token_out"] = decode_address(path_offset + 32 + (path_len - 1) * 32)
                result["deadline"] = decode_uint256(128)

            elif selector == "0x7ff36ab5":
                # swapExactETHForTokens
                # (uint amountOutMin, address[] path, address to, uint deadline)
                result["amount_in"] = 0  # ETH value from tx.value
                result["min_amount_out"] = decode_uint256(0)
                path_offset = decode_uint256(32)
                path_len = decode_uint256(path_offset)
                if path_len >= 2:
                    result["token_in"] = decode_address(path_offset + 32)
                    result["token_out"] = decode_address(path_offset + 32 + (path_len - 1) * 32)
                result["deadline"] = decode_uint256(96)

        except Exception:
            pass

        return result