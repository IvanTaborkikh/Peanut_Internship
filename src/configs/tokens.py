"""Per-chain token registry and DEX router/factory addresses.

Addresses are checksummed via core.types.Address validation.
'ETH' is registered as an alias for WETH on each chain (CEX uses ETH symbol,
DEX swaps the wrapped form).
"""
from src.configs.schema import ChainId
from src.core.types import Address, Token


_ETH_MAINNET_TOKENS: dict[str, Token] = {
    'WETH': Token(Address('0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'), 'WETH', 18),
    'USDT': Token(Address('0xdAC17F958D2ee523a2206206994597C13D831ec7'), 'USDT', 6),
    'USDC': Token(Address('0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'), 'USDC', 6),
    'WBTC': Token(Address('0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599'), 'WBTC', 8),
    'DAI':  Token(Address('0x6B175474E89094C44Da98b954EedeAC495271d0F'), 'DAI', 18),
}
_ETH_MAINNET_TOKENS['ETH'] = _ETH_MAINNET_TOKENS['WETH']


_ARBITRUM_TOKENS: dict[str, Token] = {
    'WETH':   Token(Address('0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'), 'WETH', 18),
    'USDT':   Token(Address('0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9'), 'USDT', 6),
    'USDC':   Token(Address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831'), 'USDC', 6),
    'USDC.E': Token(Address('0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8'), 'USDC.e', 6),
    'WBTC':   Token(Address('0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f'), 'WBTC', 8),
}
_ARBITRUM_TOKENS['ETH'] = _ARBITRUM_TOKENS['WETH']


TOKENS: dict[ChainId, dict[str, Token]] = {
    ChainId.ETH_MAINNET: _ETH_MAINNET_TOKENS,
    ChainId.ARBITRUM:    _ARBITRUM_TOKENS,
}


# Uniswap V2 deployment addresses (router02 + factory).
# Arbitrum uses Uniswap V2 deployed at the addresses below; SushiSwap and
# Camelot have separate deployments — add when needed.
UNISWAP_V2_ROUTERS: dict[ChainId, Address] = {
    ChainId.ETH_MAINNET: Address('0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D'),
    ChainId.ARBITRUM:    Address('0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24'),
}

UNISWAP_V2_FACTORIES: dict[ChainId, Address] = {
    ChainId.ETH_MAINNET: Address('0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f'),
    ChainId.ARBITRUM:    Address('0xf1D7CC64Fb4452F05c498126312eBE29f30Fbcf9'),
}


def get_token(chain_id: ChainId, symbol: str) -> Token:
    chain_tokens = TOKENS.get(chain_id)
    if chain_tokens is None:
        raise KeyError(f"no token registry for chain_id {chain_id}")
    sym = symbol.upper()
    token = chain_tokens.get(sym)
    if token is None:
        raise KeyError(f"token {symbol!r} not registered on chain {chain_id.name}")
    return token


def get_router(chain_id: ChainId) -> Address:
    router = UNISWAP_V2_ROUTERS.get(chain_id)
    if router is None:
        raise KeyError(f"no Uniswap V2 router configured for chain {chain_id.name}")
    return router


def get_factory(chain_id: ChainId) -> Address:
    factory = UNISWAP_V2_FACTORIES.get(chain_id)
    if factory is None:
        raise KeyError(f"no Uniswap V2 factory configured for chain {chain_id.name}")
    return factory
