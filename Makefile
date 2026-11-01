.PHONY: check fmt fmt-check lint types test live

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
