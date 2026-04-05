.PHONY: run test lint format lint-fix install pre-commit-install clean

run:
	python3 src/main.py

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

format:
	ruff format src/ tests/

install:
	pip3 install -r requirements.txt

pre-commit-install:
	pre-commit install

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +