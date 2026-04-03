from dataclasses import dataclass
from typing import Optional
from decimal import Decimal
from web3 import Web3


@dataclass(frozen=True)
class Address:
    """Ethereum address with validation and checksumming."""

    value: str

    def __post_init__(self):
        # Validate and convert to checksum
        if not Web3.is_address(self.value):
            raise ValueError(f"Invalid Ethereum address: {self.value}")
        # Store the checksum version internally
        object.__setattr__(self, "value", Web3.to_checksum_address(self.value))

    @classmethod
    def from_string(cls, s: str) -> "Address":
        """Factory method to create Address from string."""
        return cls(s)

    @property
    def checksum(self) -> str:
        """Returns the checksummed address string."""
        return self.value

    @property
    def lower(self) -> str:
        """Returns the lowercase address string (for hashing/comparison)."""
        return self.value.lower()

    def __eq__(self, other) -> bool:
        # Case-insensitive comparison
        if isinstance(other, Address):
            return self.lower == other.lower
        return NotImplemented


@dataclass(frozen=True)
class TokenAmount:
    """
    Represents a token amount with proper decimal handling.

    Internally stores raw integer (wei-equivalent).
    Provides human-readable formatting.
    """

    raw: int  # Raw amount (e.g., wei)
    decimals: int  # Token decimals (e.g., 18 for ETH, 6 for USDC)
    symbol: Optional[str] = None

    @classmethod
    def from_human(
        cls, amount: str | Decimal, decimals: int, symbol: str = None
    ) -> "TokenAmount":
        """Create from human-readable amount (e.g., '1.5' ETH)."""
        if isinstance(amount, float):
            raise ValueError("Float is not allowed. Use str or Decimal instead.")
        human_amount = Decimal(amount)
        raw_amount = int(human_amount * (10**decimals))
        return cls(raw=raw_amount, decimals=decimals, symbol=symbol)

    @property
    def human(self) -> Decimal:
        """Returns human-readable decimal."""
        return Decimal(self.raw) / (10**self.decimals)

    def __add__(self, other: "TokenAmount") -> "TokenAmount":
        # Must validate same decimals
        if self.decimals != other.decimals:
            raise ValueError("Cannot add TokenAmounts with different decimals.")
        return TokenAmount(
            raw=self.raw + other.raw, decimals=self.decimals, symbol=self.symbol
        )

    def __mul__(self, factor: int | Decimal) -> "TokenAmount":
        return TokenAmount(
            raw=int(self.raw * factor), decimals=self.decimals, symbol=self.symbol
        )

    def __str__(self) -> str:
        return f"{self.human}{self.symbol or ''}"


@dataclass(frozen=True, eq=False)
class Token:
    """
    Represents an ERC-20 token with its on-chain metadata.

    Identity is by address only — two Token instances at the same address
    are equal regardless of symbol/decimals (those are metadata, not identity).
    We use eq=False to override the dataclass-generated __eq__ and define our own.

    This type will be used extensively from Week 2 onward (AMM math, routing, etc.).
    """

    address: Address
    symbol: str
    decimals: int

    def __eq__(self, other) -> bool:
        if isinstance(other, Token):
            return (
                self.address == other.address
            )
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.address.lower)

    def __repr__(self) -> str:
        return f"Token({self.symbol},{self.address.checksum})"


@dataclass
class TransactionRequest:
    """A transaction ready to be signed."""

    to: Address
    value: TokenAmount
    data: bytes
    nonce: Optional[int] = None
    gas_limit: Optional[int] = None
    max_fee_per_gas: Optional[int] = None
    max_priority_fee: Optional[int] = None
    chain_id: int = 1

    def to_dict(self) -> dict:
        """Convert to web3-compatible dict."""
        result = {
            "to": self.to.checksum,
            "value": self.value.raw,
            "data": self.data,
            "chainId": self.chain_id,
        }
        if self.nonce is not None:
            result["nonce"] = self.nonce
        if self.gas_limit is not None:
            result["gas"] = self.gas_limit
        if self.max_fee_per_gas is not None:
            result["maxFeePerGas"] = self.max_fee_per_gas
        if self.max_priority_fee is not None:
            result["maxPriorityFeePerGas"] = self.max_priority_fee
        return result


@dataclass
class TransactionReceipt:
    """Parsed transaction receipt."""

    tx_hash: str
    block_number: int
    status: bool  # True = success
    gas_used: int
    effective_gas_price: int
    logs: list

    @property
    def tx_fee(self) -> TokenAmount:
        """Returns transaction fee as TokenAmount."""
        return TokenAmount(
            raw=self.gas_used * self.effective_gas_price, decimals=18, symbol="ETH"
        )

    @classmethod
    def from_web3(cls, receipt: dict) -> "TransactionReceipt":
        """Parse from web3 receipt dict."""
        return cls(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            status=receipt["status"] == 1,
            gas_used=receipt["gasUsed"],
            effective_gas_price=receipt.get(
                "effectiveGasPrice", receipt.get("gasPrice", 0)
            ),
            logs=receipt["logs"],
        )
