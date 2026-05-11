"""Dry-simulate what the bot would send for both arb directions, given
current real balances and live CHIP price. Does NOT broadcast anything.

Use this before restarting the bot after a code change to verify the
amount math is sane:

    python scripts/dry_simulate_signal.py
"""
import os
import sys
from decimal import Decimal

import ccxt
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

load_dotenv()

USDC = Web3.to_checksum_address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831')
CHIP = Web3.to_checksum_address('0x0c1C1C109fE34733FCa54B82D7B46b75CFB71F6E')
SWAP_ROUTER_02 = Web3.to_checksum_address('0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45')

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address", "name": "owner"}], "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint256"}]},
]

TRADE_SIZE_CHIP = Decimal('200')   # from configs/chip_observe.yaml (Day 4)
SLIPPAGE_BPS = Decimal('100')      # bumped from 50 after Bug #8 (CHIP pool slippage)


def main() -> int:
    pk = os.getenv('PRIVATE_KEY')
    rpc = os.getenv('ARBITRUM_RPC_URL')
    api = os.getenv('BINANCE_API_KEY')
    sec = os.getenv('BINANCE_SECRET')

    w3 = Web3(Web3.HTTPProvider(rpc))
    account = Account.from_key(pk)

    ex = ccxt.binance({'apiKey': api, 'secret': sec, 'enableRateLimit': True})
    ob = ex.fetch_order_book('CHIP/USDC', limit=5)
    cex_bid = Decimal(str(ob['bids'][0][0]))
    cex_ask = Decimal(str(ob['asks'][0][0]))
    cex_mid = (cex_bid + cex_ask) / 2

    # Use mid as proxy for dex_price for the simulation. Real bot uses V3 quoter.
    dex_price = cex_mid

    bal = ex.fetch_balance()
    binance_usdc = Decimal(str(bal.get('USDC', {}).get('free', 0)))
    binance_chip = Decimal(str(bal.get('CHIP', {}).get('free', 0)))

    usdc_c = w3.eth.contract(address=USDC, abi=ERC20_ABI)
    chip_c = w3.eth.contract(address=CHIP, abi=ERC20_ABI)
    wallet_usdc = Decimal(str(usdc_c.functions.balanceOf(account.address).call())) / Decimal('1000000')
    wallet_chip = Decimal(str(chip_c.functions.balanceOf(account.address).call())) / Decimal('1000000000000000000')
    wallet_eth = Decimal(str(w3.eth.get_balance(account.address))) / Decimal('1000000000000000000')
    usdc_allow = Decimal(str(usdc_c.functions.allowance(account.address, SWAP_ROUTER_02).call()))
    chip_allow = Decimal(str(chip_c.functions.allowance(account.address, SWAP_ROUTER_02).call()))

    print("=== Live state ===")
    print(f"  CHIP price (Binance mid): ${cex_mid:.5f}")
    print(f"  Binance USDC: {binance_usdc:.4f}")
    print(f"  Binance CHIP: {binance_chip:.4f}")
    print(f"  Wallet USDC:  {wallet_usdc:.4f}  allowance={'ok' if usdc_allow > Decimal('1e30') else 'NOT APPROVED'}")
    print(f"  Wallet CHIP:  {wallet_chip:.4f}  allowance={'ok' if chip_allow > Decimal('1e30') else 'NOT APPROVED'}")
    print(f"  Wallet ETH:   {wallet_eth:.6f}  ({'OK gas' if wallet_eth > Decimal('0.0002') else 'LOW GAS!'})")
    print()

    slip = Decimal('1') - SLIPPAGE_BPS / Decimal('10000')
    notional_usd = TRADE_SIZE_CHIP * cex_mid

    print(f"=== Simulated signal (size={TRADE_SIZE_CHIP} CHIP, notional=${notional_usd:.2f}) ===")
    print()

    # ---------- Direction A: BUY_CEX_SELL_DEX ----------
    print("Direction A: BUY_CEX_SELL_DEX")
    print(f"  CEX leg: BUY {TRADE_SIZE_CHIP} CHIP on Binance")
    print(f"    Need USDC: ~{notional_usd:.4f}  → have {binance_usdc:.4f} {'✓' if binance_usdc >= notional_usd else '✗ INSUFFICIENT'}")
    a_amount_in = TRADE_SIZE_CHIP
    a_amount_out_min = TRADE_SIZE_CHIP * dex_price * slip
    print(f"  DEX leg: SELL {TRADE_SIZE_CHIP} CHIP for USDC on Uniswap V3")
    print(f"    amount_in:      {a_amount_in:.4f} CHIP")
    print(f"    amount_out_min: {a_amount_out_min:.4f} USDC ({SLIPPAGE_BPS} bps slip)")
    print(f"    Need wallet CHIP: {a_amount_in:.4f}  → have {wallet_chip:.4f} {'✓' if wallet_chip >= a_amount_in else '✗ INSUFFICIENT'}")
    print()

    # ---------- Direction B: BUY_DEX_SELL_CEX ----------
    print("Direction B: BUY_DEX_SELL_CEX")
    print(f"  CEX leg: SELL {TRADE_SIZE_CHIP} CHIP on Binance")
    print(f"    Need CHIP: {TRADE_SIZE_CHIP}  → have {binance_chip:.4f} {'✓' if binance_chip >= TRADE_SIZE_CHIP else '✗ INSUFFICIENT'}")
    b_amount_in = TRADE_SIZE_CHIP * dex_price       # USDC to spend
    b_amount_out_min = TRADE_SIZE_CHIP * slip       # Min CHIP to receive
    print(f"  DEX leg: BUY {TRADE_SIZE_CHIP} CHIP with USDC on Uniswap V3")
    print(f"    amount_in:      {b_amount_in:.4f} USDC")
    print(f"    amount_out_min: {b_amount_out_min:.4f} CHIP ({SLIPPAGE_BPS} bps slip)")
    print(f"    Need wallet USDC: {b_amount_in:.4f}  → have {wallet_usdc:.4f} {'✓' if wallet_usdc >= b_amount_in else '✗ INSUFFICIENT'}")
    print()

    print("If both directions show ✓ for all 4 inventory checks, bot can execute either direction safely.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
