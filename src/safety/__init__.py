"""Public API of the safety module — risk, validation, kill switches, pause, metrics."""
from src.safety.api_key_health import ApiKeyHealthCheck, ApiKeyStatus
from src.safety.killswitch import (
    ABSOLUTE_MAX_DAILY_LOSS,
    ABSOLUTE_MAX_TRADE_USD,
    ABSOLUTE_MAX_TRADES_PER_HOUR,
    ABSOLUTE_MIN_CAPITAL,
    KILL_SWITCH_FILE,
    AutoKillSwitch,
    is_kill_switch_active,
    safety_check,
    write_heartbeat,
)
from src.safety.limits import RiskLimits, RiskManager
from src.safety.metrics import ErrorTracker, ExecutionMetrics
from src.safety.pause import TradingPauseManager
from src.safety.validator import (
    ABSURD_SPREAD_BPS,
    MAX_SIGNAL_AGE_SECONDS,
    PreTradeValidator,
)

__all__ = [
    'RiskLimits',
    'RiskManager',
    'PreTradeValidator',
    'AutoKillSwitch',
    'TradingPauseManager',
    'ExecutionMetrics',
    'ErrorTracker',
    'ApiKeyHealthCheck',
    'ApiKeyStatus',
    'is_kill_switch_active',
    'safety_check',
    'write_heartbeat',
    'KILL_SWITCH_FILE',
    'ABSOLUTE_MAX_TRADE_USD',
    'ABSOLUTE_MAX_DAILY_LOSS',
    'ABSOLUTE_MIN_CAPITAL',
    'ABSOLUTE_MAX_TRADES_PER_HOUR',
    'ABSURD_SPREAD_BPS',
    'MAX_SIGNAL_AGE_SECONDS',
]
