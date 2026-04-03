import pytest
from decimal import Decimal
from src.core import Address, TokenAmount, Token


def test_address_invalid():
    with pytest.raises(ValueError):
        Address("hello")


def test_address_case_insensitive():
    assert Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b") == Address(
        "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    )


def test_address_checksum():
    addr = Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b")
    assert addr.checksum == "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"


def test_address_lower():
    addr = Address("0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B")
    assert addr.lower == "0xab5801a7d398351b8be11c439e05c5b3259aec9b"


def test_address_from_string():
    addr = Address.from_string("0xab5801a7d398351b8be11c439e05c5b3259aec9b")
    assert isinstance(addr, Address)


def test_token_amount_from_human():
    amount = TokenAmount.from_human("1.5", 18, "ETH")
    assert amount.raw == 1500000000000000000


def test_token_amount_from_human_usdc():
    amount = TokenAmount.from_human("1.5", 6, "USDC")
    assert amount.raw == 1500000


def test_token_amount_human():
    amount = TokenAmount(raw=1500000000000000000, decimals=18, symbol="ETH")
    assert amount.human == Decimal("1.5")


def test_token_amount_add():
    a = TokenAmount(raw=1000, decimals=18, symbol="ETH")
    b = TokenAmount(raw=500, decimals=18, symbol="ETH")
    assert (a + b).raw == 1500


def test_token_amount_add_different_decimals():
    a = TokenAmount(raw=1000, decimals=18)
    b = TokenAmount(raw=1000, decimals=6)
    with pytest.raises(ValueError):
        a + b


def test_token_amount_mul_int():
    a = TokenAmount(raw=1000, decimals=18)
    assert (a * 3).raw == 3000


def test_token_amount_mul_decimal():
    a = TokenAmount(raw=1000, decimals=18)
    assert (a * Decimal("1.5")).raw == 1500


def test_token_amount_no_float():
    with pytest.raises(Exception):
        TokenAmount.from_human(1.5, 18)


def test_token_equality_by_address():
    addr = Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b")
    t1 = Token(address=addr, symbol="USDC", decimals=6)
    t2 = Token(address=addr, symbol="FAKE", decimals=18)
    assert t1 == t2


def test_token_different_address_not_equal():
    t1 = Token(
        address=Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"),
        symbol="A",
        decimals=18,
    )
    t2 = Token(
        address=Address("0xdac17f958d2ee523a2206206994597c13d831ec7"),
        symbol="A",
        decimals=18,
    )
    assert t1 != t2


def test_token_hash_consistent():
    addr = Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b")
    t1 = Token(address=addr, symbol="USDC", decimals=6)
    t2 = Token(address=addr, symbol="FAKE", decimals=18)
    assert hash(t1) == hash(t2)
