"""Unit tests for StablecoinConverter."""
import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.exchange.stablecoin_converter import StablecoinConverter


def _orderbook(bid: float, ask: float):
    return {'bids': [[bid, 100.0]], 'asks': [[ask, 100.0]]}


def test_current_ratio_uses_mid_price():
    cex = MagicMock()
    cex.fetch_order_book.return_value = _orderbook(0.9998, 1.0002)
    sc = StablecoinConverter(cex, ttl_sec=5.0)
    assert sc.current_ratio() == Decimal('1.0')


def test_usdc_to_usdt_applies_ratio():
    cex = MagicMock()
    cex.fetch_order_book.return_value = _orderbook(1.0001, 1.0003)  # mid=1.0002
    sc = StablecoinConverter(cex)
    assert sc.usdc_to_usdt(Decimal('10')) == Decimal('10.0020')


def test_usdt_to_usdc_inverse():
    cex = MagicMock()
    cex.fetch_order_book.return_value = _orderbook(1.0, 1.0)
    sc = StablecoinConverter(cex)
    assert sc.usdt_to_usdc(Decimal('10')) == Decimal('10')


def test_ttl_caching_prevents_extra_fetches():
    cex = MagicMock()
    cex.fetch_order_book.return_value = _orderbook(1.0, 1.0)
    sc = StablecoinConverter(cex, ttl_sec=5.0)
    sc.current_ratio()
    sc.current_ratio()
    sc.current_ratio()
    # All within TTL → only one underlying fetch.
    assert cex.fetch_order_book.call_count == 1


def test_ttl_expiry_triggers_refresh():
    cex = MagicMock()
    cex.fetch_order_book.return_value = _orderbook(1.0, 1.0)
    sc = StablecoinConverter(cex, ttl_sec=0.01)
    sc.current_ratio()
    time.sleep(0.05)
    sc.current_ratio()
    assert cex.fetch_order_book.call_count == 2


def test_usdt_to_usdc_zero_ratio_raises():
    cex = MagicMock()
    cex.fetch_order_book.return_value = _orderbook(0.0, 0.0)
    sc = StablecoinConverter(cex)
    with pytest.raises(ValueError, match="ratio is zero"):
        sc.usdt_to_usdc(Decimal('1'))
