"""Unit tests for ErrorTracker windowed counts."""
import time
from src.safety import ErrorTracker


def test_empty_state():
    et = ErrorTracker()
    assert et.count() == 0
    assert et.summary() == {}


def test_add_and_count():
    et = ErrorTracker()
    et.add('timeout')
    et.add('timeout')
    et.add('rejected')
    assert et.count() == 3
    assert et.summary() == {'timeout': 2, 'rejected': 1}


def test_window_expiry():
    et = ErrorTracker(window_minutes=60)
    # Inject a stale event manually.
    et._events.append((time.time() - 7200, 'timeout'))
    et.add('rejected')   # also calls _evict
    assert et.count() == 1
    assert et.summary() == {'rejected': 1}


def test_summary_groups_only_active_events():
    et = ErrorTracker(window_minutes=60)
    et._events.append((time.time() - 7200, 'old'))
    et._events.append((time.time() - 60, 'fresh'))
    et._events.append((time.time() - 30, 'fresh'))
    s = et.summary()
    assert s == {'fresh': 2}
