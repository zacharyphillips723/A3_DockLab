.PHONY: install test lint typecheck run-blue run-starship bundle-validate bundle-deploy bundle-smoke

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src

run-blue:
	python -m a3docklab.cli simulate configs/scenarios/blue_moon_side.yaml --output runs

run-starship:
	python -m a3docklab.cli simulate configs/scenarios/starship_nose.yaml --output runs

bundle-validate:
	databricks bundle validate -t dev

bundle-deploy:
	databricks bundle deploy -t dev

bundle-smoke:
	databricks bundle run -t dev smoke
