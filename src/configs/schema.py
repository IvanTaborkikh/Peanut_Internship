"""Pydantic configuration schema for the arb bot."""
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Mode(str, Enum):
    TEST = "test"
    PROD = "prod"


class ChainId(int, Enum):
    ETH_MAINNET = 1
    ARBITRUM = 42161


class CexExchange(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"


class WalletConfig(BaseModel):
    """Holds the signing key. Required only when dry_run=False or when DEX legs need signing.

    `private_key` is a SecretStr — repr() and model_dump_json() both mask it.
    Read it explicitly with `.private_key.get_secret_value()` when actually signing.
    """
    private_key: SecretStr

    @field_validator('private_key', mode='before')
    @classmethod
    def _validate_private_key(cls, v) -> str:
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if not isinstance(v, str):
            raise ValueError("private_key must be a string")
        clean = v.removeprefix('0x').removeprefix('0X')
        if len(clean) != 64:
            raise ValueError("private_key must be 32 bytes (64 hex chars)")
        try:
            int(clean, 16)
        except ValueError as e:
            raise ValueError("private_key must be valid hex") from e
        return '0x' + clean.lower()


class ChainConfig(BaseModel):
    chain_id: ChainId
    rpc_url: str
    ws_url: Optional[str] = None

    @field_validator('rpc_url')
    @classmethod
    def _rpc_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("rpc_url must not be empty")
        return v


class CexConfig(BaseModel):
    """`api_key` and `secret` are SecretStr — masked in repr() / JSON dumps.
    Use `.get_secret_value()` when handing them to ccxt or HTTP clients.
    """
    exchange: CexExchange = CexExchange.BINANCE
    api_key: SecretStr
    secret: SecretStr
    testnet: bool = True

    @field_validator('api_key', 'secret', mode='before')
    @classmethod
    def _not_empty(cls, v) -> str:
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if not isinstance(v, str) or not v.strip():
            raise ValueError("api_key and secret must not be empty")
        return v


class FeesConfig(BaseModel):
    cex_taker_bps: Decimal = Decimal('10')
    cex_maker_bps: Decimal = Decimal('2')
    dex_swap_bps: Decimal = Decimal('30')
    gas_cost_usd_default: Decimal = Decimal('0.5')

    @field_validator('*')
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("fees must be non-negative")
        return v


class SignalConfig(BaseModel):
    min_spread_bps: Decimal = Decimal('50')
    min_profit_usd: Decimal = Decimal('5')
    max_position_usd: Decimal = Decimal('10000')
    signal_ttl_seconds: float = 5.0
    cooldown_seconds: float = 2.0
    min_score: Decimal = Decimal('60')


class ExecutorConfig(BaseModel):
    leg1_timeout_seconds: float = 5.0
    leg2_timeout_seconds: float = 60.0
    min_fill_ratio: Decimal = Decimal('0.8')
    slippage_bps: Decimal = Decimal('30')
    use_flashbots: bool = False


class RiskLimitsConfig(BaseModel):
    """Configurable risk thresholds. Validated against ABSOLUTE_* hardcoded
    ceilings in safety.killswitch — any value above the absolute is rejected.
    """
    initial_capital_usd:    Decimal = Decimal('100')

    max_trade_usd:          Decimal = Decimal('20')
    max_trade_pct:          Decimal = Decimal('0.20')
    max_position_per_token: Decimal = Decimal('30')
    max_open_positions:     int     = 1

    max_loss_per_trade:     Decimal = Decimal('5')
    max_daily_loss:         Decimal = Decimal('15')
    max_drawdown_pct:       Decimal = Decimal('0.20')

    max_trades_per_hour:    int     = 20
    consecutive_loss_limit: int     = 3

    @model_validator(mode='after')
    def _within_absolute_limits(self) -> 'RiskLimitsConfig':
        # Imported here to avoid a circular-import in the safety package.
        from src.safety.killswitch import (
            ABSOLUTE_MAX_DAILY_LOSS,
            ABSOLUTE_MAX_TRADE_USD,
            ABSOLUTE_MAX_TRADES_PER_HOUR,
            ABSOLUTE_MIN_CAPITAL,
        )
        if self.max_trade_usd > ABSOLUTE_MAX_TRADE_USD:
            raise ValueError(
                f"max_trade_usd {self.max_trade_usd} exceeds ABSOLUTE_MAX_TRADE_USD {ABSOLUTE_MAX_TRADE_USD}"
            )
        if self.max_daily_loss > ABSOLUTE_MAX_DAILY_LOSS:
            raise ValueError(
                f"max_daily_loss {self.max_daily_loss} exceeds ABSOLUTE_MAX_DAILY_LOSS {ABSOLUTE_MAX_DAILY_LOSS}"
            )
        if self.initial_capital_usd < ABSOLUTE_MIN_CAPITAL:
            raise ValueError(
                f"initial_capital_usd {self.initial_capital_usd} below ABSOLUTE_MIN_CAPITAL {ABSOLUTE_MIN_CAPITAL}"
            )
        if self.max_trades_per_hour > ABSOLUTE_MAX_TRADES_PER_HOUR:
            raise ValueError(
                f"max_trades_per_hour {self.max_trades_per_hour} exceeds ABSOLUTE_MAX_TRADES_PER_HOUR {ABSOLUTE_MAX_TRADES_PER_HOUR}"
            )
        return self

    def to_runtime(self):
        """Translate to the safety.RiskLimits dataclass used at runtime."""
        from src.safety.limits import RiskLimits
        return RiskLimits(
            max_trade_usd=self.max_trade_usd,
            max_trade_pct=self.max_trade_pct,
            max_position_per_token=self.max_position_per_token,
            max_open_positions=self.max_open_positions,
            max_loss_per_trade=self.max_loss_per_trade,
            max_daily_loss=self.max_daily_loss,
            max_drawdown_pct=self.max_drawdown_pct,
            max_trades_per_hour=self.max_trades_per_hour,
            consecutive_loss_limit=self.consecutive_loss_limit,
        )


class PairConfig(BaseModel):
    pair: str
    chain_id: ChainId
    pool_address: Optional[str] = None
    trade_size: Decimal

    @field_validator('pair')
    @classmethod
    def _pair_format(cls, v: str) -> str:
        parts = v.split('/')
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise ValueError(f"pair must be 'BASE/QUOTE', got: {v!r}")
        return v.upper()


class DexConfig(BaseModel):
    """Which Uniswap version to use for DEX-side quoting.

    `v2`: existing PricingEngine + UniswapV2Pair (reserves-based math).
    `v3`: new UniswapV3PricingEngine via QuoterV2 contract (per-tick liquidity).
    `fee_tier` is only consulted in v3 (100 = 0.01%, 500 = 0.05%, 3000 = 0.3%, 10000 = 1%).
    """
    version: Literal['v2', 'v3'] = 'v2'
    fee_tier: int = 3000


class BotConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    mode: Mode
    dry_run: bool = True
    cex: CexConfig
    wallet: Optional[WalletConfig] = None
    chains: list[ChainConfig]
    pairs: list[PairConfig]
    fees: FeesConfig = Field(default_factory=FeesConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    risk: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)

    # New optional fields — defaults preserve all existing v2 configs.
    dex: DexConfig = Field(default_factory=DexConfig)
    # Map CEX-side quote symbol → DEX-side quote symbol (e.g. {"USDT": "USDC"}
    # when the CEX lists CHIP/USDT but only CHIP/USDC pool exists on Uniswap).
    dex_quote_override: dict[str, str] = Field(default_factory=dict)
    # Enables Binance USDC/USDT live ratio for cross-stablecoin spread accuracy.
    stablecoin_converter: bool = False

    @field_validator('chains')
    @classmethod
    def _unique_chain_ids(cls, v: list[ChainConfig]) -> list[ChainConfig]:
        ids = [c.chain_id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate chain_id in chains: {ids}")
        return v

    @model_validator(mode='after')
    def _pairs_reference_known_chains(self) -> 'BotConfig':
        known = {c.chain_id for c in self.chains}
        for p in self.pairs:
            if p.chain_id not in known:
                raise ValueError(
                    f"pair {p.pair!r} references chain {p.chain_id} which is not in chains list"
                )
        return self

    @model_validator(mode='after')
    def _prod_requires_wallet(self) -> 'BotConfig':
        if self.mode == Mode.PROD and self.wallet is None:
            raise ValueError("wallet is required in PROD mode")
        return self

    def get_chain(self, chain_id: ChainId) -> ChainConfig:
        for c in self.chains:
            if c.chain_id == chain_id:
                return c
        raise KeyError(f"chain_id {chain_id} not configured")
