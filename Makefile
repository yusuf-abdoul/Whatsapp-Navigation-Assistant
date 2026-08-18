# Contributor entry points. Every target here matches what CI runs, so
# `make ci` locally == green build on GitHub.
#
# Prerequisites: uv (https://github.com/astral-sh/uv), Docker for the
# Postgres + Redis services tests need.

.PHONY: help install fmt lint typecheck test ci services pre-commit

help:
	@echo "Targets:"
	@echo "  install      Install all deps into .venv (uses uv.lock exactly)"
	@echo "  fmt          Apply ruff formatting + import sort"
	@echo "  lint         Run ruff linter (no rewrites)"
	@echo "  typecheck    Run mypy on app/"
	@echo "  test         Run pytest (needs postgres + redis running)"
	@echo "  services     Start postgres + redis via docker compose"
	@echo "  ci           Everything CI runs: lint, format-check, mypy, tests"
	@echo "  pre-commit   Install the git hook so 'make fmt' runs on every commit"

install:
	uv sync --all-extras --frozen

fmt:
	uv run ruff format .
	uv run ruff check . --fix

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy app

services:
	docker compose up -d postgres redis

# `test` does NOT auto-start services — CI already has them running via
# the workflow's `services:` block, and running docker there would fail.
# Locally: run `make services` once at the start of your session.
test:
	uv run pytest -q

# Mirrors .github/workflows/ci.yml. If this passes locally, CI passes.
# Assumes postgres + redis are reachable (run `make services` first locally).
ci: install lint typecheck test

pre-commit:
	uv run pre-commit install
