import pytest
from unittest.mock import patch
from src.chain import ChainClient, GasPrice
from src.chain.errors import RPCError


# ── GasPrice tests ────────────────────────────────────────────────────────────
def test_gas_price_get_max_fee_medium():
    gp = GasPrice(
        base_fee=10_000_000_000,
        priority_fee_low=1_000_000_000,
        priority_fee_medium=2_000_000_000,
        priority_fee_high=4_000_000_000,
    )
    result = gp.get_max_fee("medium", buffer=1.2)
    expected = int((10_000_000_000 + 2_000_000_000) * 1.2)
    assert result == expected


def test_gas_price_get_max_fee_high():
    gp = GasPrice(
        base_fee=10_000_000_000,
        priority_fee_low=1_000_000_000,
        priority_fee_medium=2_000_000_000,
        priority_fee_high=4_000_000_000,
    )
    result = gp.get_max_fee("high", buffer=1.0)
    assert result == 14_000_000_000


def test_gas_price_get_max_fee_low():
    gp = GasPrice(
        base_fee=10_000_000_000,
        priority_fee_low=1_000_000_000,
        priority_fee_medium=2_000_000_000,
        priority_fee_high=4_000_000_000,
    )
    result = gp.get_max_fee("low", buffer=1.0)
    assert result == 11_000_000_000


def test_gas_price_unknown_priority_falls_back_to_medium():
    gp = GasPrice(
        base_fee=10_000_000_000,
        priority_fee_low=1_000_000_000,
        priority_fee_medium=2_000_000_000,
        priority_fee_high=4_000_000_000,
    )
    result = gp.get_max_fee("unknown", buffer=1.0)
    assert result == 12_000_000_000


# ── Retry logic tests ─────────────────────────────────────────────────────────
def _make_client() -> ChainClient:
    """Create ChainClient without real RPC connection."""
    client = ChainClient.__new__(ChainClient)
    client._max_retries = 3
    return client


def test_retry_success_on_first_attempt():
    client = _make_client()

    def always_works():
        return "ok"

    result = client._retry(always_works)
    assert result == "ok"


def test_retry_success_on_second_attempt():
    client = _make_client()
    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("Temporary error")
        return "ok"

    with patch("time.sleep"):
        result = client._retry(flaky)

    assert result == "ok"
    assert call_count == 2


def test_retry_raises_after_max_retries():
    client = _make_client()

    def always_fails():
        raise Exception("Always fails")

    with patch("time.sleep"):
        with pytest.raises(RPCError):
            client._retry(always_fails)


def test_retry_exponential_backoff():
    client = _make_client()
    sleep_calls = []

    def always_fails():
        raise Exception("fail")

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(RPCError):
            client._retry(always_fails)

    assert sleep_calls == [1, 2]


def test_retry_passes_args_to_func():
    client = _make_client()

    def add(a, b):
        return a + b

    result = client._retry(add, 2, 3)
    assert result == 5


def test_retry_wraps_exception_in_rpc_error():
    client = _make_client()

    def fails_with_message():
        raise ValueError("specific error message")

    with patch("time.sleep"):
        with pytest.raises(RPCError) as exc_info:
            client._retry(fails_with_message)

    assert "specific error message" in str(exc_info.value)
