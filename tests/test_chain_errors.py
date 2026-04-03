from src.chain.errors import (
    ChainError,
    RPCError,
    TransactionFailed,
    InsufficientFunds,
    NonceTooLow,
    ReplacementUnderpriced,
)
from src.core import TransactionReceipt


def test_rpc_error_has_code():
    err = RPCError("Something failed", code=32000)
    assert err.code == 32000
    assert "Something failed" in str(err)


def test_rpc_error_without_code():
    err = RPCError("No code error")
    assert err.code is None


def test_rpc_error_is_chain_error():
    assert isinstance(RPCError("err"), ChainError)


def test_transaction_failed_stores_hash_and_receipt():
    receipt = TransactionReceipt(
        tx_hash="0x123",
        block_number=1,
        status=False,
        gas_used=21000,
        effective_gas_price=1000,
        logs=[],
    )
    err = TransactionFailed("0x123", receipt)
    assert err.tx_hash == "0x123"
    assert err.receipt == receipt
    assert "0x123" in str(err)


def test_transaction_failed_is_chain_error():
    receipt = TransactionReceipt(
        tx_hash="0x123",
        block_number=1,
        status=False,
        gas_used=21000,
        effective_gas_price=1000,
        logs=[],
    )
    assert isinstance(TransactionFailed("0x123", receipt), ChainError)


def test_insufficient_funds_is_chain_error():
    assert isinstance(InsufficientFunds("Not enough ETH"), ChainError)


def test_nonce_too_low_is_chain_error():
    assert isinstance(NonceTooLow("Nonce too low"), ChainError)


def test_replacement_underpriced_is_chain_error():
    assert isinstance(ReplacementUnderpriced("Too low"), ChainError)


def test_all_errors_catchable_as_chain_error():
    receipt = TransactionReceipt(
        tx_hash="0x123",
        block_number=1,
        status=False,
        gas_used=21000,
        effective_gas_price=1000,
        logs=[],
    )
    errors = [
        RPCError("rpc"),
        TransactionFailed("0x123", receipt),
        InsufficientFunds("funds"),
        NonceTooLow("nonce"),
        ReplacementUnderpriced("price"),
    ]
    for err in errors:
        assert isinstance(err, ChainError)
