import pytest
import time
from collections import deque
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.exchange.client import ExchangeClient, _DEFAULT_WEIGHT_LIMIT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _exchange_info(weight_limit=1200, interval='MINUTE', interval_num=1):
    """Minimal exchangeInfo response with REQUEST_WEIGHT rate limit."""
    return {
        'rateLimits': [
            {
                'rateLimitType': 'REQUEST_WEIGHT',
                'interval':      interval,
                'intervalNum':   interval_num,
                'limit':         weight_limit,
            },
            {
                'rateLimitType': 'ORDERS',   # should be ignored
                'interval':      'SECOND',
                'intervalNum':   10,
                'limit':         50,
            },
        ]
    }


@pytest.fixture
def mock_exchange():
    ex = MagicMock()
    ex.fetch_time.return_value = 1706000000000
    ex.publicGetExchangeinfo.return_value = _exchange_info()
    return ex


@pytest.fixture
def client(mock_exchange):
    """ExchangeClient with ccxt I/O fully mocked. No network required."""
    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ccxt.binance.return_value = mock_exchange
        c = ExchangeClient({'apiKey': 'test_key', 'secret': 'test_secret', 'sandbox': True})
    return c


def _raw_orderbook():
    return {
        'bids': [[2001.0, 1.5], [2000.0, 2.0], [1999.0, 0.5]],
        'asks': [[2002.0, 1.0], [2003.0, 3.0], [2004.0, 0.8]],
        'timestamp': 1706000000000,
    }


def _raw_balance():
    return {
        'ETH':  {'free': 10.5,     'used': 0.0,   'total': 10.5},
        'USDT': {'free': 20000.0,  'used': 500.0,  'total': 20500.0},
        'BNB':  {'free': 0.0,      'used': 0.0,   'total': 0.0},    # zero — must be excluded
        'info': {},
        'free': {},
        'used': {},
        'total': {},
    }


def _raw_order(status='closed', filled=0.8, amount=1.0, average=2000.5):
    return {
        'id': '99991',
        'symbol': 'ETH/USDT',
        'side': 'buy',
        'type': 'limit',
        'timeInForce': 'IOC',
        'amount': amount,
        'filled': filled,
        'average': average,
        'fee': {'cost': 1.6, 'currency': 'USDT'},
        'status': status,
        'timestamp': 1706000000000,
    }


# ---------------------------------------------------------------------------
# fetch_order_book
# ---------------------------------------------------------------------------

def test_fetch_order_book_structure(client, mock_exchange):
    """Order book has all required fields with correct types."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')

    assert ob['symbol'] == 'ETH/USDT'
    assert isinstance(ob['timestamp'], int)
    assert isinstance(ob['bids'], list)
    assert isinstance(ob['asks'], list)
    assert isinstance(ob['best_bid'], tuple)
    assert isinstance(ob['best_ask'], tuple)
    assert isinstance(ob['mid_price'], Decimal)
    assert isinstance(ob['spread_bps'], Decimal)


def test_order_book_bids_descending(client, mock_exchange):
    """Bids sorted highest price first (best bid on top)."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')

    prices = [p for p, _ in ob['bids']]
    assert prices == sorted(prices, reverse=True)


def test_order_book_asks_ascending(client, mock_exchange):
    """Asks sorted lowest price first (best ask on top)."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')

    prices = [p for p, _ in ob['asks']]
    assert prices == sorted(prices)


def test_spread_calculation(client, mock_exchange):
    """spread_bps == (best_ask - best_bid) / mid * 10000."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')

    best_bid = ob['best_bid'][0]
    best_ask = ob['best_ask'][0]
    mid = (best_bid + best_ask) / Decimal('2')
    expected_bps = (best_ask - best_bid) / mid * Decimal('10000')

    assert ob['spread_bps'] == pytest.approx(expected_bps, rel=Decimal('1e-9'))


def test_order_book_values_are_decimal(client, mock_exchange):
    """Prices and quantities in the order book are Decimal, not float."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')

    price, qty = ob['bids'][0]
    assert isinstance(price, Decimal)
    assert isinstance(qty, Decimal)
    assert isinstance(ob['best_bid'][0], Decimal)
    assert isinstance(ob['mid_price'], Decimal)


def test_order_book_best_bid_is_highest_bid(client, mock_exchange):
    """best_bid matches the first (highest) element of bids list."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')
    assert ob['best_bid'] == ob['bids'][0]


def test_order_book_best_ask_is_lowest_ask(client, mock_exchange):
    """best_ask matches the first (lowest) element of asks list."""
    mock_exchange.fetch_order_book.return_value = _raw_orderbook()
    ob = client.fetch_order_book('ETH/USDT')
    assert ob['best_ask'] == ob['asks'][0]


# ---------------------------------------------------------------------------
# fetch_balance
# ---------------------------------------------------------------------------

def test_fetch_balance_filters_zeros(client, mock_exchange):
    """Zero-balance assets must not appear in the result."""
    mock_exchange.fetch_balance.return_value = _raw_balance()
    balance = client.fetch_balance()

    assert 'BNB' not in balance


def test_fetch_balance_non_zero_assets_present(client, mock_exchange):
    """Assets with non-zero balance are included."""
    mock_exchange.fetch_balance.return_value = _raw_balance()
    balance = client.fetch_balance()

    assert 'ETH' in balance
    assert 'USDT' in balance


def test_fetch_balance_values_are_decimal(client, mock_exchange):
    """free / locked / total must be Decimal."""
    mock_exchange.fetch_balance.return_value = _raw_balance()
    balance = client.fetch_balance()

    for field in ('free', 'locked', 'total'):
        assert isinstance(balance['ETH'][field], Decimal)


def test_fetch_balance_locked_maps_to_used(client, mock_exchange):
    """ccxt 'used' key is exposed as 'locked'."""
    mock_exchange.fetch_balance.return_value = _raw_balance()
    balance = client.fetch_balance()

    assert balance['USDT']['locked'] == Decimal('500')
    assert balance['USDT']['free'] == Decimal('20000')
    assert balance['USDT']['total'] == Decimal('20500')


# ---------------------------------------------------------------------------
# create_limit_ioc_order
# ---------------------------------------------------------------------------

def test_limit_ioc_returns_fill_info(client, mock_exchange):
    """IOC order result has fill qty, avg price, fee."""
    mock_exchange.create_order.return_value = _raw_order()
    result = client.create_limit_ioc_order('ETH/USDT', 'buy', 1.0, 2000.0)

    assert isinstance(result['amount_filled'], Decimal)
    assert isinstance(result['avg_fill_price'], Decimal)
    assert isinstance(result['fee'], Decimal)
    assert result['amount_filled'] == Decimal('0.8')
    assert result['avg_fill_price'] == Decimal('2000.5')
    assert result['fee'] == Decimal('1.6')
    assert result['fee_asset'] == 'USDT'


def test_limit_ioc_partial_fill_status(client, mock_exchange):
    """closed order with filled < requested maps to 'partially_filled'."""
    mock_exchange.create_order.return_value = _raw_order(
        status='closed', filled=0.3, amount=1.0
    )
    result = client.create_limit_ioc_order('ETH/USDT', 'buy', 1.0, 2000.0)
    assert result['status'] == 'partially_filled'


def test_limit_ioc_full_fill_status(client, mock_exchange):
    """closed order with filled == requested maps to 'filled'."""
    mock_exchange.create_order.return_value = _raw_order(
        status='closed', filled=1.0, amount=1.0
    )
    result = client.create_limit_ioc_order('ETH/USDT', 'buy', 1.0, 2000.0)
    assert result['status'] == 'filled'


def test_limit_ioc_expired_status(client, mock_exchange):
    """expired order maps to 'expired'."""
    mock_exchange.create_order.return_value = _raw_order(
        status='expired', filled=0.0, amount=1.0
    )
    result = client.create_limit_ioc_order('ETH/USDT', 'buy', 1.0, 2000.0)
    assert result['status'] == 'expired'


def test_limit_ioc_order_calls_ccxt_with_ioc(client, mock_exchange):
    """create_order must be called with timeInForce=IOC."""
    mock_exchange.create_order.return_value = _raw_order()
    client.create_limit_ioc_order('ETH/USDT', 'buy', 1.0, 2000.0)

    _, kwargs = mock_exchange.create_order.call_args
    assert kwargs.get('params', {}).get('timeInForce') == 'IOC'
    assert kwargs.get('type') == 'limit'


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

def test_rate_limiter_blocks_when_exhausted(client):
    """time.sleep is called when accumulated weight >= limit."""
    now = time.time()
    # Pre-fill the log to exactly the weight limit
    client._request_log = deque([(now, client._weight_limit)])

    with patch('src.exchange.client.time.sleep') as mock_sleep:
        client._rate_limit(1)

    mock_sleep.assert_called_once()
    sleep_secs = mock_sleep.call_args[0][0]
    assert sleep_secs > 0


def test_rate_limiter_does_not_sleep_when_under_limit(client):
    """No sleep when total weight is safely below the limit."""
    now = time.time()
    client._request_log = deque([(now, 100)])  # well under 1200

    with patch('src.exchange.client.time.sleep') as mock_sleep:
        client._rate_limit(1)

    mock_sleep.assert_not_called()


def test_rate_limiter_prunes_old_entries(client):
    """Entries outside the 60-second window are removed before checking."""
    old_ts = time.time() - 120   # 2 minutes ago — outside window
    client._request_log = deque([(old_ts, client._weight_limit)])

    with patch('src.exchange.client.time.sleep') as mock_sleep:
        client._rate_limit(1)   # should NOT block — old entry pruned

    mock_sleep.assert_not_called()


def test_rate_limiter_appends_new_entry(client):
    """After a call, the request log grows by one entry."""
    before = len(client._request_log)
    client._rate_limit(5)
    assert len(client._request_log) == before + 1


# ---------------------------------------------------------------------------
# _load_rate_limits
# ---------------------------------------------------------------------------

def test_load_rate_limits_reads_weight_limit(mock_exchange):
    """weight_limit is taken from exchangeInfo, not the hardcoded default."""
    mock_exchange.publicGetExchangeinfo.return_value = _exchange_info(weight_limit=2400)

    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ccxt.binance.return_value = mock_exchange
        c = ExchangeClient({'apiKey': 'k', 'secret': 's'})

    assert c._weight_limit == 2400


def test_load_rate_limits_converts_window_to_seconds(mock_exchange):
    """intervalNum=2, interval=MINUTE → window_sec=120."""
    mock_exchange.publicGetExchangeinfo.return_value = _exchange_info(
        interval='MINUTE', interval_num=2
    )

    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ccxt.binance.return_value = mock_exchange
        c = ExchangeClient({'apiKey': 'k', 'secret': 's'})

    assert c._window_sec == 120


def test_load_rate_limits_falls_back_on_error(mock_exchange):
    """If exchangeInfo raises, defaults are kept and no exception propagates."""
    mock_exchange.publicGetExchangeinfo.side_effect = Exception('network error')

    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ccxt.binance.return_value = mock_exchange
        c = ExchangeClient({'apiKey': 'k', 'secret': 's'})

    assert c._weight_limit == _DEFAULT_WEIGHT_LIMIT
    assert c._window_sec   == 60


def test_load_rate_limits_ignores_non_request_weight(mock_exchange):
    """ORDERS rate limit entry is ignored — only REQUEST_WEIGHT matters."""
    mock_exchange.publicGetExchangeinfo.return_value = {
        'rateLimits': [
            {'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': 50},
        ]
    }

    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ccxt.binance.return_value = mock_exchange
        c = ExchangeClient({'apiKey': 'k', 'secret': 's'})

    # No REQUEST_WEIGHT found → falls back to default
    assert c._weight_limit == _DEFAULT_WEIGHT_LIMIT


# ---------------------------------------------------------------------------
# _sync_weight_from_response
# ---------------------------------------------------------------------------

def test_sync_reads_header(client, mock_exchange):
    """Server weight is updated from X-MBX-USED-WEIGHT-1M header."""
    mock_exchange.last_response_headers = {'X-MBX-USED-WEIGHT-1M': '42'}
    client._sync_weight_from_response()
    assert client._server_used_weight == 42


def test_sync_reads_lowercase_header(client, mock_exchange):
    """Header lookup is case-insensitive (lowercase variant)."""
    mock_exchange.last_response_headers = {'x-mbx-used-weight-1m': '100'}
    client._sync_weight_from_response()
    assert client._server_used_weight == 100


def test_sync_no_op_when_header_absent(client, mock_exchange):
    """No crash and no change when header is missing."""
    mock_exchange.last_response_headers = {}
    client._server_used_weight = 50
    client._sync_weight_from_response()
    assert client._server_used_weight == 50   # unchanged


def test_rate_limiter_uses_server_weight_when_higher(client):
    """If server reports more usage than local log, server value wins."""
    client._server_used_weight = client._weight_limit - 1
    client._server_weight_ts   = time.time()   # fresh reading
    client._request_log        = deque()        # local log empty

    with patch('src.exchange.client.time.sleep') as mock_sleep:
        client._rate_limit(5)   # local says 0 used, but server says limit-1

    mock_sleep.assert_called_once()


def test_rate_limiter_ignores_stale_server_weight(client):
    """Stale server weight (older than window) is not used."""
    client._server_used_weight = client._weight_limit   # would block if trusted
    client._server_weight_ts   = time.time() - 200      # older than 60s window → stale
    client._request_log        = deque()                # local says 0

    with patch('src.exchange.client.time.sleep') as mock_sleep:
        client._rate_limit(1)

    mock_sleep.assert_not_called()   # stale server weight ignored


# ---------------------------------------------------------------------------
# Connection / init
# ---------------------------------------------------------------------------

def test_init_raises_on_auth_failure():
    """ConnectionError raised when ccxt throws AuthenticationError on init."""
    import ccxt as ccxt_real
    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ex = MagicMock()
        mock_ex.fetch_time.side_effect = ccxt_real.AuthenticationError('bad key')
        mock_ccxt.binance.return_value = mock_ex
        mock_ccxt.AuthenticationError = ccxt_real.AuthenticationError
        mock_ccxt.NetworkError = ccxt_real.NetworkError

        with pytest.raises(ConnectionError, match='Auth'):
            ExchangeClient({'apiKey': 'bad', 'secret': 'bad', 'sandbox': True})  # pragma: allowlist secret


def test_get_trading_fees_fallback_on_testnet(client, mock_exchange):
    """NotSupported (testnet sapi) returns standard 0.1% fallback instead of raising."""
    import ccxt as ccxt_real
    mock_exchange.fetch_trading_fee.side_effect = ccxt_real.NotSupported('no sapi on testnet')

    with patch('src.exchange.client.ccxt.NotSupported', ccxt_real.NotSupported):
        fees = client.get_trading_fees('ETH/USDT')

    assert fees == {'maker': Decimal('0.001'), 'taker': Decimal('0.001')}


def test_init_raises_on_network_error():
    """ConnectionError raised when ccxt throws NetworkError on init."""
    import ccxt as ccxt_real
    with patch('src.exchange.client.ccxt') as mock_ccxt:
        mock_ex = MagicMock()
        mock_ex.fetch_time.side_effect = ccxt_real.NetworkError('unreachable')
        mock_ccxt.binance.return_value = mock_ex
        mock_ccxt.AuthenticationError = ccxt_real.AuthenticationError
        mock_ccxt.NetworkError = ccxt_real.NetworkError

        with pytest.raises(ConnectionError, match='Network'):
            ExchangeClient({'apiKey': 'k', 'secret': 's', 'sandbox': True})