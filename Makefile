.PHONY: run test lint format lint-fix install pre-commit-install clean analyze integration-test pricing-demo impact-analyzer test-mempool test-fork fork stop-fork smoke-exchange smoke-orderbook smoke-tracker arb-check rebalance-check rebalance-plan pnl-summary pnl-recent smoke-multi arb-log smoke e2e bot sim smoke-dex verify-tx dry-run dry-run-prod dry-run-chip flatten flatten-testnet kill unkill heartbeat help

# Portable timeout: GNU coreutils ships `timeout` on Linux, `gtimeout` via brew on macOS.
TIMEOUT := $(shell command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null)
DRY_RUN_SECONDS ?= 1800

# ── OS detection ──────────────────────────────────────────────────────────────
ifeq ($(OS),Windows_NT)
    PYTHON   = .venv\Scripts\python
    PIP      = .venv\Scripts\pip
    RM_CACHE = for /d /r . %%d in (__pycache__ .pytest_cache) do @if exist "%%d" rd /s /q "%%d"
    TX_CHECK = @if "$(TX)"=="" (echo Usage: make analyze TX=0x...  [RPC=https://...] & exit 1)
else
    PYTHON   = .venv/bin/python3
    PIP      = .venv/bin/pip3
    RM_CACHE = find . -type d \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} +
    TX_CHECK = @test -n "$(TX)" || (echo "Usage: make analyze TX=0x...  [RPC=https://...]"; exit 1)
endif

# ── Commands ──────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "Available commands:"
	@echo ""
	@echo "  Setup"
	@echo "    make install              Install all dependencies"
	@echo "    make pre-commit-install   Wire up git hooks (ruff + detect-secrets)"
	@echo ""
	@echo "  Development"
	@echo "    make test                 Run all unit tests"
	@echo "    make lint                 Check code with ruff"
	@echo "    make lint-fix             Auto-fix lint errors"
	@echo "    make format               Auto-format code"
	@echo "    make clean                Remove cache files (__pycache__, .pytest_cache)"
	@echo ""
	@echo "  Blockchain"
	@echo "    make analyze TX=0x...              Analyze a transaction (uses RPC_URL from .env)"
	@echo "    make analyze TX=0x... RPC=https:// Analyze with a specific RPC endpoint"
	@echo ""
	@echo "    make integration-test                          Run integration test on Sepolia (default: 0.000001 ETH)"
	@echo "    make integration-test AMOUNT=0.00005           Send custom amount"
	@echo "    make integration-test TO=0xAddress             Send to custom address"
	@echo "    make integration-test AMOUNT=0.00005 TO=0x...  Both"
	@echo ""
	@echo "  Exchange / Inventory (Week 3)"
	@echo "    make smoke-exchange           Fetch order book, balance and fees from Binance testnet"
	@echo "    make smoke-orderbook          Formatted order book report  [PAIR=ETH/USDT DEPTH=20 SMALL=2 LARGE=10]"
	@echo "    make smoke-tracker            Inventory snapshot + skew analysis"
	@echo "    make arb-check                Binance arb check  [PAIR=ETH/USDT SIZE=1]"
	@echo "    make smoke-multi              Multi-exchange arb: Bybit vs Binance  [PAIR=ETH/USDT SIZE=1]"
	@echo "    make arb-log                  Show arb opportunity log  [N=20 FILE=arb_log.csv]"
	@echo "    make rebalance-check          Show inventory skew across venues"
	@echo "    make rebalance-plan           Generate transfer plan  [ASSET=ETH]"
	@echo "    make pnl-summary              PnL summary (simulated trades)"
	@echo "    make pnl-recent               Last N trades  [N=5]"
	@echo ""
	@echo "  Bot (Week 4)"
	@echo "    make smoke                Quick end-to-end smoke test (no keys needed, 5 ticks)"
	@echo "    make e2e                  Full e2e test — 8 scenarios (replay, CB, partial fill, etc.)"
	@echo "    make bot                  Run arb bot in simulation mode (Ctrl+C to stop)"
	@echo "    make bot PAIR=BTC/USDT    Run with custom pair"
	@echo "    make sim                  Realistic market simulation (100 ticks, random prices)"
	@echo "    make sim TICKS=200        Custom tick count"
	@echo "    make sim SEED=42          Reproducible run with fixed seed"
	@echo "    make sim VERBOSE=1        Show per-tick log output"
	@echo "    make smoke-dex            Real Uniswap V2 mainnet quotes vs Binance order book"
	@echo "    make verify-tx            Verify tx_builder + unwind end-to-end (no RPC needed)"
	@echo ""
	@echo "  Safety / Operations (Week 5)"
	@echo "    make dry-run                       Dry-run with configs/test.yaml (Binance testnet, 30 min)"
	@echo "    make dry-run-prod                  Dry-run with configs/prod.yaml (Binance mainnet, dry_run=true)"
	@echo "    make dry-run-chip                  Dry-run CHIP/USDT (Binance) ↔ Uniswap V3 CHIP/USDC (Arbitrum)"
	@echo "    make dry-run CONFIG=path/foo.yaml  Custom config"
	@echo "    DRY_RUN_SECONDS=300 make dry-run   Override 30-min cap (5 min smoke)"
	@echo "    make flatten-testnet      Plan emergency flatten (testnet, dry preview)"
	@echo "    make flatten              Plan emergency flatten (production, dry preview)"
	@echo "    make kill                 Activate kill switch (touch /tmp/arb_bot_kill)"
	@echo "    make unkill               Disarm kill switch"
	@echo "    make heartbeat            Show current heartbeat age"
	@echo ""
	@echo "  Pricing"
	@echo "    make pricing-demo             Run pricing module demo (no network needed)"
	@echo "    make impact-analyzer          Show price impact table (offline demo)"
	@echo "    make impact-analyzer TOKEN_IN=USDC SIZES=1000,10000,100000  Custom sizes"
	@echo "    make test-mempool             Watch live mempool for Uniswap swaps (needs WS_RPC_URL)"
	@echo "    make test-fork                Test fork simulator (needs Anvil running)"
	@echo "    make fork                     Start Anvil mainnet fork on port 8545"
	@echo "    make stop-fork                Stop running Anvil process"
	@echo ""

run:
	$(PYTHON) src/main.py

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	$(PYTHON) -m ruff check src/ tests/

lint-fix:
	$(PYTHON) -m ruff check src/ tests/ --fix

format:
	$(PYTHON) -m ruff format src/ tests/

install:
	$(PIP) install -r requirements.txt

pre-commit-install:
	pre-commit install

analyze:
	$(TX_CHECK)
	$(PYTHON) -m src.chain.analyzer $(TX) $(if $(RPC),--rpc $(RPC),)

integration-test:
	$(PYTHON) scripts/integration_test.py $(if $(AMOUNT),--amount $(AMOUNT),) $(if $(TO),--to $(TO),)

pricing-demo:
	PYTHONPATH=. $(PYTHON) scripts/pricing_demo.py

impact-analyzer:
	PYTHONPATH=. $(PYTHON) -m src.pricing.impact_analyzer $(if $(PAIR),$(PAIR),demo) $(if $(TOKEN_IN),--token-in $(TOKEN_IN),) $(if $(SIZES),--sizes $(SIZES),) $(if $(RPC),--rpc $(RPC),)

test-mempool:
	PYTHONPATH=. $(PYTHON) scripts/test_mempool.py

test-fork:
	PYTHONPATH=. $(PYTHON) scripts/test_fork_simulator.py

fork:
	bash scripts/start_fork.sh

stop-fork:
	@pkill -f "anvil" && echo "Anvil stopped." || echo "Anvil was not running."

clean:
	$(RM_CACHE)

smoke-exchange:
	PYTHONPATH=. $(PYTHON) scripts/smoke_exchange.py

smoke-orderbook:
	PYTHONPATH=. $(PYTHON) scripts/smoke_orderbook.py $(if $(PAIR),$(PAIR),ETH/USDT) $(if $(DEPTH),$(DEPTH),20) $(if $(SMALL),$(SMALL),2) $(if $(LARGE),$(LARGE),10)

smoke-tracker:
	PYTHONPATH=. $(PYTHON) scripts/smoke_tracker.py

arb-check:
	PYTHONPATH=. $(PYTHON) -m src.integration.arb_checker $(if $(PAIR),$(PAIR),ETH/USDT) $(if $(SIZE),--size $(SIZE),)

rebalance-check:
	PYTHONPATH=. $(PYTHON) -m src.inventory.rebalancer --check

rebalance-plan:
	PYTHONPATH=. $(PYTHON) -m src.inventory.rebalancer --plan $(if $(ASSET),$(ASSET),ETH)

pnl-summary:
	PYTHONPATH=. $(PYTHON) -m src.inventory.pnl --summary

pnl-recent:
	PYTHONPATH=. $(PYTHON) -m src.inventory.pnl --recent $(if $(N),$(N),5)

smoke-multi:
	PYTHONPATH=. $(PYTHON) scripts/smoke_multi_exchange.py $(if $(PAIR),$(PAIR),ETH/USDT) $(if $(SIZE),$(SIZE),1.0)

arb-log:
	PYTHONPATH=. $(PYTHON) -m src.integration.arb_logger $(if $(N),--tail $(N),) $(if $(FILE),--file $(FILE),)

smoke:
	PYTHONPATH=. $(PYTHON) scripts/smoke_test.py

e2e:
	PYTHONPATH=. $(PYTHON) scripts/bot_e2e_test.py

bot:
	PYTHONPATH=. $(PYTHON) scripts/arb_bot.py

sim:
	PYTHONPATH=. $(PYTHON) scripts/realistic_sim.py \
		$(if $(TICKS),--ticks $(TICKS),) \
		$(if $(SEED),--seed $(SEED),) \
		$(if $(VERBOSE),--verbose,)

smoke-dex:
	PYTHONPATH=. $(PYTHON) scripts/smoke_real_dex.py

verify-tx:
	PYTHONPATH=. $(PYTHON) scripts/verify_tx_unwind.py

dry-run: CONFIG ?= configs/test.yaml
dry-run:
ifeq ($(TIMEOUT),)
	@echo "⚠️  No timeout binary. macOS: brew install coreutils. Running unbounded — Ctrl+C to stop."
	PYTHONPATH=. $(PYTHON) scripts/arb_bot.py --config $(CONFIG) || true
else
	PYTHONPATH=. $(TIMEOUT) $(DRY_RUN_SECONDS) $(PYTHON) scripts/arb_bot.py --config $(CONFIG) || true
endif
	@echo ""
	@echo "Dry-run finished. Latest log:"
	@ls -t logs/bot_*.log 2>/dev/null | head -1
	@echo ""
	@echo "Would-trade signals captured:"
	@latest=$$(ls -t logs/bot_*.log 2>/dev/null | head -1); \
		if [ -n "$$latest" ]; then \
			grep -c "DRY RUN | Would trade" "$$latest" 2>/dev/null || true; \
		else echo 0; fi

dry-run-prod:
	@$(MAKE) dry-run CONFIG=configs/prod.yaml

dry-run-chip:
	@$(MAKE) dry-run CONFIG=configs/chip_observe.yaml

flatten-testnet:
	PYTHONPATH=. $(PYTHON) scripts/emergency_flatten.py --testnet

flatten:
	PYTHONPATH=. $(PYTHON) scripts/emergency_flatten.py

kill:
	touch /tmp/arb_bot_kill
	@echo "Kill switch ARMED. Bot will stop within ~5s."

unkill:
	rm -f /tmp/arb_bot_kill
	@echo "Kill switch disarmed."

heartbeat:
	@if [ -f /tmp/arb_bot_heartbeat ]; then \
	  age=$$(echo "$$(date +%s) - $$(cat /tmp/arb_bot_heartbeat)" | bc -l); \
	  printf "heartbeat age: %.0fs\n" $$age; \
	else \
	  echo "no heartbeat file — bot not running"; \
	fi