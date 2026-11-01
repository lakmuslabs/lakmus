.PHONY: check fmt fmt-check lint types test live schema

check: fmt-check lint types test

fmt:
	uv run ruff format .
	uv run ruff check --fix .

fmt-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

types:
	uv run mypy

test:
	uv run pytest -q

live:
	uv run pytest -q -m live

schema:
	uv run python -m lakmus.suite.schema
	uv run python -m lakmus.results.schema
	uv run python -m lakmus.scorers.catalogue
