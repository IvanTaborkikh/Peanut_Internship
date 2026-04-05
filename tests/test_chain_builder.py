import pytest
from unittest.mock import MagicMock
from src.chain.builder import TransactionBuilder
from src.chain import ChainClient, GasPrice
from src.chain.errors import InsufficientFunds, TransactionFailed
from src.core import Address, TokenAmount, TransactionReceipt
from src.core import WalletManager

TEST_ADDRESS = "0xab5801a7d398351b8be11c439e05c5b3259aec9b"
RICH_BALANCE = TokenAmount(raw=10**18, decimals=18, symbol="ETH")  # 1 ETH
BROKE_BALANCE = TokenAmount(raw=0, decimals=18, symbol="ETH")


def _make_builder(balance: TokenAmount = None):
    """Create TransactionBuilder with mocked client and wallet."""
    client = MagicMock(spec=ChainClient)
    client.get_nonce.return_value = 5
    client.estimate_gas.return_value = 21000
    client.get_gas_price.return_value = GasPrice(
        base_fee=10_000_000_000,
        priority_fee_low=1_000_000_000,
        priority_fee_medium=2_000_000_000,
        priority_fee_high=4_000_000_000,
    )
    client.get_balance.return_value = balance if balance is not None else RICH_BALANCE

    wallet = MagicMock(spec=WalletManager)
    wallet.address = TEST_ADDRESS

    return TransactionBuilder(client, wallet), client, wallet


# ── Validation tests ──────────────────────────────────────────────────────────
def test_build_requires_to():
    builder, _, _ = _make_builder()
    with pytest.raises(ValueError, match="'to' address"):
        builder.build()


def test_build_default_value_is_zero():
    """Value is optional — defaults to 0 for contract calls."""
    builder, _, _ = _make_builder()
    tx = builder.to(Address(TEST_ADDRESS)).build()
    assert tx.value.raw == 0


def test_build_success():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.to.lower == Address(TEST_ADDRESS).lower
    assert tx.value.raw == 1000


def test_build_sets_nonce_automatically():
    builder, client, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.nonce == 5
    client.get_nonce.assert_called_once()


def test_explicit_nonce_skips_rpc():
    builder, client, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .nonce(42)
        .build()
    )
    assert tx.nonce == 42
    client.get_nonce.assert_not_called()


# ── Gas estimation tests ──────────────────────────────────────────────────────
def test_with_gas_estimate_sets_limit():
    builder, client, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_estimate(buffer=1.0)
    tx = builder.build()
    assert tx.gas_limit == 21000


def test_with_gas_estimate_applies_buffer():
    builder, client, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_estimate(buffer=1.5)
    tx = builder.build()
    assert tx.gas_limit == int(21000 * 1.5)


def test_with_gas_price_medium():
    builder, _, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_price("medium")
    tx = builder.build()
    assert tx.max_fee_per_gas is not None
    assert tx.max_priority_fee == 2_000_000_000


def test_with_gas_price_high():
    builder, _, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_price("high")
    tx = builder.build()
    assert tx.max_priority_fee == 4_000_000_000


def test_with_gas_price_invalid_priority_raises():
    builder, _, _ = _make_builder()
    with pytest.raises(ValueError, match="Invalid priority"):
        builder.with_gas_price("ultra")


def test_explicit_gas_limit():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .gas_limit(50000)
        .build()
    )
    assert tx.gas_limit == 50000


# ── Balance check tests ───────────────────────────────────────────────────────
def test_build_raises_insufficient_funds_when_balance_zero():
    builder, _, _ = _make_builder(balance=BROKE_BALANCE)
    with pytest.raises(InsufficientFunds):
        builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18)).build()


def test_build_passes_when_balance_sufficient():
    builder, _, _ = _make_builder(balance=RICH_BALANCE)
    tx = builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18)).build()
    assert tx is not None


# ── Chain ID tests ────────────────────────────────────────────────────────────
def test_default_chain_id_is_mainnet():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.chain_id == 1


def test_chain_id_sepolia():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .chain_id(11155111)
        .build()
    )
    assert tx.chain_id == 11155111


# ── Data tests ────────────────────────────────────────────────────────────────
def test_data_default_empty():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.data == b""


def test_data_set():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .data(b"\x12\x34")
        .build()
    )
    assert tx.data == b"\x12\x34"


# ── Fluent interface tests ────────────────────────────────────────────────────
def test_builder_returns_self_for_chaining():
    builder, _, _ = _make_builder()
    result = builder.to(Address(TEST_ADDRESS))
    assert result is builder


def test_build_and_sign_calls_wallet():
    builder, _, wallet = _make_builder()
    signed_mock = MagicMock()
    wallet.sign_transaction.return_value = signed_mock

    (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build_and_sign()
    )

    wallet.sign_transaction.assert_called_once()


# ── send() and send_and_wait() tests ─────────────────────────────────────────
def test_send_calls_client_send_transaction():
    builder, client, wallet = _make_builder()
    signed_mock = MagicMock()
    signed_mock.rawTransaction = b"\x01\x02"
    wallet.sign_transaction.return_value = signed_mock
    client.send_transaction.return_value = "0xabc"

    tx_hash = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .send()
    )

    client.send_transaction.assert_called_once_with(b"\x01\x02")
    assert tx_hash == "0xabc"


def test_send_and_wait_returns_receipt_on_success():
    builder, client, wallet = _make_builder()
    signed_mock = MagicMock()
    signed_mock.rawTransaction = b"\x01\x02"
    wallet.sign_transaction.return_value = signed_mock
    client.send_transaction.return_value = "0xabc"

    receipt = TransactionReceipt(
        tx_hash="0xabc", block_number=1, status=True,
        gas_used=21000, effective_gas_price=1000, logs=[],
    )
    client.wait_for_receipt.return_value = receipt

    result = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .send_and_wait()
    )

    assert result.status is True


def test_send_and_wait_raises_on_revert():
    builder, client, wallet = _make_builder()
    signed_mock = MagicMock()
    signed_mock.rawTransaction = b"\x01\x02"
    wallet.sign_transaction.return_value = signed_mock
    client.send_transaction.return_value = "0xabc"

    reverted_receipt = TransactionReceipt(
        tx_hash="0xabc", block_number=1, status=False,
        gas_used=21000, effective_gas_price=1000, logs=[],
    )
    client.wait_for_receipt.return_value = reverted_receipt

    with pytest.raises(TransactionFailed) as exc_info:
        (
            builder.to(Address(TEST_ADDRESS))
            .value(TokenAmount(raw=1000, decimals=18))
            .send_and_wait()
        )

    assert exc_info.value.tx_hash == "0xabc"