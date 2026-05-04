"""Tests for config loading, validation, env expansion, and tokens registry."""
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.configs.loader import ConfigError, _expand_env_vars, load_config, load_config_from_dict
from src.configs.schema import (
    BotConfig,
    ChainId,
    FeesConfig,
    Mode,
    PairConfig,
    RiskLimitsConfig,
    WalletConfig,
)
from src.safety.killswitch import (
    ABSOLUTE_MAX_DAILY_LOSS,
    ABSOLUTE_MAX_TRADE_USD,
    ABSOLUTE_MIN_CAPITAL,
)
from src.safety.limits import RiskLimits as RuntimeRiskLimits
from src.configs.tokens import TOKENS, get_factory, get_router, get_token


REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_YAML = REPO_ROOT / 'configs' / 'test.yaml'
PROD_YAML = REPO_ROOT / 'configs' / 'prod.yaml'

VALID_PRIVATE_KEY = '0x' + '1' * 64  # pragma: allowlist secret

FAKE_ENV = {
    'BINANCE_TESTNET_API_KEY': 'test_key',         # pragma: allowlist secret
    'BINANCE_TESTNET_SECRET':  'test_secret',      # pragma: allowlist secret
    'BINANCE_API_KEY':         'prod_key',         # pragma: allowlist secret
    'BINANCE_SECRET':          'prod_secret',      # pragma: allowlist secret
    'PRIVATE_KEY':             VALID_PRIVATE_KEY,       # pragma: allowlist secret
    'MAINNET_RPC_URL':         'https://eth.example/v1',    # pragma: allowlist secret
    'WS_RPC_URL':              'wss://eth.example/ws',      # pragma: allowlist secret
    'RPC_URL':                 'https://arb.example/v1',    # pragma: allowlist secret
}


# --- env expansion ---

def test_expand_env_vars_replaces_placeholders():
    out = _expand_env_vars("a=${FOO} b=${BAR}", {'FOO': '1', 'BAR': '2'})
    assert out == "a=1 b=2"


def test_expand_env_vars_raises_on_missing():
    with pytest.raises(ConfigError, match="MISSING_VAR"):
        _expand_env_vars("x=${MISSING_VAR}", {})


def test_expand_env_vars_lists_all_missing():
    with pytest.raises(ConfigError) as exc:
        _expand_env_vars("a=${A} b=${B}", {})
    assert 'A' in str(exc.value) and 'B' in str(exc.value)


def test_expand_env_vars_no_placeholder_unchanged():
    assert _expand_env_vars("plain text", {}) == "plain text"


# --- loading the actual YAML configs ---

def test_load_test_yaml():
    cfg = load_config(TEST_YAML, env=FAKE_ENV, load_dotenv_file=False)
    assert isinstance(cfg, BotConfig)
    assert cfg.mode == Mode.TEST
    assert cfg.dry_run is True
    assert cfg.cex.testnet is True
    assert cfg.cex.api_key.get_secret_value() == 'test_key'  # pragma: allowlist secret
    assert {c.chain_id for c in cfg.chains} == {ChainId.ETH_MAINNET}
    assert any(p.pair == 'ETH/USDT' for p in cfg.pairs)
    # Every pair must have a pool_address — required for PricingEngine.load_pools().
    assert all(p.pool_address for p in cfg.pairs)


def test_load_prod_yaml():
    cfg = load_config(PROD_YAML, env=FAKE_ENV, load_dotenv_file=False)
    assert cfg.mode == Mode.PROD
    assert cfg.cex.testnet is False
    assert cfg.cex.api_key.get_secret_value() == 'prod_key'  # pragma: allowlist secret
    assert cfg.executor.use_flashbots is True
    assert cfg.signal.min_spread_bps == Decimal('65')


def test_load_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config('/nonexistent/path.yaml', env={}, load_dotenv_file=False)


def test_load_missing_env_var():
    incomplete = {k: v for k, v in FAKE_ENV.items() if k != 'BINANCE_TESTNET_API_KEY'}
    with pytest.raises(ConfigError, match="BINANCE_TESTNET_API_KEY"):
        load_config(TEST_YAML, env=incomplete, load_dotenv_file=False)


# --- BotConfig validation ---

def _minimal_bot_dict(**overrides) -> dict:
    base = {
        'mode': 'test',
        'cex': {'api_key': 'k', 'secret': 's'},
        'chains': [{'chain_id': 1, 'rpc_url': 'https://x'}],
        'pairs': [{'pair': 'ETH/USDC', 'chain_id': 1, 'trade_size': '0.1'}],
    }
    base.update(overrides)
    return base


def test_minimal_config_loads():
    cfg = load_config_from_dict(_minimal_bot_dict())
    assert cfg.mode == Mode.TEST
    assert cfg.dry_run is True


def test_pair_with_unknown_chain_rejected():
    bad = _minimal_bot_dict(
        pairs=[{'pair': 'ETH/USDC', 'chain_id': 42161, 'trade_size': '0.1'}]
    )
    with pytest.raises(ValidationError, match="not in chains list"):
        load_config_from_dict(bad)


def test_duplicate_chain_ids_rejected():
    bad = _minimal_bot_dict(chains=[
        {'chain_id': 1, 'rpc_url': 'https://x'},
        {'chain_id': 1, 'rpc_url': 'https://y'},
    ])
    with pytest.raises(ValidationError, match="duplicate chain_id"):
        load_config_from_dict(bad)


def test_prod_without_wallet_rejected():
    bad = _minimal_bot_dict(mode='prod')
    with pytest.raises(ValidationError, match="wallet is required in PROD"):
        load_config_from_dict(bad)


def test_prod_with_wallet_ok():
    cfg = load_config_from_dict(_minimal_bot_dict(
        mode='prod',
        wallet={'private_key': VALID_PRIVATE_KEY},
    ))
    assert cfg.wallet is not None


def test_invalid_private_key_rejected():
    with pytest.raises(ValidationError, match="32 bytes"):
        WalletConfig(private_key='0x1234')  # pragma: allowlist secret


def test_secrets_masked_in_repr_and_dump():
    """Regression guard: secrets must never appear in repr / model_dump_json /
    error tracebacks. Read them only via .get_secret_value()."""
    cfg = load_config_from_dict(_minimal_bot_dict(
        wallet={'private_key': VALID_PRIVATE_KEY},
        cex={'api_key': 'real_key', 'secret': 'real_secret'},  # pragma: allowlist secret
    ))
    rep = repr(cfg)
    js = cfg.model_dump_json()
    leaks = ['real_key', 'real_secret', VALID_PRIVATE_KEY.removeprefix('0x'),
             '11111111111111111111111111111111']
    for needle in leaks:
        assert needle not in rep, f"secret leaked into repr: {needle}"
        assert needle not in js, f"secret leaked into model_dump_json: {needle}"
    # And we can still read them when actually needed:
    assert cfg.cex.secret.get_secret_value() == 'real_secret'  # pragma: allowlist secret
    assert cfg.wallet.private_key.get_secret_value() == VALID_PRIVATE_KEY


def test_pair_format_validation():
    with pytest.raises(ValidationError, match="BASE/QUOTE"):
        PairConfig(pair='ETHUSDC', chain_id=ChainId.ETH_MAINNET, trade_size=Decimal('0.1'))


def test_negative_fee_rejected():
    with pytest.raises(ValidationError, match="non-negative"):
        FeesConfig(cex_taker_bps=Decimal('-1'))


def test_extra_keys_rejected():
    bad = _minimal_bot_dict(unknown_field=123)
    with pytest.raises(ValidationError):
        load_config_from_dict(bad)


def test_get_chain_helper():
    cfg = load_config_from_dict(_minimal_bot_dict())
    chain = cfg.get_chain(ChainId.ETH_MAINNET)
    assert chain.rpc_url == 'https://x'
    with pytest.raises(KeyError):
        cfg.get_chain(ChainId.ARBITRUM)


# --- tokens registry ---

def test_eth_mainnet_tokens_present():
    eth = get_token(ChainId.ETH_MAINNET, 'ETH')
    weth = get_token(ChainId.ETH_MAINNET, 'WETH')
    assert eth == weth
    assert weth.decimals == 18
    assert get_token(ChainId.ETH_MAINNET, 'usdc').decimals == 6


def test_arbitrum_tokens_present():
    weth = get_token(ChainId.ARBITRUM, 'WETH')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')
    usdc_e = get_token(ChainId.ARBITRUM, 'USDC.e')
    assert weth.decimals == 18
    assert usdc != usdc_e


def test_unknown_token_raises():
    with pytest.raises(KeyError):
        get_token(ChainId.ETH_MAINNET, 'FOOBAR')


def test_router_and_factory_per_chain():
    r1 = get_router(ChainId.ETH_MAINNET)
    r2 = get_router(ChainId.ARBITRUM)
    assert r1 != r2
    f1 = get_factory(ChainId.ETH_MAINNET)
    f2 = get_factory(ChainId.ARBITRUM)
    assert f1 != f2


def test_tokens_registry_covers_both_chains():
    assert ChainId.ETH_MAINNET in TOKENS
    assert ChainId.ARBITRUM in TOKENS


# --- RiskLimitsConfig ---

def test_risk_limits_defaults_within_absolutes():
    rl = RiskLimitsConfig()
    assert rl.max_trade_usd <= ABSOLUTE_MAX_TRADE_USD
    assert rl.max_daily_loss <= ABSOLUTE_MAX_DAILY_LOSS
    assert rl.initial_capital_usd >= ABSOLUTE_MIN_CAPITAL


def test_risk_limits_max_trade_above_absolute_rejected():
    with pytest.raises(ValidationError, match="ABSOLUTE_MAX_TRADE_USD"):
        RiskLimitsConfig(max_trade_usd=ABSOLUTE_MAX_TRADE_USD + Decimal('1'))


def test_risk_limits_daily_loss_above_absolute_rejected():
    with pytest.raises(ValidationError, match="ABSOLUTE_MAX_DAILY_LOSS"):
        RiskLimitsConfig(max_daily_loss=ABSOLUTE_MAX_DAILY_LOSS + Decimal('1'))


def test_risk_limits_capital_below_absolute_rejected():
    with pytest.raises(ValidationError, match="ABSOLUTE_MIN_CAPITAL"):
        RiskLimitsConfig(initial_capital_usd=ABSOLUTE_MIN_CAPITAL - Decimal('1'))


def test_risk_limits_to_runtime_round_trips():
    rl = RiskLimitsConfig(max_trade_usd=Decimal('15'), consecutive_loss_limit=2)
    runtime = rl.to_runtime()
    assert isinstance(runtime, RuntimeRiskLimits)
    assert runtime.max_trade_usd == Decimal('15')
    assert runtime.consecutive_loss_limit == 2


def test_test_yaml_has_risk_block():
    cfg = load_config(TEST_YAML, env=FAKE_ENV, load_dotenv_file=False)
    assert cfg.risk.max_trade_usd == Decimal('20')
    assert cfg.risk.initial_capital_usd == Decimal('100')


def test_prod_yaml_has_conservative_risk():
    cfg = load_config(PROD_YAML, env=FAKE_ENV, load_dotenv_file=False)
    # prod must be no looser than test
    assert cfg.risk.max_trade_usd <= Decimal('5')
    assert cfg.risk.max_daily_loss <= Decimal('10')
