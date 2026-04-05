import os
from eth_account.datastructures import SignedTransaction, SignedMessage
from eth_account import Account
from eth_account.messages import encode_defunct


class WalletManager:
    """
    Manages wallet operations: key loading, signing, verification.

    Keys can be loaded from:
    - Environment variable
    - Encrypted keyfile (optional stretch goal)

    CRITICAL: Private key must never appear in logs, errors, or string representations.
    """

    def __init__(self, private_key: str):
        """Initialize with a private key string."""
        try:
            self._account = Account.from_key(private_key)
        except Exception:
            raise ValueError("Invalid private key") from None

    @classmethod
    def from_env(cls, env_var: str = "PRIVATE_KEY") -> "WalletManager":
        """Load private key from environment variable."""
        private_key = os.getenv(env_var)
        if not private_key:
            raise ValueError(f"Environment variable '{env_var}' is not set.")
        if not private_key.startswith("0x"):
            raise ValueError(f"Private key in '{env_var}' must start with '0x'.")
        return cls(private_key)

    @classmethod
    def generate(cls) -> tuple["WalletManager", str]:
        """Generate a new random wallet. Returns (manager, private_key_hex).
        Caller is responsible for securely storing the private key."""
        account = Account.create()
        key_hex = "0x" + account.key.hex()
        print(f"Generated new wallet with address: {account.address}")
        return cls(key_hex), key_hex

    @property
    def address(self) -> str:
        """Returns checksummed address."""
        return self._account.address

    def sign_message(self, message: str) -> SignedMessage:
        """Sign an arbitrary message (with EIP-191 prefix)."""
        if not isinstance(message, str):
            raise TypeError("Message must be a string.")
        if not message:
            raise ValueError("Message cannot be empty.")
        msg = encode_defunct(text=message)
        return self._account.sign_message(msg)

    def sign_typed_data(self, domain: dict, types: dict, value: dict) -> SignedMessage:
        """Sign EIP-712 typed data (used by many DeFi protocols)."""
        if not isinstance(domain, dict):
            raise TypeError("Domain must be a dict.")
        if not isinstance(types, dict):
            raise TypeError("Types must be a dict.")
        if not isinstance(value, dict):
            raise TypeError("Value must be a dict.")
        if not domain:
            raise ValueError("Domain cannot be empty.")
        if not types:
            raise ValueError("Types cannot be empty.")
        if not value:
            raise ValueError("Value cannot be empty.")
        return self._account.sign_typed_data(domain, types, value)

    def sign_transaction(self, tx: dict) -> SignedTransaction:
        """Sign a transaction dict."""
        if not isinstance(tx, dict):
            raise TypeError("Transaction must be a dict.")
        if not tx:
            raise ValueError("Transaction cannot be empty.")
        if "to" not in tx:
            raise ValueError("Transaction must have 'to' field.")
        if "value" not in tx:
            raise ValueError("Transaction must have 'value' field.")

        return self._account.sign_transaction(tx)

    def __repr__(self) -> str:
        """MUST NOT expose private key."""
        return f"WalletManager(address={self.address})"
