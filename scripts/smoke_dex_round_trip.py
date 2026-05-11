"""End-to-end smoke test of the bot's DEX swap path.

Sends one tiny real swap (5 CHIP → USDC) using the same TxBuilder code path
the bot uses for live arbitrage. If this succeeds, every DEX-side fix
(Bug #1, #6, #7, #8) is verified together against the real chain. Cost:
~$0.05 in gas + ~5-10 bps slippage on $0.32 trade ≈ $0.06 worst case.

Usage:
    python scripts/smoke_dex_round_trip.py             # sell 5 CHIP for USDC
    python scripts/smoke_dex_round_trip.py --buy        # buy ~5 CHIP with USDC
    python scripts/smoke_dex_round_trip.py --dry        # build+sign, do NOT broadcast

Always asks for confirmation before broadcasting (unless --dry).
"""
import argparse
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

# Ensure repo root is on sys.path so `src.*` imports work when run as
# `python scripts/smoke_dex_round_trip.py` without a PYTHONPATH= prefix.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound

from src.configs.schema import ChainId
from src.configs.tokens import get_token, get_v3_router
from src.executor.tx_builder import TxBuilder

load_dotenv()


ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address", "name": "owner"}], "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint256"}]},
]

SUFFICIENT_ALLOWANCE = 10**30
MIN_ETH_FOR_GAS = Decimal('0.0003')  # ~$1 on Arbitrum at 3500 USD/ETH


def _balance(w3: Web3, token_addr: str, owner: str) -> int:
    return w3.eth.contract(address=token_addr, abi=ERC20_ABI).functions.balanceOf(owner).call()


def _allowance(w3: Web3, token_addr: str, owner: str, spender: str) -> int:
    return w3.eth.contract(address=token_addr, abi=ERC20_ABI).functions.allowance(owner, spender).call()


def preflight(w3: Web3, account, token_in, amount_in_wei: int, router_addr: str) -> bool:
    """Fail-fast checks before we ask the user to confirm. Prints diagnostics."""
    print("Pre-flight checks:")
    ok = True

    eth_wei = w3.eth.get_balance(account.address)
    eth = Decimal(eth_wei) / Decimal(10**18)
    eth_ok = eth >= MIN_ETH_FOR_GAS
    print(f"  ETH for gas:  {eth:.6f} ETH  {'✓' if eth_ok else '✗ need ≥ ' + str(MIN_ETH_FOR_GAS)}")
    ok &= eth_ok

    bal = _balance(w3, token_in.address.checksum, account.address)
    bal_disp = Decimal(bal) / Decimal(10**token_in.decimals)
    need_disp = Decimal(amount_in_wei) / Decimal(10**token_in.decimals)
    bal_ok = bal >= amount_in_wei
    print(f"  {token_in.symbol} balance: {bal_disp:.6f}  need ≥ {need_disp:.6f}  {'✓' if bal_ok else '✗'}")
    ok &= bal_ok

    allow = _allowance(w3, token_in.address.checksum, account.address, router_addr)
    allow_ok = allow >= SUFFICIENT_ALLOWANCE
    print(f"  {token_in.symbol} → router allowance: {'✓ approved' if allow_ok else '✗ NOT APPROVED — run grant_v3_allowances.py'}")
    ok &= allow_ok

    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--buy', action='store_true', help='Buy CHIP with USDC instead of selling')
    parser.add_argument('--size', type=str, default='5', help='Trade size in CHIP units (default 5)')
    parser.add_argument('--slippage-bps', type=int, default=100, help='Slippage tolerance in bps (default 100)')
    parser.add_argument('--dry', action='store_true', help='Build + sign only — do not broadcast')
    args = parser.parse_args()

    pk = os.getenv('PRIVATE_KEY')
    rpc = os.getenv('ARBITRUM_RPC_URL')
    if not pk or not rpc:
        print("ERROR: PRIVATE_KEY or ARBITRUM_RPC_URL not set", file=sys.stderr)
        return 1

    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        print(f"ERROR: cannot connect to {rpc}", file=sys.stderr)
        return 1

    account = Account.from_key(pk)
    print(f"Wallet: {account.address}")

    # Same TxBuilder construction as ArbBot.__init__ — verifies the V3 path.
    builder = TxBuilder(
        web3=w3,
        chain_id=ChainId.ARBITRUM,
        account=account,
        dex_version='v3',
        fee_tier=100,
    )
    router_addr = get_v3_router(ChainId.ARBITRUM).checksum

    chip = get_token(ChainId.ARBITRUM, 'CHIP')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')

    # Binance mid for amount_out_min sizing. Tiny trade, so spot-CEX vs DEX
    # divergence is dominated by slippage_bps anyway.
    import ccxt
    ex = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_SECRET'),
        'enableRateLimit': True,
    })
    ob = ex.fetch_order_book('CHIP/USDC', limit=5)
    cex_mid = (Decimal(str(ob['bids'][0][0])) + Decimal(str(ob['asks'][0][0]))) / 2
    print(f"CHIP price (Binance mid): ${cex_mid:.5f}")

    size = Decimal(args.size)
    slip = Decimal('1') - Decimal(args.slippage_bps) / Decimal('10000')

    if args.buy:
        # Buy `size` CHIP with USDC: USDC → CHIP. amount_in fixed in USDC,
        # amount_out_min = `size` CHIP × slippage.
        token_in, token_out = usdc, chip
        amount_in_wei = int(size * cex_mid * (10 ** token_in.decimals))
        amount_out_min_wei = int(size * (10 ** token_out.decimals) * slip)
        action = f"BUY {size} CHIP with ~{size * cex_mid:.4f} USDC"
        in_disp = size * cex_mid
    else:
        # Sell `size` CHIP for USDC: CHIP → USDC.
        token_in, token_out = chip, usdc
        amount_in_wei = int(size * (10 ** token_in.decimals))
        amount_out_min_wei = int(size * cex_mid * (10 ** token_out.decimals) * slip)
        action = f"SELL {size} CHIP for ~{size * cex_mid:.4f} USDC"
        in_disp = size

    print(f"\nAction: {action}")
    print(f"  amount_in:      {amount_in_wei} wei ({in_disp:.6f} {token_in.symbol})")
    print(f"  amount_out_min: {amount_out_min_wei} wei (slippage {args.slippage_bps} bps)")
    print()

    if not preflight(w3, account, token_in, amount_in_wei, router_addr):
        print("\nPre-flight FAILED. Fix the ✗ items above and re-run.", file=sys.stderr)
        return 2
    print()

    # Snapshot balances pre-swap so we can confirm the swap actually moved tokens.
    bal_in_before = _balance(w3, token_in.address.checksum, account.address)
    bal_out_before = _balance(w3, token_out.address.checksum, account.address)
    eth_before = w3.eth.get_balance(account.address)

    print("Building signed tx via TxBuilder.build_dex_swap()...")
    try:
        prepared = builder.build_dex_swap(token_in, token_out, amount_in_wei, amount_out_min_wei)
    except Exception as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        return 3

    print(f"  predicted tx_hash: {prepared.tx_hash}")
    print(f"  gas: {prepared.gas}")
    print(f"  raw_tx length: {len(prepared.raw_tx)} chars")

    if args.dry:
        print("\n--dry: build+sign verified. NOT broadcasting.")
        print("✓ TxBuilder.build_dex_swap (V3) produced a valid signed tx.")
        return 0

    print()
    confirm = input("Type YES to broadcast (anything else aborts): ")
    if confirm.strip().upper() not in ('YES', 'Y'):
        print(f"Aborted. (got input: {confirm!r})")
        return 0

    print("\nBroadcasting...")
    try:
        raw_bytes = bytes.fromhex(prepared.raw_tx[2:] if prepared.raw_tx.startswith('0x') else prepared.raw_tx)
        tx_hash = w3.eth.send_raw_transaction(raw_bytes).hex()
        if not tx_hash.startswith('0x'):
            tx_hash = '0x' + tx_hash
    except Exception as e:
        print(f"BROADCAST FAILED: {e}", file=sys.stderr)
        return 4

    print(f"  tx_hash: {tx_hash}")
    print(f"  https://arbiscan.io/tx/{tx_hash}")

    print("\nWaiting for receipt (up to 60s)...")
    deadline = time.time() + 60
    receipt = None
    while time.time() < deadline:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                break
        except TransactionNotFound:
            pass
        time.sleep(1)

    if receipt is None:
        print("TIMEOUT waiting for receipt", file=sys.stderr)
        return 5
    if receipt.get('status') != 1:
        print(f"REVERTED in block {receipt.get('blockNumber')}", file=sys.stderr)
        return 6

    # Post-swap snapshot — confirms balances moved as expected and reports
    # the *actual* slippage we got vs the CEX mid we sized against.
    bal_in_after = _balance(w3, token_in.address.checksum, account.address)
    bal_out_after = _balance(w3, token_out.address.checksum, account.address)
    eth_after = w3.eth.get_balance(account.address)

    spent_wei = bal_in_before - bal_in_after
    received_wei = bal_out_after - bal_out_before
    spent = Decimal(spent_wei) / Decimal(10 ** token_in.decimals)
    received = Decimal(received_wei) / Decimal(10 ** token_out.decimals)

    eth_used = Decimal(eth_before - eth_after) / Decimal(10**18)

    print(f"\n✓ SUCCESS — confirmed in block {receipt.get('blockNumber')}")
    print(f"   gas used: {receipt.get('gasUsed')}")
    print(f"   effective gas price: {receipt.get('effectiveGasPrice')}")
    print(f"   ETH cost: {eth_used:.7f} ETH (~${eth_used * Decimal('3500'):.4f})")
    print()
    print(f"   {token_in.symbol} spent:    {spent:.6f}")
    print(f"   {token_out.symbol} received: {received:.6f}")

    # Effective price + slip vs CEX mid (in the direction of the trade).
    if args.buy:
        effective_price = spent / received if received > 0 else Decimal('0')
        slip_bps = (effective_price - cex_mid) / cex_mid * Decimal('10000') if cex_mid > 0 else Decimal('0')
        print(f"   effective price: {effective_price:.6f} USDC/CHIP  (cex_mid {cex_mid:.5f})")
        print(f"   slippage vs CEX mid: {slip_bps:+.1f} bps")
    else:
        effective_price = received / spent if spent > 0 else Decimal('0')
        slip_bps = (cex_mid - effective_price) / cex_mid * Decimal('10000') if cex_mid > 0 else Decimal('0')
        print(f"   effective price: {effective_price:.6f} USDC/CHIP  (cex_mid {cex_mid:.5f})")
        print(f"   slippage vs CEX mid: {slip_bps:+.1f} bps")

    print()
    print("DEX swap path is VERIFIED end-to-end. The bot's _execute_dex_leg")
    print("would behave identically for a real arbitrage signal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())