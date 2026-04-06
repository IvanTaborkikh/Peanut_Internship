.PHONY: run test lint format lint-fix install pre-commit-install clean analyze integration-test help

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

clean:
	$(RM_CACHE)