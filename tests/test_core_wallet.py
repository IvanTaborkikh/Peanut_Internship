import pytest
import os
from unittest.mock import patch
from eth_account import Account
from src.core import WalletManager
from dotenv import load_dotenv

load_dotenv()
TEST_PRIVATE_KEY = os.getenv("PRIVATE_KEY")


def test_repr_does_not_expose_private_key():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    assert TEST_PRIVATE_KEY not in repr(wallet)


def test_str_does_not_expose_private_key():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    assert TEST_PRIVATE_KEY not in str(wallet)


def test_repr_shows_address():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    assert wallet.address in repr(wallet)


def test_from_env_missing_variable():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError):
            WalletManager.from_env("MISSING_KEY")


def test_from_env_loads_correctly():
    with patch.dict("os.environ", {"PRIVATE_KEY": TEST_PRIVATE_KEY}):
        wallet = WalletManager.from_env("PRIVATE_KEY")
        assert wallet.address is not None


def test_generate_returns_wallet():
    wallet = WalletManager.generate()
    assert isinstance(wallet, WalletManager)
    assert wallet.address.startswith("0x")


def test_sign_message_empty_raises():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    with pytest.raises(ValueError):
        wallet.sign_message("")


def test_sign_message_returns_signature():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    signed = wallet.sign_message("hello")
    assert signed.signature is not None


def test_sign_message_verifiable():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    signed = wallet.sign_message("hello")
    from eth_account.messages import encode_defunct

    msg = encode_defunct(text="hello")
    recovered = Account.recover_message(msg, signature=signed.signature)
    assert recovered == wallet.address


def test_sign_transaction_empty_raises():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    with pytest.raises(ValueError):
        wallet.sign_transaction({})


def test_sign_typed_data_empty_domain_raises():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    with pytest.raises(ValueError):
        wallet.sign_typed_data({}, {"Test": []}, {"value": 1})


def test_address_is_checksummed():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    assert wallet.address == Account.from_key(TEST_PRIVATE_KEY).address


def test_exception_does_not_expose_private_key():
    try:
        WalletManager("invalid_key")
    except Exception as e:
        assert TEST_PRIVATE_KEY not in str(e)


def test_sign_message_invalid_type_raises():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    with pytest.raises(TypeError):
        wallet.sign_message(123)


def test_sign_transaction_invalid_type_raises():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    with pytest.raises(TypeError):
        wallet.sign_transaction("not a dict")


def test_sign_typed_data_invalid_type_raises():
    wallet = WalletManager(TEST_PRIVATE_KEY)
    with pytest.raises(TypeError):
        wallet.sign_typed_data("not a dict", {}, {})
