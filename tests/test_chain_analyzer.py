# tests/test_chain_analyzer.py
from decimal import Decimal
from unittest.mock import MagicMock, patch
from src.chain import (
    _decode_selector,
    _decode_arguments,
    _normalize_calldata,
    _wei_to_gwei,
    _wei_to_eth,
    _extract_revert_reason,
    get_eth_price_usd,
)
from web3 import Web3


# ── Helper function tests ─────────────────────────────────────────────────────
def test_wei_to_gwei_one_gwei():
    assert _wei_to_gwei(1_000_000_000) == Decimal("1")


def test_wei_to_gwei_zero():
    assert _wei_to_gwei(0) == Decimal("0")


def test_wei_to_gwei_fraction():
    assert _wei_to_gwei(500_000_000) == Decimal("0.5")


def test_wei_to_eth_one_eth():
    assert _wei_to_eth(1_000_000_000_000_000_000) == Decimal("1")


def test_wei_to_eth_zero():
    assert _wei_to_eth(0) == Decimal("0")


def test_wei_to_eth_small_amount():
    result = _wei_to_eth(1_000_000_000_000_000)
    assert result == Decimal("0.001")


# ── Normalize calldata tests ──────────────────────────────────────────────────
def test_normalize_calldata_none():
    assert _normalize_calldata(None) == "0x"


def test_normalize_calldata_empty_string():
    assert _normalize_calldata("") == "0x"


def test_normalize_calldata_bytes():
    result = _normalize_calldata(b"\x38\xed\x17\x39")
    assert result == "0x38ed1739"


def test_normalize_calldata_already_0x():
    assert _normalize_calldata("0xabcd") == "0xabcd"


def test_normalize_calldata_without_0x():
    assert _normalize_calldata("abcd") == "0xabcd"


# ── Decode selector tests ─────────────────────────────────────────────────────
def test_decode_selector_empty():
    selector, name = _decode_selector("0x")
    assert selector == "0x"
    assert name == "ETH Transfer (no calldata)"


def test_decode_selector_none_data():
    selector, name = _decode_selector("")
    assert name == "ETH Transfer (no calldata)"


def test_decode_selector_short_data():
    selector, name = _decode_selector("0x1234")
    assert name == "ETH Transfer (no calldata)"


def test_decode_selector_transfer():
    data = "0xa9059cbb" + "00" * 64
    selector, name = _decode_selector(data)
    assert selector == "0xa9059cbb"
    assert "transfer" in name.lower()


def test_decode_selector_approve():
    data = "0x095ea7b3" + "00" * 64
    selector, name = _decode_selector(data)
    assert selector == "0x095ea7b3"
    assert "approve" in name.lower()


def test_decode_selector_swap_exact_eth():
    data = "0x7ff36ab5" + "00" * 64
    selector, name = _decode_selector(data)
    assert selector == "0x7ff36ab5"
    assert "swapExactETHForTokens" in name


def test_decode_selector_multicall():
    data = "0xac9650d8" + "00" * 64
    selector, name = _decode_selector(data)
    assert selector == "0xac9650d8"
    assert "multicall" in name


def test_decode_selector_unknown():
    data = "0xdeadbeef" + "00" * 64
    selector, name = _decode_selector(data)
    assert selector == "0xdeadbeef"
    assert name == "Unknown function"


# ── Decode arguments tests ────────────────────────────────────────────────────
def test_decode_arguments_unknown_selector():
    w3 = MagicMock()
    result = _decode_arguments("0xdeadbeef", "0xdeadbeef", w3)
    assert result is None


def test_decode_arguments_transfer():
    w3 = Web3()
    recipient = "0xab5801a7d398351b8be11c439e05c5b3259aec9b"
    amount = 1000

    contract = w3.eth.contract(
        abi=[
            {
                "name": "transfer",
                "type": "function",
                "inputs": [
                    {"name": "recipient", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "outputs": [],
            }
        ]
    )
    encoded = contract.encode_abi(
        "transfer", [Web3.to_checksum_address(recipient), amount]
    )

    result = _decode_arguments(encoded, "0xa9059cbb", w3)
    assert result is not None
    assert any("recipient" in name for name, _ in result)
    assert any("amount" in name for name, _ in result)


# ── Revert reason tests ───────────────────────────────────────────────────────
def test_extract_revert_reason_clean():
    e = Exception("execution reverted: Return amount is not enough")
    assert _extract_revert_reason(e) == "Return amount is not enough"


def test_extract_revert_reason_with_hex():
    e = Exception(
        "execution reverted: Return amount is not enough"
        "', '0x08c379a000000000000000000000000000000000000000'"
    )
    result = _extract_revert_reason(e)
    assert "Return amount is not enough" in result
    assert "0x08c379" not in result


def test_extract_revert_reason_no_keyword():
    e = Exception("Some other error")
    assert _extract_revert_reason(e) == "Some other error"


def test_extract_revert_reason_empty():
    e = Exception("")
    assert _extract_revert_reason(e) == ""


# ── get_eth_price_usd tests ───────────────────────────────────────────────────
def test_get_eth_price_usd_returns_float_on_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"ethereum": {"usd": 3500.0}}
    mock_response.raise_for_status.return_value = None

    with patch("requests.get", return_value=mock_response):
        price = get_eth_price_usd()

    assert price == 3500.0
    assert isinstance(price, float)


def test_get_eth_price_usd_returns_none_on_network_error():
    with patch("requests.get", side_effect=Exception("connection refused")):
        price = get_eth_price_usd()

    assert price is None


def test_get_eth_price_usd_returns_none_on_bad_json():
    mock_response = MagicMock()
    mock_response.json.return_value = {"unexpected": "format"}
    mock_response.raise_for_status.return_value = None

    with patch("requests.get", return_value=mock_response):
        price = get_eth_price_usd()

    assert price is None


def test_get_eth_price_usd_returns_none_on_http_error():
    import requests
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("503")

    with patch("requests.get", return_value=mock_response):
        price = get_eth_price_usd()

    assert price is None