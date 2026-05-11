"""Pre-flight balance check.

Prints current Binance Spot balances (USDC, CHIP) and Arbitrum wallet
balances (USDC, CHIP, ETH for gas) before the bot goes live. Use this to
confirm the funding flow completed.

    python scripts/check_balances.py
"""
import os
import sys

import ccxt
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

load_dotenv()

USDC_ARB = Web3.to_checksum_address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831')
CHIP_ARB = Web3.to_checksum_address('0x0c1C1C109fE34733FCa54B82D7B46b75CFB71F6E')
SWAP_ROUTER_02 = Web3.to_checksum_address('0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45')

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address", "name": "owner"}], "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint256"}]},
]


def check_binance() -> None:
    api_key = os.getenv('BINANCE_API_KEY')
    secret = os.getenv('BINANCE_SECRET')
    if not api_key or not secret:
        print("Binance: skipped (BINANCE_API_KEY/SECRET not set)")
        return
    print("\n=== Binance Spot ===")
    ex = ccxt.binance({'apiKey': api_key, 'secret': secret, 'enableRateLimit': True})
    bal = ex.fetch_balance()
    for sym in ('USDC', 'USDT', 'CHIP', 'ETH'):
        free = bal.get(sym, {}).get('free', 0)
        total = bal.get(sym, {}).get('total', 0)
        print(f"  {sym:<5}  free={float(free):>12.6f}  total={float(total):>12.6f}")


def check_arbitrum() -> None:
    pk = os.getenv('PRIVATE_KEY')
    rpc = os.getenv('ARBITRUM_RPC_URL')
    if not pk or not rpc:
        print("Arbitrum: skipped (PRIVATE_KEY/ARBITRUM_RPC_URL not set)")
        return
    w3 = Web3(Web3.HTTPProvider(rpc))
    account = Account.from_key(pk)
    print(f"\n=== Arbitrum wallet {account.address} ===")
    eth_wei = w3.eth.get_balance(account.address)
    print(f"  ETH    balance={eth_wei / 1e18:>12.6f}  (gas reserve)")
    for addr in (USDC_ARB, CHIP_ARB):
        token = w3.eth.contract(address=addr, abi=ERC20_ABI)
        symbol = token.functions.symbol().call()
        decimals = token.functions.decimals().call()
        raw = token.functions.balanceOf(account.address).call()
        allowance = token.functions.allowance(account.address, SWAP_ROUTER_02).call()
        approved = "✓" if allowance > 10**30 else "✗ NOT APPROVED"
        print(f"  {symbol:<5}  balance={raw / 10**decimals:>12.6f}  router_allowance={approved}")


def main() -> int:
    check_binance()
    check_arbitrum()
    print("\nReady to trade if both venues show ~$25 USDC + ~$25 CHIP and Arbitrum approvals are ✓.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
