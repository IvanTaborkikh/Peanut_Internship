#!/bin/bash
# Usage:
#   ./scripts/start_fork.sh
#   ./scripts/start_fork.sh --block 19000000   # fork at specific block

set -e

# Load .env if present
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | sed 's/"//g' | xargs)
fi

if [ -z "$ETH_RPC_URL" ] && [ -z "$MAINNET_RPC_URL" ]; then
    echo "ERROR: Set ETH_RPC_URL or MAINNET_RPC_URL in .env"
    exit 1
fi

RPC_URL="${MAINNET_RPC_URL:-$ETH_RPC_URL}"
BLOCK_ARG=""

if [ -n "$1" ] && [ "$1" = "--block" ] && [ -n "$2" ]; then
    BLOCK_ARG="--fork-block-number $2"
fi

echo "Starting Anvil fork..."
echo "  RPC:   ${RPC_URL:0:40}..."
echo "  Port:  8545"
echo "  Accounts: 10 x 10000 ETH"
echo ""

anvil \
    --fork-url "$RPC_URL" \
    $BLOCK_ARG \
    --port 8545 \
    --accounts 10 \
    --balance 10000