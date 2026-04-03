.PHONY: run test lint format lint-fix install pre-commit-install clean

run:
	python src/main.py

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/ core/ chain/

lint-fix:
	ruff check src/ tests/ core/ chain/ --fix

format:
	ruff format src/ tests/ core/ chain/

install:
	pip install -r requirements.txt

pre-commit-install:
	pre-commit install

clean:
	powershell -Command "Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force"
	powershell -Command "Get-ChildItem -Recurse -Directory -Filter .pytest_cache | Remove-Item -Recurse -Force"