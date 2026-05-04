"""Unit tests for ApiKeyHealthCheck and ApiKeyStatus."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.safety import ApiKeyHealthCheck, ApiKeyStatus


def test_invalid_key_when_fetch_balance_raises():
    ex = MagicMock()
    ex.fetch_balance.side_effect = RuntimeError("auth: -2014")
    status = ApiKeyHealthCheck(ex).check()
    assert status.valid is False
    assert "auth" in status.error_msg
    assert status.expires_at is None
    assert status.days_remaining is None


def test_valid_with_expiration():
    ex = MagicMock()
    ex.fetch_balance.return_value = {}
    future_ms = int((datetime.now() + timedelta(days=30)).timestamp() * 1000)
    ex.sapi_get_account_apirestrictions.return_value = {
        'ipRestrict': False,
        'tradingAuthorityExpirationTime': future_ms,
    }
    status = ApiKeyHealthCheck(ex).check()
    assert status.valid is True
    assert status.expires_at is not None
    assert 29 < status.days_remaining < 31
    assert status.ip_restricted is False


def test_valid_ip_whitelisted_no_expiration():
    ex = MagicMock()
    ex.fetch_balance.return_value = {}
    ex.sapi_get_account_apirestrictions.return_value = {
        'ipRestrict': True,
    }
    status = ApiKeyHealthCheck(ex).check()
    assert status.valid is True
    assert status.expires_at is None
    assert status.days_remaining is None
    assert status.ip_restricted is True


def test_apirestrictions_endpoint_failure_non_fatal():
    ex = MagicMock()
    ex.fetch_balance.return_value = {}
    ex.sapi_get_account_apirestrictions.side_effect = RuntimeError("404 not found")
    status = ApiKeyHealthCheck(ex).check()
    assert status.valid is True
    assert status.expires_at is None
    assert status.ip_restricted is None


def test_is_expiring_soon_threshold_inclusive():
    s = ApiKeyStatus(valid=True, expires_at=datetime.now(),
                     days_remaining=7.0, error_msg=None, ip_restricted=False)
    assert s.is_expiring_soon(threshold_days=7.0) is True
    assert s.is_expiring_soon(threshold_days=6.0) is False


def test_is_expiring_soon_no_expiration_returns_false():
    s = ApiKeyStatus(valid=True, expires_at=None,
                     days_remaining=None, error_msg=None, ip_restricted=True)
    assert s.is_expiring_soon() is False


def test_zero_or_missing_expiration_treated_as_permanent():
    ex = MagicMock()
    ex.fetch_balance.return_value = {}
    ex.sapi_get_account_apirestrictions.return_value = {
        'ipRestrict': True,
        'tradingAuthorityExpirationTime': 0,
    }
    status = ApiKeyHealthCheck(ex).check()
    assert status.expires_at is None
    assert status.days_remaining is None
