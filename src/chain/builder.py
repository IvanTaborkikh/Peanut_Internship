from eth_account.datastructures import SignedTransaction

from src.chain.client import ChainClient
from src.chain.errors import InsufficientFunds, TransactionFailed
from src.core.types import Address, TokenAmount, TransactionRequest, TransactionReceipt
from src.core.wallet import WalletManager


class TransactionBuilder:
    """
    Fluent builder for transactions.

    Usage:
        tx = (TransactionBuilder(client, wallet)
            .to(recipient)
            .value(TokenAmount.from_human("0.1", 18))
            .data(calldata)
            .with_gas_estimate()
            .with_gas_price("high")
            .build())
    """

    def __init__(self, client: ChainClient, wallet: WalletManager):
        self._client = client
        self._wallet = wallet
        self._to: Address | None = None
        self._value: TokenAmount = TokenAmount(raw=0, decimals=18, symbol="ETH")
        self._data: bytes = b""
        self._nonce: int | None = None
        self._gas_limit: int | None = None
        self._max_fee_per_gas: int | None = None
        self._max_priority_fee: int | None = None
        self._chain_id: int = 1

    def to(self, address: Address) -> "TransactionBuilder":
        self._to = address
        return self

    def value(self, amount: TokenAmount) -> "TransactionBuilder":
        self._value = amount
        return self

    def data(self, calldata: bytes) -> "TransactionBuilder":
        self._data = calldata
        return self

    def nonce(self, nonce: int) -> "TransactionBuilder":
        """Explicit nonce (for replacement or batch)."""
        self._nonce = nonce
        return self

    def chain_id(self, chain_id: int) -> "TransactionBuilder":
        """Set chain ID explicitly."""
        self._chain_id = chain_id
        return self

    def gas_limit(self, limit: int) -> "TransactionBuilder":
        self._gas_limit = limit
        return self

    def with_gas_estimate(self, buffer: float = 1.2) -> "TransactionBuilder":
        """Estimate gas and set limit with buffer."""
        tx = self._build_partial()
        estimated = self._client.estimate_gas(tx)
        self._gas_limit = int(estimated * buffer)
        return self

    def with_gas_price(self, priority: str = "medium") -> "TransactionBuilder":
        """Set gas price based on current network conditions."""
        valid = {"low", "medium", "high"}
        if priority not in valid:
            raise ValueError(f"Invalid priority '{priority}'. Must be one of: {valid}")
        gas_price = self._client.get_gas_price()
        self._max_fee_per_gas = gas_price.get_max_fee(priority)
        self._max_priority_fee = getattr(gas_price, f"priority_fee_{priority}")
        return self

    def build(self) -> TransactionRequest:
        """Validate and return transaction request."""
        if self._to is None:
            raise ValueError("Transaction must have a 'to' address.")

        # Fetch nonce as late as possible to minimize race condition window
        if self._nonce is None:
            self._nonce = self._client.get_nonce(Address(self._wallet.address))

        if self._max_fee_per_gas is None or self._max_priority_fee is None:
            gas_price = self._client.get_gas_price()
            self._max_fee_per_gas = gas_price.get_max_fee(priority="medium")
            self._max_priority_fee = gas_price.priority_fee_medium

        if self._gas_limit is None:
            partial = self._build_partial()
            self._gas_limit = self._client.estimate_gas(partial)

        # Check balance covers value + gas cost
        wallet_address = Address(self._wallet.address)
        balance = self._client.get_balance(wallet_address)
        gas_cost = self._gas_limit * self._max_fee_per_gas
        total_required = self._value.raw + gas_cost
        if balance.raw < total_required:
            raise InsufficientFunds(
                f"Balance {balance.raw} wei < required {total_required} wei "
                f"(value={self._value.raw}, gas={gas_cost})"
            )

        return TransactionRequest(
            to=self._to,
            value=self._value,
            data=self._data,
            nonce=self._nonce,
            gas_limit=self._gas_limit,
            max_fee_per_gas=self._max_fee_per_gas,
            max_priority_fee=self._max_priority_fee,
            chain_id=self._chain_id,
        )

    def build_and_sign(self) -> SignedTransaction:
        """Build, sign, and return ready-to-send transaction."""
        tx = self.build()
        return self._wallet.sign_transaction(tx.to_dict())

    def send(self) -> str:
        """Build, sign, send, return tx hash."""
        signed = self.build_and_sign()
        return self._client.send_transaction(signed.rawTransaction)

    def send_and_wait(self, timeout: int = 120) -> TransactionReceipt:
        """Build, sign, send, wait for confirmation. Raises TransactionFailed if reverted."""
        tx_hash = self.send()
        receipt = self._client.wait_for_receipt(tx_hash, timeout=timeout)
        if not receipt.status:
            raise TransactionFailed(tx_hash, receipt)
        return receipt

    def _build_partial(self) -> TransactionRequest:
        """Build partial tx for gas estimation (no nonce/gas required)."""
        if self._to is None:
            raise ValueError("Transaction must have a 'to' address.")
        return TransactionRequest(
            to=self._to,
            value=self._value,
            data=self._data,
            chain_id=self._chain_id,
        )
