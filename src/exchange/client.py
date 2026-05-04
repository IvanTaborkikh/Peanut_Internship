import ccxt
import time
import logging
from collections import deque
from decimal import Decimal

logger = logging.getLogger(__name__)

# Fallback limits used before exchangeInfo is loaded (or if it fails)
_DEFAULT_WEIGHT_LIMIT = 1200
_DEFAULT_WINDOW_SEC   = 60

# Standard Binance spot fees — used when testnet sapi endpoint is unavailable
_TESTNET_FEE_FALLBACK = {
    'maker': Decimal('0.001'),
    'taker': Decimal('0.001'),
}


class ExchangeClient:
    """
    Wrapper around ccxt for Binance testnet.
    Handles rate limiting, error handling, and response normalization.
    """

    def __init__(self, config: dict):
        """
        Initialize with config dict containing apiKey, secret, sandbox flag.
        Loads actual rate limits from exchangeInfo, then validates connection.
        """
        self._exchange = ccxt.binance({
            'apiKey': config.get('apiKey'),
            'secret': config.get('secret'),
            'options': config.get('options', {'defaultType': 'spot'}),
            'enableRateLimit': config.get('enableRateLimit', True),
        })
        if config.get('sandbox', False):
            self._exchange.set_sandbox_mode(True)

        # (timestamp, weight) pairs within the current window
        self._request_log: deque = deque()

        # Start with safe defaults; overwritten by _load_rate_limits()
        self._weight_limit = _DEFAULT_WEIGHT_LIMIT
        self._window_sec   = _DEFAULT_WINDOW_SEC

        # Last weight reading from server response header (X-MBX-USED-WEIGHT-1M)
        self._server_used_weight: int   = 0
        self._server_weight_ts:   float = 0.0

        self._load_rate_limits()
        self._check_connection()

    @classmethod
    def from_config(cls, cex_config) -> "ExchangeClient":
        """Build from a `CexConfig` (pydantic model with SecretStr fields).

        Translates SecretStr → str at the boundary so ccxt sees a normal dict.
        Use this in production paths; the dict ctor remains for tests.
        """
        return cls({
            'apiKey':  cex_config.api_key.get_secret_value(),
            'secret':  cex_config.secret.get_secret_value(),
            'sandbox': cex_config.testnet,
            'options': {'defaultType': 'spot'},
            'enableRateLimit': True,
        })

    def client_for_health_check(self):
        """Return the underlying ccxt instance for `ApiKeyHealthCheck`.

        Sole intended caller is the safety health-check probe; nothing else
        should reach into the raw ccxt client directly.
        """
        return self._exchange

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_rate_limits(self) -> None:
        """
        Fetch actual rate limits from Binance /api/v3/exchangeInfo.
        Updates self._weight_limit and self._window_sec.
        Falls back to defaults if the call fails.

        exchangeInfo returns a list like:
        [
          {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE",
           "intervalNum": 1, "limit": 6000},
          {"rateLimitType": "ORDERS", ...},
        ]
        We only care about REQUEST_WEIGHT.
        """
        try:
            info = self._exchange.publicGetExchangeinfo()

            for rl in info.get('rateLimits', []):
                if rl.get('rateLimitType') != 'REQUEST_WEIGHT':
                    continue

                limit = int(rl['limit'])
                interval = rl.get('interval', 'MINUTE').upper()
                interval_num = int(rl.get('intervalNum', 1))

                # Convert to seconds
                interval_sec = {'SECOND': 1, 'MINUTE': 60, 'HOUR': 3600}.get(interval, 60)
                window_sec = interval_sec * interval_num

                self._weight_limit = limit
                self._window_sec = window_sec
                logger.info(
                    f"Rate limits loaded from exchangeInfo: "
                    f"{self._weight_limit} weight / {self._window_sec}s"
                )
                return

            logger.warning("REQUEST_WEIGHT entry not found in exchangeInfo — using defaults")

        except Exception as e:
            logger.warning(
                f"_load_rate_limits failed ({e}) — "
                f"using defaults ({_DEFAULT_WEIGHT_LIMIT}/{_DEFAULT_WINDOW_SEC}s)"
            )

    def _check_connection(self) -> None:
        try:
            logger.info("Checking exchange connection...")
            server_time = self._exchange.fetch_time()
            logger.info(f"Connected to exchange. Server time: {server_time}")
        except ccxt.AuthenticationError as e:
            raise ConnectionError(f"Authentication failed on init: {e}") from e
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error on init: {e}") from e

    def _sync_weight_from_response(self) -> None:
        """
        Read X-MBX-USED-WEIGHT-1M from the last response header and store it.
        Binance returns the actual accumulated weight for the current minute window.
        No-op if the header is absent (e.g. testnet or connection error).
        """
        headers = self._exchange.last_response_headers or {}
        raw = headers.get('X-MBX-USED-WEIGHT-1M') or headers.get('x-mbx-used-weight-1m')
        if raw is not None:
            self._server_used_weight = int(raw)
            self._server_weight_ts   = time.time()
            logger.debug(f"server used weight: {self._server_used_weight}/{self._weight_limit}")

    def _rate_limit(self, weight: int = 1) -> None:
        """
        Sleep if the next request would exceed the per-minute quota.

        Uses two sources for the current usage:
        - Local request log (rolling window of our own calls)
        - Server header X-MBX-USED-WEIGHT-1M from the last response (source of truth)

        Takes the higher of the two to stay conservative.
        """
        now = time.time()
        # Drop local entries outside the rolling window
        while self._request_log and self._request_log[0][0] < now - self._window_sec:
            self._request_log.popleft()

        local_used = sum(w for _, w in self._request_log)

        # If server weight was received recently, treat it as authoritative
        if now - self._server_weight_ts < self._window_sec:
            used = max(local_used, self._server_used_weight)
        else:
            used = local_used

        if used + weight > self._weight_limit:
            oldest_ts = self._request_log[0][0] if self._request_log else now
            sleep_for = self._window_sec - (now - oldest_ts) + 0.1
            logger.warning(
                f"Rate limit: used={used}/{self._weight_limit}, sleeping {sleep_for:.2f}s"
            )
            time.sleep(max(sleep_for, 0))
            now = time.time()
            while self._request_log and self._request_log[0][0] < now - self._window_sec:
                self._request_log.popleft()

        self._request_log.append((time.time(), weight))

    def _normalize_order(self, order: dict) -> dict:
        """Convert a raw ccxt order dict to the project's canonical format."""
        filled = Decimal(str(order.get('filled') or 0))
        requested = Decimal(str(order.get('amount') or 0))
        avg_price = Decimal(str(order.get('average') or 0))

        fee_info = order.get('fee') or {}
        fee = Decimal(str(fee_info.get('cost') or 0))
        fee_asset = fee_info.get('currency', '')

        raw_status = order.get('status', '')
        if raw_status == 'closed':
            status = 'filled' if filled >= requested else 'partially_filled'
        elif raw_status in ('canceled', 'expired', 'rejected'):
            status = 'expired'
        else:
            status = raw_status

        return {
            'id': str(order['id']),
            'symbol': order['symbol'],
            'side': order['side'],
            'type': order.get('type', 'limit'),
            'time_in_force': order.get('timeInForce', ''),
            'amount_requested': requested,
            'amount_filled': filled,
            'avg_fill_price': avg_price,
            'fee': fee,
            'fee_asset': fee_asset,
            'status': status,
            'timestamp': order.get('timestamp', 0),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        """
        Fetch L2 order book snapshot.

        Returns normalized dict:
        {
            'symbol': 'ETH/USDT',
            'timestamp': 1706000000000,
            'bids': [(price, qty), ...],  # Sorted best->worst
            'asks': [(price, qty), ...],  # Sorted best->worst
            'best_bid': (price, qty),
            'best_ask': (price, qty),
            'mid_price': Decimal,
            'spread_bps': Decimal,
        }
        """
        weight = 2 if limit <= 20 else 5 if limit <= 100 else 10
        self._rate_limit(weight)
        logger.info(f"fetch_order_book: symbol={symbol} limit={limit}")

        try:
            ob = self._exchange.fetch_order_book(symbol, limit)
        except ccxt.RateLimitExceeded as e:
            logger.error(f"fetch_order_book rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"fetch_order_book auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"fetch_order_book network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"fetch_order_book exchange error: {e}")
            raise

        self._sync_weight_from_response()
        bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in ob['bids']]
        asks = [(Decimal(str(p)), Decimal(str(q))) for p, q in ob['asks']]

        best_bid = bids[0] if bids else (Decimal('0'), Decimal('0'))
        best_ask = asks[0] if asks else (Decimal('0'), Decimal('0'))
        mid_price = (best_bid[0] + best_ask[0]) / Decimal('2')
        spread_bps = (
            (best_ask[0] - best_bid[0]) / mid_price * Decimal('10000')
            if mid_price
            else Decimal('0')
        )

        result = {
            'symbol': symbol,
            'timestamp': ob.get('timestamp') or int(time.time() * 1000),
            'bids': bids,
            'asks': asks,
            'best_bid': best_bid,
            'best_ask': best_ask,
            'mid_price': mid_price,
            'spread_bps': spread_bps,
        }
        logger.info(
            f"fetch_order_book: mid={mid_price} spread={spread_bps:.2f}bps "
            f"bids={len(bids)} asks={len(asks)}"
        )
        return result

    def fetch_balance(self) -> dict[str, dict]:
        """
        Fetch account balances. Filters out zero-balance assets.

        Returns:
        {
            'ETH':  {'free': Decimal, 'locked': Decimal, 'total': Decimal},
            'USDT': {'free': Decimal, 'locked': Decimal, 'total': Decimal},
            ...
        }
        """
        self._rate_limit()
        logger.debug("fetch_balance: requesting account balances")

        try:
            raw = self._exchange.fetch_balance()
        except ccxt.RateLimitExceeded as e:
            logger.error(f"fetch_balance rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"fetch_balance auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"fetch_balance network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"fetch_balance exchange error: {e}")
            raise

        result = {}
        for asset, data in raw.items():
            if asset in ('info', 'free', 'used', 'total', 'debt') or not isinstance(data, dict):
                continue
            free = Decimal(str(data.get('free') or 0))
            locked = Decimal(str(data.get('used') or 0))
            total = Decimal(str(data.get('total') or 0))
            if total > 0:
                result[asset] = {'free': free, 'locked': locked, 'total': total}

        self._sync_weight_from_response()
        logger.debug(f"fetch_balance: non-zero assets={list(result.keys())}")
        return result

    def create_limit_ioc_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
    ) -> dict:
        """
        Place a LIMIT IOC (Immediate Or Cancel) order.

        Returns normalized order result:
        {
            'id': str,
            'symbol': str,
            'side': str,
            'type': 'limit',
            'time_in_force': 'IOC',
            'amount_requested': Decimal,
            'amount_filled': Decimal,
            'avg_fill_price': Decimal,
            'fee': Decimal,
            'fee_asset': str,
            'status': str,   # 'filled', 'partially_filled', 'expired'
            'timestamp': int,
        }
        """
        self._rate_limit()
        logger.info(
            f"create_limit_ioc_order: symbol={symbol} side={side} "
            f"amount={amount} price={price}"
        )

        try:
            order = self._exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side,
                amount=amount,
                price=price,
                params={'timeInForce': 'IOC'},
            )
        except ccxt.InsufficientFunds as e:
            logger.error(f"create_limit_ioc_order insufficient funds: {e}")
            raise
        except ccxt.InvalidOrder as e:
            logger.error(f"create_limit_ioc_order invalid order: {e}")
            raise
        except ccxt.RateLimitExceeded as e:
            logger.error(f"create_limit_ioc_order rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"create_limit_ioc_order auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"create_limit_ioc_order network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"create_limit_ioc_order exchange error: {e}")
            raise

        self._sync_weight_from_response()
        result = self._normalize_order(order)
        logger.info(
            f"create_limit_ioc_order: id={result['id']} "
            f"status={result['status']} filled={result['amount_filled']}"
        )
        return result

    def create_market_order(self, symbol: str, side: str, amount: float) -> dict:
        """
        Place a market order. Same return format as create_limit_ioc_order.
        Use sparingly — LIMIT IOC is preferred for arb.
        """
        self._rate_limit()
        logger.info(f"create_market_order: symbol={symbol} side={side} amount={amount}")

        try:
            order = self._exchange.create_order(
                symbol=symbol,
                type='market',
                side=side,
                amount=amount,
            )
        except ccxt.InsufficientFunds as e:
            logger.error(f"create_market_order insufficient funds: {e}")
            raise
        except ccxt.InvalidOrder as e:
            logger.error(f"create_market_order invalid order: {e}")
            raise
        except ccxt.RateLimitExceeded as e:
            logger.error(f"create_market_order rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"create_market_order auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"create_market_order network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"create_market_order exchange error: {e}")
            raise

        self._sync_weight_from_response()
        result = self._normalize_order(order)
        logger.info(
            f"create_market_order: id={result['id']} "
            f"status={result['status']} filled={result['amount_filled']}"
        )
        return result

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order. Returns normalized order status after cancel."""
        self._rate_limit()
        logger.info(f"cancel_order: id={order_id} symbol={symbol}")

        try:
            order = self._exchange.cancel_order(order_id, symbol)
        except ccxt.OrderNotFound as e:
            logger.error(f"cancel_order not found: {e}")
            raise
        except ccxt.RateLimitExceeded as e:
            logger.error(f"cancel_order rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"cancel_order auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"cancel_order network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"cancel_order exchange error: {e}")
            raise

        self._sync_weight_from_response()
        result = self._normalize_order(order)
        logger.info(f"cancel_order: id={result['id']} status={result['status']}")
        return result

    def fetch_order_status(self, order_id: str, symbol: str) -> dict:
        """Check current status of an order. Returns normalized order dict."""
        self._rate_limit()
        logger.info(f"fetch_order_status: id={order_id} symbol={symbol}")

        try:
            order = self._exchange.fetch_order(order_id, symbol)
        except ccxt.OrderNotFound as e:
            logger.error(f"fetch_order_status not found: {e}")
            raise
        except ccxt.RateLimitExceeded as e:
            logger.error(f"fetch_order_status rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"fetch_order_status auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"fetch_order_status network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"fetch_order_status exchange error: {e}")
            raise

        self._sync_weight_from_response()
        result = self._normalize_order(order)
        logger.info(f"fetch_order_status: id={result['id']} status={result['status']}")
        return result

    def get_trading_fees(self, symbol: str) -> dict:
        """
        Returns fee structure:
        {'maker': Decimal('0.001'), 'taker': Decimal('0.001')}

        Note: Binance testnet does not support the sapi fee endpoint.
        Falls back to standard Binance spot fees (0.1% maker/taker).
        """
        self._rate_limit()
        logger.info(f"get_trading_fees: symbol={symbol}")

        try:
            fees = self._exchange.fetch_trading_fee(symbol)
        except ccxt.NotSupported:
            logger.warning(
                "get_trading_fees: sapi endpoint not supported on testnet, "
                f"using fallback {_TESTNET_FEE_FALLBACK}"
            )
            return _TESTNET_FEE_FALLBACK
        except ccxt.RateLimitExceeded as e:
            logger.error(f"get_trading_fees rate limit exceeded: {e}")
            raise
        except ccxt.AuthenticationError as e:
            logger.error(f"get_trading_fees auth error: {e}")
            raise
        except ccxt.NetworkError as e:
            logger.error(f"get_trading_fees network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"get_trading_fees exchange error: {e}")
            raise

        self._sync_weight_from_response()
        result = {
            'maker': Decimal(str(fees.get('maker', 0))),
            'taker': Decimal(str(fees.get('taker', 0))),
        }
        logger.info(f"get_trading_fees: {result}")
        return result
