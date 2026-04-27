"""Pydantic configuration schema for the arb bot."""
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    """Holds the signing key. Required only when dry_run=False or when DEX legs need signing."""
    private_key: str

    @field_validator('private_key')
    @classmethod
    def _validate_private_key(cls, v: str) -> str:
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
    exchange: CexExchange = CexExchange.BINANCE
    api_key: str
    secret: str
    testnet: bool = True

    @field_validator('api_key', 'secret')
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
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
