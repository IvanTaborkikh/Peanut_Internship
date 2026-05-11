"""One-shot ERC20 approve script for the bot's wallet.

Approves USDC and CHIP on Arbitrum to spend MAX_UINT256 via Uniswap V3
SwapRouter02. Without this, the very first DEX leg reverts on
`TRANSFER_FROM_FAILED`.

Run once before flipping the bot to live trading:

    python scripts/grant_v3_allowances.py

Reads PRIVATE_KEY and ARBITRUM_RPC_URL from .env. Skips tokens that already
have a sufficient allowance, so re-running is safe.
"""
import os
import sys
import time

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

load_dotenv()

# Arbitrum mainnet addresses.
SWAP_ROUTER_02 = Web3.to_checksum_address('0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45')
USDC = Web3.to_checksum_address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831')
CHIP = Web3.to_checksum_address('0x0c1C1C109fE34733FCa54B82D7B46b75CFB71F6E')

MAX_UINT256 = 2**256 - 1
# A "sufficient" allowance check threshold — anything above this we skip.
SUFFICIENT_ALLOWANCE = 10**30

ERC20_ABI = [
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address", "name": "owner"}, {"type": "address", "name": "spender"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"type": "address", "name": "spender"}, {"type": "uint256", "name": "amount"}],
     "outputs": [{"type": "bool"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
]


def main() -> int:
    pk = os.getenv('PRIVATE_KEY')
    rpc = os.getenv('ARBITRUM_RPC_URL')
    if not pk:
        print("ERROR: PRIVATE_KEY not set in .env", file=sys.stderr)
        return 1
    if not rpc:
        print("ERROR: ARBITRUM_RPC_URL not set in .env", file=sys.stderr)
        return 1

    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        print(f"ERROR: cannot connect to {rpc}", file=sys.stderr)
        return 1

    account = Account.from_key(pk)
    print(f"Wallet: {account.address}")
    eth_balance = w3.eth.get_balance(account.address) / 1e18
    print(f"ETH balance (gas): {eth_balance:.6f}")
    if eth_balance < 0.0003:
        print("WARNING: ETH balance low; approves may run out of gas. Bridge ~0.001 ETH first.")

    for token_addr in (USDC, CHIP):
        token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
        symbol = token.functions.symbol().call()
        current = token.functions.allowance(account.address, SWAP_ROUTER_02).call()
        if current >= SUFFICIENT_ALLOWANCE:
            print(f"  {symbol}: already approved ({current}), skipping")
            continue

        print(f"  {symbol}: approving SwapRouter02 ({SWAP_ROUTER_02}) for MAX_UINT256...")
        fn = token.functions.approve(SWAP_ROUTER_02, MAX_UINT256)
        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price
        tx = fn.build_transaction({
            'from': account.address,
            'nonce': nonce,
            'chainId': 42161,
            'maxFeePerGas': gas_price + Web3.to_wei(2, 'gwei'),
            'maxPriorityFeePerGas': Web3.to_wei(2, 'gwei'),
        })
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        if not tx_hash.startswith('0x'):
            tx_hash = '0x' + tx_hash
        print(f"    tx_hash: {tx_hash}")
        print(f"    https://arbiscan.io/tx/{tx_hash}")

        # Wait for receipt.
        deadline = time.time() + 60
        receipt = None
        while time.time() < deadline:
            try:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    break
            except Exception:
                pass
            time.sleep(1)

        if receipt is None:
            print(f"    TIMEOUT waiting for {symbol} approve receipt", file=sys.stderr)
            return 2
        if receipt['status'] != 1:
            print(f"    REVERTED: {symbol} approve failed", file=sys.stderr)
            return 3
        print(f"    confirmed in block {receipt['blockNumber']}")

    print("\nAll approves done. The bot can now broadcast V3 swaps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
