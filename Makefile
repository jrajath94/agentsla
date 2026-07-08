# AgentSLA developer cheatsheet.
# All commands are designed to be `make` targets that wrap uv invocation so the
# project works the same on CI as on a developer laptop.

.PHONY: help install install-all lint format type test coverage bench clean

help:  ## Show this help. (Default target.)
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install runtime + dev dependencies (excludes adapter/bench/metrics extras).
	uv sync --extra test

install-all:  ## Install everything, including optional adapter/bench/metrics extras.
	uv sync --extra all

lint:  ## ruff lint + format check (LINT-01).
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format the tree.
	uv run ruff format .
	uv run ruff check --fix .

type:  ## mypy --strict on the three target modules (TYPING-01).
	uv run mypy agentsla/core

test:  ## Pytest, no coverage gating. Fast.
	uv run pytest -q

coverage:  ## pytest with coverage report; enforces ≥85% floor (COVERAGE-01).
	uv run pytest --cov=agentsla/core --cov-report=term-missing --cov-fail-under=85

bench:  ## Run the bench harness (Phase 5 surface; stubbed until W8).
	uv run agentsla bench --all

clean:  ## Remove caches + build artifacts but keep .venv.
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -exec rm -rf {} +
