.PHONY: sync test lint run

sync:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check app tests

run:
	uv run loanratio
