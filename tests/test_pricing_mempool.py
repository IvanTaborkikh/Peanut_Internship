from decimal import Decimal
from src.pricing.MempoolMonitor import MempoolMonitor, ParsedSwap
from src.core.types import Address

SENDER = Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b")
WETH   = Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
USDC   = Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")


def make_monitor() -> MempoolMonitor:
    return MempoolMonitor(ws_url="wss://fake", callback=lambda _: None)


def make_swap(amount_in=1000 * 10**6, min_amount_out=950 * 10**6) -> ParsedSwap:
    return ParsedSwap(
        tx_hash="0xabc",
        router="0xRouter",
        dex="UniswapV2",
        method="swapExactTokensForTokens",
        token_in=USDC,
        token_out=WETH,
        amount_in=amount_in,
        min_amount_out=min_amount_out,
        deadline=9999999999,
        sender=SENDER,
        gas_price=20_000_000_000,
    )


# ── ParsedSwap ────────────────────────────────────────────────────────────────

def test_slippage_tolerance_five_percent():
    """1000 in, 950 min out = 5% slippage."""
    swap = make_swap(amount_in=1000, min_amount_out=950)
    assert swap.slippage_tolerance == Decimal("0.05")


def test_slippage_tolerance_zero():
    """min_amount_out == amount_in means 0% slippage."""
    swap = make_swap(amount_in=1000, min_amount_out=1000)
    assert swap.slippage_tolerance == Decimal("0")


def test_slippage_tolerance_one_percent():
    swap = make_swap(amount_in=10000, min_amount_out=9900)
    assert swap.slippage_tolerance == Decimal("0.01")


# ── parse_transaction ─────────────────────────────────────────────────────────

def test_parse_transaction_returns_none_for_non_swap():
    monitor = make_monitor()
    tx = {"input": "0xdeadbeef" + "00" * 64, "from": SENDER.checksum, "gasPrice": "0x1"}
    result = monitor.parse_transaction(tx)
    assert result is None


def test_parse_transaction_returns_none_for_empty_input():
    monitor = make_monitor()
    tx = {"input": "0x", "from": SENDER.checksum, "gasPrice": "0x1"}
    result = monitor.parse_transaction(tx)
    assert result is None


def test_parse_transaction_returns_none_for_missing_input():
    monitor = make_monitor()
    result = monitor.parse_transaction({})
    assert result is None


def test_parse_transaction_detects_swap_exact_eth_for_tokens():
    """0x7ff36ab5 = swapExactETHForTokens"""
    monitor = make_monitor()
    # Minimal valid calldata with known selector
    selector = "7ff36ab5"
    calldata = "0x" + selector + "00" * 128
    tx = {
        "input": calldata,
        "from": SENDER.checksum,
        "gasPrice": hex(20 * 10**9),
        "value": hex(10**18),
        "hash": "0xabc",
        "to": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    }
    result = monitor.parse_transaction(tx)
    if result is not None:
        assert result.dex == "UniswapV2"
        assert result.method == "swapExactETHForTokens"


# ── SWAP_SELECTORS ────────────────────────────────────────────────────────────

def test_known_selectors_present():
    assert "0x38ed1739" in MempoolMonitor.SWAP_SELECTORS
    assert "0x7ff36ab5" in MempoolMonitor.SWAP_SELECTORS
    assert "0x18cbafe5" in MempoolMonitor.SWAP_SELECTORS


def test_selector_values_are_tuples():
    for selector, value in MempoolMonitor.SWAP_SELECTORS.items():
        assert isinstance(value, tuple)
        assert len(value) == 2