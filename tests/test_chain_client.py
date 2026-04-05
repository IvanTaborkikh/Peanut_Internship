import pytest
import time
from unittest.mock import patch, MagicMock
from src.chain import ChainClient, GasPrice
from src.chain.errors import RPCError, InsufficientFunds, NonceTooLow, ReplacementUnderpriced


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
def _make_client(rpc_urls=None) -> ChainClient:
    """Create ChainClient without real RPC connection."""
    client = ChainClient.__new__(ChainClient)
    client._max_retries = 3
    client._rpc_urls = rpc_urls or ["http://fake-rpc"]
    client._current_rpc_index = 0
    client._timeout = 30
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


# ── Fatal error tests ─────────────────────────────────────────────────────────
def test_fatal_error_not_retried():
    client = _make_client()
    call_count = 0

    def fails_with_fatal():
        nonlocal call_count
        call_count += 1
        raise Exception("insufficient funds for transfer")

    with pytest.raises(InsufficientFunds):
        client._retry(fails_with_fatal)

    assert call_count == 1


def test_fatal_nonce_too_low_not_retried():
    client = _make_client()
    call_count = 0

    def fails():
        nonlocal call_count
        call_count += 1
        raise Exception("nonce too low")

    with pytest.raises(NonceTooLow):
        client._retry(fails)

    assert call_count == 1


def test_fatal_replacement_underpriced_not_retried():
    client = _make_client()

    def fails():
        raise Exception("replacement transaction underpriced")

    with pytest.raises(ReplacementUnderpriced):
        client._retry(fails)


def test_non_fatal_error_is_retried():
    client = _make_client()
    call_count = 0

    def fails():
        nonlocal call_count
        call_count += 1
        raise Exception("connection timeout")

    with patch("time.sleep"):
        with pytest.raises(RPCError):
            client._retry(fails)

    assert call_count == 3  # retried max_retries times


# ── _classify_error tests ─────────────────────────────────────────────────────
def test_classify_insufficient_funds():
    client = _make_client()
    e = Exception("insufficient funds for gas")
    assert isinstance(client._classify_error(e), InsufficientFunds)


def test_classify_nonce_too_low():
    client = _make_client()
    e = Exception("nonce too low")
    assert isinstance(client._classify_error(e), NonceTooLow)


def test_classify_replacement_underpriced():
    client = _make_client()
    e = Exception("replacement transaction underpriced")
    assert isinstance(client._classify_error(e), ReplacementUnderpriced)


def test_classify_generic_returns_rpc_error():
    client = _make_client()
    e = Exception("something unexpected")
    assert isinstance(client._classify_error(e), RPCError)


# ── _switch_rpc tests ─────────────────────────────────────────────────────────
def test_switch_rpc_single_url_returns_false():
    client = _make_client(rpc_urls=["http://only-one"])
    assert client._switch_rpc() is False


def test_switch_rpc_multiple_urls_switches():
    client = _make_client(rpc_urls=["http://rpc-1", "http://rpc-2"])
    switched = client._switch_rpc()
    assert switched is True
    assert client._current_rpc_index == 1


def test_switch_rpc_wraps_around():
    client = _make_client(rpc_urls=["http://rpc-1", "http://rpc-2"])
    client._switch_rpc()  # → index 1
    client._switch_rpc()  # → index 0 (wrap)
    assert client._current_rpc_index == 0


# ── wait_for_receipt tests ────────────────────────────────────────────────────
def test_wait_for_receipt_timeout():
    client = _make_client()
    client._web3 = MagicMock()
    client._web3.eth.get_transaction_receipt.return_value = None

    with pytest.raises(TimeoutError):
        client.wait_for_receipt("0xabc", timeout=0, poll_interval=0)


def test_wait_for_receipt_switches_rpc_on_error():
    """On transient RPC error, _switch_rpc() is called and polling continues."""
    client = _make_client(rpc_urls=["http://rpc-1", "http://rpc-2"])
    client._web3 = MagicMock()

    switch_calls = []
    original_switch = client._switch_rpc

    def track_switch():
        switch_calls.append(1)
        return original_switch()

    client._switch_rpc = track_switch

    # First call raises, second returns None (not confirmed yet) → timeout
    client._web3.eth.get_transaction_receipt.side_effect = [
        Exception("connection error"),
        None,
    ]

    with pytest.raises(TimeoutError):
        client.wait_for_receipt("0xabc", timeout=0, poll_interval=0)

    assert len(switch_calls) >= 1

# ── __init__ tests ────────────────────────────────────────────────────────────
def test_init_stores_rpc_urls():
    client = ChainClient(rpc_urls=["http://fake-rpc-1", "http://fake-rpc-2"], timeout=10, max_retries=5)
    assert client._rpc_urls == ["http://fake-rpc-1", "http://fake-rpc-2"]
    assert client._timeout == 10
    assert client._max_retries == 5
    assert client._current_rpc_index == 0


def test_init_default_params():
    client = ChainClient(rpc_urls=["http://fake-rpc"])
    assert client._timeout == 30
    assert client._max_retries == 3


# ── Public method tests (with mocked _web3) ───────────────────────────────────
def _make_client_with_mock_web3(rpc_urls=None):
    client = _make_client(rpc_urls)
    client._web3 = MagicMock()
    return client


def test_get_balance_returns_token_amount():
    client = _make_client_with_mock_web3()
    client._web3.eth.get_balance.return_value = 10 ** 18

    from src.core import Address
    balance = client.get_balance(Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"))

    assert balance.raw == 10 ** 18
    assert balance.symbol == "ETH"
    assert balance.decimals == 18


def test_get_nonce_returns_int():
    client = _make_client_with_mock_web3()
    client._web3.eth.get_transaction_count.return_value = 7

    from src.core import Address
    nonce = client.get_nonce(Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"))

    assert nonce == 7


def test_get_gas_price_returns_gas_price():
    client = _make_client_with_mock_web3()
    client._web3.eth.get_block.return_value = {"baseFeePerGas": 10_000_000_000}
    client._web3.eth.max_priority_fee = 1_000_000_000

    gas_price = client.get_gas_price()

    assert gas_price.base_fee == 10_000_000_000
    assert gas_price.priority_fee_low == 1_000_000_000
    assert gas_price.priority_fee_high == 2_000_000_000


def test_estimate_gas_returns_int():
    client = _make_client_with_mock_web3()
    client._web3.eth.estimate_gas.return_value = 21000

    from src.core import Address, TokenAmount, TransactionRequest
    tx = TransactionRequest(
        to=Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"),
        value=TokenAmount(raw=1000, decimals=18),
        data=b"",
    )
    result = client.estimate_gas(tx)
    assert result == 21000


def test_send_transaction_returns_hash_with_0x():
    client = _make_client_with_mock_web3()
    mock_hash = MagicMock()
    mock_hash.hex.return_value = "0xdeadbeef"
    client._web3.eth.send_raw_transaction.return_value = mock_hash

    tx_hash = client.send_transaction(b"\x01\x02\x03")
    assert tx_hash == "0xdeadbeef"


def test_send_transaction_adds_0x_prefix_if_missing():
    client = _make_client_with_mock_web3()
    mock_hash = MagicMock()
    mock_hash.hex.return_value = "deadbeef"  # без 0x
    client._web3.eth.send_raw_transaction.return_value = mock_hash

    tx_hash = client.send_transaction(b"\x01\x02\x03")
    assert tx_hash.startswith("0x")


def test_wait_for_receipt_returns_on_success():
    client = _make_client_with_mock_web3()
    raw_receipt = {
        "transactionHash": bytes.fromhex("ab" * 32),
        "blockNumber": 5,
        "status": 1,
        "gasUsed": 21000,
        "effectiveGasPrice": 1000,
        "logs": [],
    }
    client._web3.eth.get_transaction_receipt.return_value = raw_receipt

    receipt = client.wait_for_receipt("0x" + "ab" * 32, timeout=10)

    assert receipt.status is True
    assert receipt.block_number == 5
    assert receipt.gas_used == 21000


def test_wait_for_receipt_retries_on_transaction_not_found():
    from web3.exceptions import TransactionNotFound
    client = _make_client_with_mock_web3()

    raw_receipt = {
        "transactionHash": bytes.fromhex("ab" * 32),
        "blockNumber": 1,
        "status": 1,
        "gasUsed": 21000,
        "effectiveGasPrice": 1000,
        "logs": [],
    }
    client._web3.eth.get_transaction_receipt.side_effect = [
        TransactionNotFound("tx not found"),
        raw_receipt,
    ]

    with patch("time.sleep"):
        receipt = client.wait_for_receipt("0x" + "ab" * 32, timeout=10)

    assert receipt.status is True


def test_get_transaction_returns_dict():
    client = _make_client_with_mock_web3()
    client._web3.eth.get_transaction.return_value = {"hash": "0xabc", "nonce": 3}

    result = client.get_transaction("0xabc")
    assert isinstance(result, dict)
    assert result["nonce"] == 3


def test_get_receipt_returns_none_when_not_found():
    client = _make_client_with_mock_web3()
    client._web3.eth.get_transaction_receipt.return_value = None

    result = client.get_receipt("0xabc")
    assert result is None


def test_get_receipt_returns_receipt_when_found():
    client = _make_client_with_mock_web3()
    raw_receipt = {
        "transactionHash": bytes.fromhex("ab" * 32),
        "blockNumber": 10,
        "status": 1,
        "gasUsed": 21000,
        "effectiveGasPrice": 1000,
        "logs": [],
    }
    client._web3.eth.get_transaction_receipt.return_value = raw_receipt

    result = client.get_receipt("0x" + "ab" * 32)
    assert result is not None
    assert result.block_number == 10


def test_call_returns_bytes():
    client = _make_client_with_mock_web3()
    client._web3.eth.call.return_value = b"\x00\x01"

    from src.core import Address, TokenAmount, TransactionRequest
    tx = TransactionRequest(
        to=Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"),
        value=TokenAmount(raw=0, decimals=18),
        data=b"",
    )
    result = client.call(tx)
    assert result == b"\x00\x01"
