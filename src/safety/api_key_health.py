"""Pre-flight and runtime checks for Binance API-key validity & expiration.

Binance's Spot & Margin trading permission expires 90 days after activation
(if no IP whitelist). After expiration the system silently revokes that
permission — read endpoints keep working, but every order submission fails.
Without an explicit check, the bot would only notice via AutoKillSwitch's
50-errors/hour threshold — far too late.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ApiKeyStatus:
    valid:           bool
    expires_at:      Optional[datetime]   # None == permanent (IP-whitelisted) or unknown
    days_remaining:  Optional[float]      # None when expires_at is None
    error_msg:       Optional[str]        # populated when valid=False
    ip_restricted:   Optional[bool]       # True when IP-whitelist is on (key won't expire)

    def is_expiring_soon(self, threshold_days: float = 7.0) -> bool:
        return (
            self.days_remaining is not None
            and self.days_remaining <= threshold_days
        )


class ApiKeyHealthCheck:
    """Wraps a raw ccxt exchange instance and returns a structured ApiKeyStatus.

    Strategy:
      1) Liveness — call fetch_balance(). On auth/permission error → valid=False.
      2) Expiration — call sapi_get_account_apirestrictions(). Read
         `tradingAuthorityExpirationTime` (ms timestamp; null/0/missing = permanent).
         Failure on this probe is non-fatal (testnet builds may lack the endpoint).
    """

    def __init__(self, exchange):
        self.exchange = exchange  # raw ccxt.binance instance

    def check(self) -> ApiKeyStatus:
        try:
            self.exchange.fetch_balance()
        except Exception as e:
            return ApiKeyStatus(
                valid=False,
                expires_at=None,
                days_remaining=None,
                error_msg=f"fetch_balance failed: {e}",
                ip_restricted=None,
            )

        expires_at: Optional[datetime] = None
        ip_restricted: Optional[bool] = None
        try:
            r = self.exchange.sapi_get_account_apirestrictions()
            ip_restricted = bool(r.get('ipRestrict', False))
            ts_ms = r.get('tradingAuthorityExpirationTime')
            if ts_ms:
                expires_at = datetime.fromtimestamp(int(ts_ms) / 1000)
        except Exception as e:
            logger.warning("apiRestrictions probe failed (non-fatal): %s", e)

        days_remaining = None
        if expires_at is not None:
            days_remaining = (expires_at - datetime.now()).total_seconds() / 86400

        return ApiKeyStatus(
            valid=True,
            expires_at=expires_at,
            days_remaining=days_remaining,
            error_msg=None,
            ip_restricted=ip_restricted,
        )
