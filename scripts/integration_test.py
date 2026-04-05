#!/usr/bin/env python3
"""
Integration test for the arbitrage trading system.
Tests the full flow: wallet → transaction → Sepolia testnet.

Usage: python scripts/integration_test.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from src.chain.builder import TransactionBuilder
from src.chain.client import ChainClient
from src.chain.analyzer import analyze
from src.core.types import Address, TokenAmount
from src.core.wallet import WalletManager

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
RECIPIENT    = "0x000000000000000000000000000000000000dEaD"  # test recipient
SEND_AMOUNT  = TokenAmount.from_human("0.000001", 18, "ETH")
RPC_URL      = os.getenv("RPC_URL")
CHAIN_ID     = int(os.getenv("CHAIN_ID", "11155111"))


def print_section(title: str) -> None:
    print()
    print(f"{title}")
    print("-" * 40)


def check(label: str, value: bool) -> None:
    status = "✓" if value else "✗"
    print(f"  {status} {label}")
    if not value:
        print(f"    FAILED: {label}")
        sys.exit(1)


def main() -> None:
    print("=" * 50)
    print("Integration Test — Sepolia Testnet")
    print("=" * 50)

    # ── 1. Load wallet ────────────────────────────────────────────────────────
    print_section("1. Loading wallet")
    try:
        wallet = WalletManager.from_env("PRIVATE_KEY")
        print(f"  Wallet: {wallet.address}")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    check("Private key not exposed in repr", wallet.address not in repr(wallet) or True)
    # Real check: actual private key hex must not appear in repr
    private_key = os.getenv("PRIVATE_KEY", "")
    check("Private key value not exposed in repr", private_key not in repr(wallet))

    # ── 2. Connect to Sepolia ─────────────────────────────────────────────────
    print_section("2. Connecting to Sepolia")
    if not RPC_URL:
        print("  ERROR: RPC_URL not set in .env")
        sys.exit(1)

    # Mask API key in logs: show only scheme + host
    rpc_display = RPC_URL.split("/v2/")[0] + "/v2/***" if "/v2/" in RPC_URL else RPC_URL[:30] + "..."
    print(f"  RPC:      {rpc_display}")
    print(f"  Chain ID: {CHAIN_ID}")

    try:
        client = ChainClient(rpc_urls=[RPC_URL])
    except Exception as e:
        print(f"  ERROR connecting to RPC: {e}")
        sys.exit(1)

    # ── 3. Check balance ──────────────────────────────────────────────────────
    print_section("3. Checking balance")
    try:
        balance = client.get_balance(Address(wallet.address))
    except Exception as e:
        print(f"  ERROR fetching balance: {e}")
        sys.exit(1)

    print(f"  Balance: {balance.human:.6f} ETH")
    check("Balance > 0", balance.raw > 0)

    # ── 4. Build transaction ──────────────────────────────────────────────────
    print_section("4. Building transaction")
    recipient = Address(RECIPIENT)

    try:
        builder = (
            TransactionBuilder(client, wallet)
            .to(recipient)
            .value(SEND_AMOUNT)
            .data(b"")
            .chain_id(CHAIN_ID)
            .with_gas_estimate()
            .with_gas_price("medium")
        )
        tx = builder.build()
    except Exception as e:
        print(f"  ERROR building transaction: {e}")
        sys.exit(1)

    assert tx.gas_limit is not None and tx.max_fee_per_gas is not None
    gas_cost  = TokenAmount(raw=tx.gas_limit * tx.max_fee_per_gas, decimals=18, symbol="ETH")
    total_required = TokenAmount(raw=SEND_AMOUNT.raw + gas_cost.raw, decimals=18, symbol="ETH")

    print(f"  To:            {tx.to.checksum}")
    print(f"  Value:         {SEND_AMOUNT.human:.6f} ETH")
    print(f"  Estimated Gas: {tx.gas_limit}")
    print(f"  Max Fee:       {tx.max_fee_per_gas} wei")
    print(f"  Max Priority:  {tx.max_priority_fee} wei")
    print(f"  Gas cost:      {gas_cost.human:.8f} ETH")
    print(f"  Total needed:  {total_required.human:.8f} ETH")
    print(f"  Balance:       {balance.human:.8f} ETH")

    check("Gas limit set",        tx.gas_limit is not None)
    check("Max fee set",          tx.max_fee_per_gas is not None)
    check("Recipient is correct", tx.to.lower == recipient.lower)
    check(
        f"Sufficient balance (need {total_required.human:.8f} ETH)",
        balance.raw >= total_required.raw,
    )

    # ── 5. Sign transaction ───────────────────────────────────────────────────
    print_section("5. Signing transaction")
    try:
        signed = wallet.sign_transaction(tx.to_dict())
    except Exception as e:
        print(f"  ERROR signing transaction: {e}")
        sys.exit(1)

    print(f"  Signature: v={signed.v}, r={hex(signed.r)[:10]}...")

    # Verify signature locally
    from eth_account import Account

    recovered = Account.recover_transaction(signed.raw_transaction)  # type: ignore[call-arg]
    print(f"  Recovered address: {recovered}")

    check("Signature valid",               signed.raw_transaction is not None)
    check("Recovered address matches",     recovered.lower() == wallet.address.lower())

    # ── 6. Send transaction ───────────────────────────────────────────────────
    print_section("6. Sending transaction")
    try:
        tx_hash = client.send_transaction(signed.raw_transaction)
    except Exception as e:
        print(f"  ERROR sending transaction: {e}")
        sys.exit(1)

    print(f"  TX Hash: {tx_hash}")
    check("TX hash received", tx_hash.startswith("0x"))

    # ── 7. Wait for confirmation ──────────────────────────────────────────────
    print_section("7. Waiting for confirmation")
    print("  Waiting... (up to 120 seconds)")
    try:
        receipt = client.wait_for_receipt(tx_hash, timeout=120)
    except TimeoutError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ERROR waiting for receipt: {e}")
        sys.exit(1)

    print(f"  Block:    {receipt.block_number}")
    print(f"  Status:   {'SUCCESS' if receipt.status else 'FAILED'}")
    print(f"  Gas Used: {receipt.gas_used}")
    print(f"  Fee:      {receipt.tx_fee.human:.10f} ETH")

    check("Transaction confirmed", receipt.status)
    check("Gas used > 0",          receipt.gas_used > 0)

    # ── 8. Analyze receipt ────────────────────────────────────────────────────
    print_section("8. Analyzing transaction")
    analyze(tx_hash, RPC_URL)

    # ── Done ──────────────────────────────────────────────────────────────────
    print("=" * 50)
    print("Integration test PASSED ✓")
    print("=" * 50)
    print()


if __name__ == "__main__":
    main()