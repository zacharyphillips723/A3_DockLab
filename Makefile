.PHONY: install test lint typecheck run-blue run-starship

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src

run-blue:
	python -m a3docklab.cli configs/scenarios/blue_moon_side.yaml --output runs

run-starship:
	python -m a3docklab.cli configs/scenarios/starship_nose.yaml --output runs
