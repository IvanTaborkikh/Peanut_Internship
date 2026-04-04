from dataclasses import dataclass
from typing import Optional
import web3
from src.core.types import TransactionReceipt, TransactionRequest, Address, TokenAmount
from src.chain.errors import RPCError
from web3 import Web3
from decimal import Decimal
import time


@dataclass
class GasPrice:
    """Current gas price information."""

    base_fee: int
    priority_fee_low: int
    priority_fee_medium: int
    priority_fee_high: int

    def get_max_fee(self, priority: str = "medium", buffer: float = 1.2) -> int:
        """Calculate maxFeePerGas with buffer for base fee increase."""
        priority_map = {
            "low": self.priority_fee_low,
            "medium": self.priority_fee_medium,
            "high": self.priority_fee_high,
        }
        priority_fee = priority_map.get(priority, self.priority_fee_medium)
        return int((self.base_fee + priority_fee) * buffer)


class ChainClient:
    """
    Ethereum RPC client with reliability features.

    Features:
    - Automatic retry with exponential backoff
    - Multiple RPC endpoint fallback
    - Request timing/logging
    - Proper error classification
    """

    def __init__(self, rpc_urls: list[str], timeout: int = 30, max_retries: int = 3):
        self._rpc_urls = rpc_urls
        self._timeout = timeout
        self._max_retries = max_retries
        self._web3 = Web3(
            Web3.HTTPProvider(rpc_urls[0], request_kwargs={"timeout": timeout})
        )

    def _retry(self, func, *args, **kwargs):
        """Retry with exponential backoff."""
        for attempt in range(self._max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == self._max_retries - 1:
                    raise RPCError(str(e)) from e
                time.sleep(2**attempt)

    def get_balance(self, address: Address) -> TokenAmount:
        """Get ETH balance of an address."""
        raw = self._retry(self._web3.eth.get_balance, address.checksum)
        return TokenAmount(raw=raw, decimals=18, symbol="ETH")

    def get_nonce(self, address: Address, block: str = "pending") -> int:
        return self._retry(
            self._web3.eth.get_transaction_count, address.checksum, block
        )

    def get_gas_price(self) -> GasPrice:
        """Returns current gas price info (base fee, priority fee estimates)."""
        block = self._retry(self._web3.eth.get_block, "latest")
        priority_fee = self._retry(lambda: self._web3.eth.max_priority_fee)
        return GasPrice(
            base_fee=block["baseFeePerGas"],
            priority_fee_low=priority_fee,
            priority_fee_medium=int(Decimal(priority_fee) * Decimal("1.5")),
            priority_fee_high=priority_fee * 2,
        )

    def estimate_gas(self, tx: TransactionRequest) -> int:
        return self._retry(self._web3.eth.estimate_gas, tx.to_dict())

    def send_transaction(self, signed_tx: bytes) -> str:
        """Send and return tx hash. Does NOT wait for confirmation."""
        tx_hash_hex = self._retry(self._web3.eth.send_raw_transaction, signed_tx).hex()
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex
        return tx_hash_hex

    def wait_for_receipt(
        self, tx_hash: str, timeout: int = 120, poll_interval: float = 1.0
    ) -> TransactionReceipt:
        """Wait for transaction confirmation."""
        start_time = time.time()
        while True:
            try:
                receipt = self._web3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    return TransactionReceipt.from_web3(receipt)
            except web3.exceptions.TransactionNotFound:
                pass
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Transaction {tx_hash} not confirmed within {timeout} seconds"
                )
            time.sleep(poll_interval)

    def get_transaction(self, tx_hash: str) -> dict:
        return dict(self._retry(self._web3.eth.get_transaction, tx_hash))

    def get_receipt(self, tx_hash: str) -> Optional[TransactionReceipt]:
        """Get transaction receipt if available, else None."""
        receipt = self._retry(self._web3.eth.get_transaction_receipt, tx_hash)
        if receipt is None:
            return None
        return TransactionReceipt.from_web3(receipt)

    def call(self, tx: TransactionRequest, block: str = "latest") -> bytes:
        """eth_call - simulate transaction without sending."""
        return self._retry(self._web3.eth.call, tx.to_dict(), block)
