run:
	python src/main.py

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/