#!/usr/bin/env python3
"""
Transaction Analyzer CLI.
Usage: python -m chain.analyzer <tx_hash> [--rpc URL]
"""

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Optional

import requests
from web3 import Web3

from src.chain.client import ChainClient
from src.chain.errors import ChainError

# ── Known function signatures ─────────────────────────────────────────────────
KNOWN_FUNCTIONS = {
    # ERC-20
    "0xa9059cbb": "transfer(address,uint256)",
    "0x095ea7b3": "approve(address,uint256)",
    "0x23b872dd": "transferFrom(address,address,uint256)",
    # Uniswap V2
    "0x38ed1739": "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
    "0x7ff36ab5": "swapExactETHForTokens(uint256,address[],address,uint256)",
    "0x18cbafe5": "swapExactTokensForETH(uint256,uint256,address[],address,uint256)",
    "0xe8e33700": "addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256)",
    "0xf305d719": "addLiquidityETH(address,uint256,uint256,uint256,address,uint256)",
    "0xbaa2abde": "removeLiquidity(address,address,uint256,uint256,uint256,address,uint256)",
    # Uniswap V3
    "0xac9650d8": "multicall(bytes[])",
    "0xb858183f": "exactInput((bytes,address,uint256,uint256,uint256))",
    "0x04e45aaf": "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))",
    "0x09b81346": "exactOutput((bytes,address,uint256,uint256,uint256))",
}

# ABI definitions for argument decoding
FUNCTION_ABIS = {
    "0xa9059cbb": {
        "name": "transfer",
        "inputs": [("recipient", "address"), ("amount", "uint256")],
    },
    "0x095ea7b3": {
        "name": "approve",
        "inputs": [("spender", "address"), ("amount", "uint256")],
    },
    "0x23b872dd": {
        "name": "transferFrom",
        "inputs": [
            ("sender", "address"),
            ("recipient", "address"),
            ("amount", "uint256"),
        ],
    },
    "0x38ed1739": {
        "name": "swapExactTokensForTokens",
        "inputs": [
            ("amountIn", "uint256"),
            ("amountOutMin", "uint256"),
            ("path", "address[]"),
            ("to", "address"),
            ("deadline", "uint256"),
        ],
    },
    "0x7ff36ab5": {
        "name": "swapExactETHForTokens",
        "inputs": [
            ("amountOutMin", "uint256"),
            ("path", "address[]"),
            ("to", "address"),
            ("deadline", "uint256"),
        ],
    },
    "0x18cbafe5": {
        "name": "swapExactTokensForETH",
        "inputs": [
            ("amountIn", "uint256"),
            ("amountOutMin", "uint256"),
            ("path", "address[]"),
            ("to", "address"),
            ("deadline", "uint256"),
        ],
    },
}

# Swap selectors for Swap Summary detection
SWAP_SELECTORS = {"0x38ed1739", "0x7ff36ab5", "0x18cbafe5", "0x04e45aaf", "0xb858183f"}

# Event topics
TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef" # pragma: allowlist secret
SWAP_TOPIC_V2 = "d78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"  # pragma: allowlist secret
SYNC_TOPIC = "1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"     # pragma: allowlist secret


# ── Helpers ───────────────────────────────────────────────────────────────────


def _wei_to_gwei(wei: int) -> Decimal:
    return Decimal(wei) / Decimal(10**9)


def _wei_to_eth(wei: int) -> Decimal:
    return Decimal(wei) / Decimal(10**18)


def _format_number(n: int) -> str:
    return f"{n:,}"


def _normalize_calldata(data) -> str:
    """Normalize calldata to 0x-prefixed hex string."""
    if not data:
        return "0x"
    if isinstance(data, bytes):
        return "0x" + data.hex()
    if not str(data).startswith("0x"):
        return "0x" + str(data)
    return str(data)


def _decode_selector(data: str) -> tuple[str, str]:
    """Returns (selector_hex, function_name)."""
    if not data or data == "0x" or len(data) < 10:
        return "0x", "ETH Transfer (no calldata)"
    selector = data[:10].lower()
    name = KNOWN_FUNCTIONS.get(selector, "Unknown function")
    return selector, name


def _decode_arguments(
    data: str, selector: str, w3: Web3
) -> Optional[list[tuple[str, str]]]:
    """Decode function arguments. Returns list of (name, value) or None."""
    if selector not in FUNCTION_ABIS:
        return None
    abi_info = FUNCTION_ABIS[selector]
    inputs = abi_info["inputs"]
    try:
        abi = [
            {
                "name": abi_info["name"],
                "type": "function",
                "inputs": [{"name": n, "type": t} for n, t in inputs],
                "outputs": [],
            }
        ]
        contract = w3.eth.contract(abi=abi)
        _, decoded = contract.decode_function_input(data)
        return [(name, str(decoded.get(name, "?"))) for name, _ in inputs]
    except Exception:
        return None


def _extract_revert_reason(e: Exception) -> str:
    """Extract clean revert reason from exception."""
    msg = str(e)
    if "execution reverted:" in msg:
        clean = msg.split("execution reverted:")[-1].strip()
        # Remove hex data tuple if present
        if "'," in clean:
            clean = clean.split("',")[0].strip().strip("'").strip('"')
        return clean.strip("'").strip('"')
    return msg

def get_eth_price_usd() -> Optional[float]:
    """Returns ETH price in USD, or None if the API is unavailable."""
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "ethereum", "vs_currencies": "usd"},
            timeout=5,
        )
        response.raise_for_status()
        return response.json()["ethereum"]["usd"]
    except Exception:
        return None

# ── Token metadata cache ──────────────────────────────────────────────────────

ERC20_ABI_MIN = [
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]


@lru_cache(maxsize=256)
def _get_token_meta(address: str, rpc_url: str) -> tuple[str, int]:
    """Returns (symbol, decimals). Cached per address."""
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(address), abi=ERC20_ABI_MIN
        )
        return contract.functions.symbol().call(), contract.functions.decimals().call()
    except Exception:
        return "???", 18


# ── Main analyzer ─────────────────────────────────────────────────────────────


def analyze(tx_hash: str, rpc_url: str) -> None:
    client = ChainClient(rpc_urls=[rpc_url])
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    # ── Fetch tx and receipt ──────────────────────────────────────────────────
    try:
        tx = client.get_transaction(tx_hash)
    except Exception:
        print(f"ERROR: Transaction {tx_hash} not found or invalid hash.")
        sys.exit(1)

    receipt = client.get_receipt(tx_hash)

    # ── Basic info ────────────────────────────────────────────────────────────
    status = "PENDING"
    if receipt:
        status = "SUCCESS" if receipt.status else "FAILED"

    block_number = tx.get("blockNumber") or "pending"
    timestamp = ""
    if block_number != "pending":
        block = w3.eth.get_block(block_number)
        ts = block.get("timestamp", 0)
        timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    value_eth = _wei_to_eth(tx.get("value", 0))

    print()
    print("Transaction Analysis")
    print("=" * 50)
    print(f"Hash:           {tx_hash}")
    print(
        f"Block:          {_format_number(block_number) if block_number != 'pending' else 'pending'}"
    )
    if timestamp:
        print(f"Timestamp:      {timestamp}")
    print(f"Status:         {status}")
    print()
    print(f"From:           {tx.get('from', 'N/A')}")
    print(f"To:             {tx.get('to', 'N/A')}")
    print(f"Value:          {value_eth:.6f} ETH")

    # ── Gas analysis ──────────────────────────────────────────────────────────
    if receipt:
        gas_limit = tx.get("gas", 0)
        gas_used = receipt.gas_used
        gas_pct = (gas_used / gas_limit * 100) if gas_limit else 0
        eff_price = receipt.effective_gas_price
        fee_eth = _wei_to_eth(gas_used * eff_price)
        eth_price_usd = get_eth_price_usd()
        fee_usd = fee_eth * Decimal(eth_price_usd) if eth_price_usd else None

        is_legacy = block.get("baseFeePerGas") is None

        print()
        print("Gas Analysis")
        print("-" * 40)
        print(f"Gas Limit:      {_format_number(gas_limit)}")
        print(f"Gas Used:       {_format_number(gas_used)} ({gas_pct:.2f}%)")

        if is_legacy:
            print("Base Fee:       N/A (pre-EIP-1559)")
            print("Priority Fee:   N/A (pre-EIP-1559)")
            print(f"Gas Price:      {_wei_to_gwei(eff_price):.9f} gwei")
        else:
            base_fee = block["baseFeePerGas"]
            priority_fee = eff_price - base_fee
            print(f"Base Fee:       {_wei_to_gwei(base_fee):.9f} gwei")
            print(f"Priority Fee:   {_wei_to_gwei(priority_fee):.9f} gwei")
            print(f"Effective Price:{_wei_to_gwei(eff_price):.9f} gwei")

        fee_usd_str = f"{fee_usd:.6f} USD" if fee_usd is not None else "N/A (price unavailable)"
        print(f"Transaction Fee:{fee_eth:.18f} ETH ({fee_usd_str})")

    # ── Function decoding ─────────────────────────────────────────────────────
    calldata = _normalize_calldata(tx.get("input", "0x"))
    selector, func_name = _decode_selector(calldata)

    print()
    print("Function Called")
    print("-" * 40)
    print(f"Selector:       {selector}")
    print(f"Function:       {func_name}")

    decoded_args = None
    if selector != "0x" and func_name != "Unknown function":
        decoded_args = _decode_arguments(calldata, selector, w3)
        if decoded_args:
            print("Arguments:")
            for name, value in decoded_args:
                print(f"  - {name}: {value}")
    elif func_name == "Unknown function" and len(calldata) > 10:
        print(f"Raw data:       {calldata[10:50]}...")

    # ── Token transfers + Swap/Sync events ───────────────────────────────────
    if receipt and receipt.logs:
        transfers = []
        swap_events = []
        sync_events = []

        for log in receipt.logs:
            if not log.get("topics"):
                continue
            topic0 = (
                log["topics"][0].hex()
                if isinstance(log["topics"][0], bytes)
                else log["topics"][0]
            )

            # Transfer events
            if topic0 == TRANSFER_TOPIC and len(log["topics"]) >= 3:
                try:
                    t1 = log["topics"][1]
                    t2 = log["topics"][2]
                    from_addr = "0x" + (t1.hex() if isinstance(t1, bytes) else t1)[-40:]
                    to_addr = "0x" + (t2.hex() if isinstance(t2, bytes) else t2)[-40:]
                    raw_data = log["data"]
                    raw_amount = int(
                        raw_data.hex() if isinstance(raw_data, bytes) else raw_data, 16
                    )
                    contract = log["address"]
                    symbol, decimals = _get_token_meta(contract, rpc_url)
                    amount = Decimal(raw_amount) / Decimal(10**decimals)
                    transfers.append(
                        {
                            "symbol": symbol,
                            "from": from_addr,
                            "to": to_addr,
                            "amount": amount,
                            "decimals": decimals,
                        }
                    )
                except Exception:
                    pass

            # Swap events (Uniswap V2)
            elif topic0 == SWAP_TOPIC_V2:
                swap_events.append(log["address"])

            # Sync events (pair reserves update)
            elif topic0 == SYNC_TOPIC:
                sync_events.append(log["address"])

        if transfers:
            print()
            print("Token Transfers")
            print("-" * 40)
            for i, t in enumerate(transfers, 1):
                print(
                    f"{i}. {t['symbol']}: "
                    f"{Web3.to_checksum_address(t['from'])} → "
                    f"{Web3.to_checksum_address(t['to'])}  "
                    f"{t['amount']:.6f} {t['symbol']}"
                )

        if swap_events:
            print()
            print("Swap Events")
            print("-" * 40)
            for i, addr in enumerate(swap_events, 1):
                print(f"{i}. Swap at {addr}")

        if sync_events:
            print()
            print("Sync Events")
            print("-" * 40)
            for i, addr in enumerate(sync_events, 1):
                print(f"{i}. Sync at {addr}")

        # ── Swap Summary ──────────────────────────────────────────────────────
        if selector in SWAP_SELECTORS and len(transfers) >= 2:
            sold = transfers[0]
            received = transfers[-1]
            print()
            print("Swap Summary")
            print("-" * 40)
            print(f"Sold:           {sold['amount']:.6f} {sold['symbol']}")
            print(f"Received:       {received['amount']:.6f} {received['symbol']}")
            if received["amount"] > 0:
                price = sold["amount"] / received["amount"]
                print(
                    f"Execution Price:{price:.4f} {sold['symbol']}/{received['symbol']}"
                )

    # ── Failed tx revert reason ───────────────────────────────────────────────
    if receipt and not receipt.status:
        print()
        print("Revert Info")
        print("-" * 40)
        try:
            w3.eth.call(
                {
                    "to": tx.get("to"),
                    "from": tx.get("from"),
                    "data": calldata,
                    "value": tx.get("value", 0),
                },
                block_identifier=block_number,
            )
        except Exception as e:
            print(f"Revert reason:  {_extract_revert_reason(e)}")

    print()


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze an Ethereum transaction.")
    parser.add_argument("tx_hash", help="Transaction hash (0x...)")
    parser.add_argument("--rpc", default=None, help="RPC endpoint URL")
    args = parser.parse_args()

    import os
    from dotenv import load_dotenv

    load_dotenv()

    rpc_url = args.rpc or os.getenv("RPC_URL")
    if not rpc_url:
        print("ERROR: No RPC URL provided. Use --rpc or set RPC_URL in .env")
        sys.exit(1)

    try:
        analyze(args.tx_hash, rpc_url)
    except ChainError as e:
        print(f"Chain error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
