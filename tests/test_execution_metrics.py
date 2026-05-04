"""Unit tests for ExecutionMetrics counters and execution_rate."""
from src.safety import ExecutionMetrics


def test_default_zero_state():
    m = ExecutionMetrics()
    assert m.signals_generated == 0
    assert m.execution_rate == 0.0


def test_execution_rate_basic():
    m = ExecutionMetrics(signals_generated=10, trades_executed=3)
    assert m.execution_rate == 30.0


def test_execution_rate_zero_signals_safe():
    m = ExecutionMetrics(signals_generated=0, trades_executed=0)
    assert m.execution_rate == 0.0


def test_snapshot_keys():
    m = ExecutionMetrics(signals_generated=4, trades_executed=2)
    snap = m.snapshot()
    assert snap['signals_generated'] == 4
    assert snap['trades_executed'] == 2
    assert snap['execution_rate_pct'] == 50.0
    for k in [
        'signals_passed_validator',
        'signals_passed_risk',
        'signals_passed_score',
        'trades_successful',
        'trades_failed',
    ]:
        assert k in snap
