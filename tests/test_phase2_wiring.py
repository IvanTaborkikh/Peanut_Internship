"""Phase 2 wiring tests: factory methods, generator chain selection, fee plumbing."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.configs.schema import ChainId
from src.configs.loader import load_config_from_dict
from src.executor.engine import Executor, ExecutorConfig
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator


VALID_PRIVATE_KEY = '0x' + '1' * 64  # pragma: allowlist secret


def _bot_dict(**overrides):
    base = {
        'mode': 'test',
        'cex': {'api_key': 'k', 'secret': 's'},  # pragma: allowlist secret
        'wallet': {'private_key': VALID_PRIVATE_KEY},
        'chains': [
            {'chain_id': 1,     'rpc_url': 'https://eth.example'},
            {'chain_id': 42161, 'rpc_url': 'https://arb.example'},
        ],
        'pairs': [
            {'pair': 'ETH/USDC', 'chain_id': 1,     'trade_size': '0.1'},
        ],
    }
    base.update(overrides)
    return base


# --- generator chain_id selection ---

def test_generator_uses_arbitrum_tokens_by_default():
    """Backward-compat: existing tests construct generator without chain_id."""
    gen = SignalGenerator(
        exchange_client=None, pricing_module=None, inventory_tracker=None,
        fee_structure=FeeStructure(), config={},
    )
    weth, usdt = gen._pair_to_tokens('ETH/USDT')
    # Arbitrum WETH address starts with 0x82aF49...
    assert weth.address.checksum.startswith('0x82aF49')
    assert usdt.symbol == 'USDT'


def test_generator_uses_eth_mainnet_tokens_when_chain_id_set():
    gen = SignalGenerator(
        exchange_client=None, pricing_module=None, inventory_tracker=None,
        fee_structure=FeeStructure(), config={},
        chain_id=ChainId.ETH_MAINNET,
    )
    weth, usdc = gen._pair_to_tokens('ETH/USDC')
    # Mainnet WETH starts with 0xC02aaA...
    assert weth.address.checksum.startswith('0xC02aaA')
    assert usdc.symbol == 'USDC'


def test_generator_unknown_token_raises():
    gen = SignalGenerator(
        exchange_client=None, pricing_module=None, inventory_tracker=None,
        fee_structure=FeeStructure(), config={},
        chain_id=ChainId.ETH_MAINNET,
    )
    with pytest.raises(KeyError):
        gen._pair_to_tokens('FOOBAR/USDC')


# --- ExecutorConfig fee plumbing ---

def test_executor_config_default_fees_match_legacy_constants():
    """The defaults must equal the previous hardcodes (10 bps CEX, 30 bps DEX)."""
    cfg = ExecutorConfig()
    assert cfg.cex_taker_bps == Decimal('10')
    assert cfg.dex_swap_bps == Decimal('30')


def test_executor_config_overridden_fees_used_in_pnl():
    """Custom fee bps must flow through _calculate_pnl."""
    cfg = ExecutorConfig(cex_taker_bps=Decimal('5'), dex_swap_bps=Decimal('25'),
                         gas_cost_usd=Decimal('0'))
    ex = Executor(exchange_client=None, pricing_module=None, inventory_tracker=None,
                  config=cfg)

    from types import SimpleNamespace
    from src.strategy.signal import Direction
    ctx = SimpleNamespace(
        signal=SimpleNamespace(direction=Direction.BUY_CEX_SELL_DEX),
        leg1_venue='cex', leg2_venue='dex',
        leg1_fill_price=Decimal('2000'), leg2_fill_price=Decimal('2010'),
        leg1_fill_size=Decimal('1'),     leg2_fill_size=Decimal('1'),
    )
    pnl = ex._calculate_pnl(ctx)
    # gross = 2010 - 2000 = 10
    # cex_fee = 1 * 2000 * 0.0005 = 1.0
    # dex_fee = 1 * 2010 * 0.0025 = 5.025
    # pnl = 10 - 1 - 5.025 - 0 = 3.975
    assert pnl == Decimal('3.975')


# --- ExchangeClient.from_config ---

def test_exchange_client_from_config_unwraps_secrets():
    """from_config must unwrap SecretStr → str when handing off to ccxt."""
    cfg = load_config_from_dict(_bot_dict())
    captured = {}

    class FakeClient:
        def __init__(self, raw_config):
            captured.update(raw_config)

    with patch('src.exchange.client.ExchangeClient.__init__', FakeClient.__init__):
        from src.exchange.client import ExchangeClient
        # The patched __init__ replaces real init so no network call.
        ExchangeClient.from_config(cfg.cex)

    assert captured['apiKey'] == 'k'         # pragma: allowlist secret
    assert captured['secret'] == 's'         # pragma: allowlist secret
    assert captured['sandbox'] is True


# --- ArbBot.from_config (translation only — not running the bot) ---

def test_arb_bot_from_config_translates_dict():
    """from_config must produce the dict the legacy __init__ expects, with
    secrets unwrapped and signal config flattened."""
    cfg = load_config_from_dict(_bot_dict(
        executor={'use_flashbots': True},
        signal={'min_profit_usd': '7', 'cooldown_seconds': 3.5},
    ))
    captured = {}

    # Patch ArbBot.__init__ to just capture the dict, not run anything.
    from scripts import arb_bot as ab_mod

    def fake_init(self, config):
        captured.update(config)

    with patch.object(ab_mod.ArbBot, '__init__', fake_init):
        ab_mod.ArbBot.from_config(cfg)

    assert captured['apiKey'] == 'k'                       # pragma: allowlist secret
    assert captured['secret'] == 's'                       # pragma: allowlist secret
    assert captured['sandbox'] is True
    assert captured['rpc_url'] == 'https://eth.example'
    assert captured['pairs'] == ['ETH/USDC']
    assert captured['use_flashbots'] is True
    assert captured['signal_config']['min_profit_usd'] == '7'
    assert captured['signal_config']['cooldown_seconds'] == 3.5


# --- Sanity: no _ARBITRUM_TOKENS leftover in generator ---

def test_no_hardcoded_arbitrum_tokens_in_generator():
    """Regression guard: the inline _ARBITRUM_TOKENS dict is gone."""
    import src.strategy.generator as g
    assert not hasattr(g, '_ARBITRUM_TOKENS')
