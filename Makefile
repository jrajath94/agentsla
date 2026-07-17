# AgentSLA developer cheatsheet.
# All commands are designed to be `make` targets that wrap uv invocation so the
# project works the same on CI as on a developer laptop.

.PHONY: help install install-all lint format type test coverage bench bench-full clean

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
	uv run mypy agentsla/core agentsla/policy agentsla/verify

test:  ## Pytest, no coverage gating. Fast.
	uv run pytest -q

coverage:  ## pytest with coverage report; enforces ≥85% floor (COVERAGE-01).
	uv run pytest --cov=agentsla/core --cov=agentsla/policy --cov=agentsla/verify --cov-report=term-missing --cov-fail-under=85

bench:  ## Run the bench harness (Phase 5 surface; stubbed until W8).
	uv run agentsla bench --all

# `bench-full` mirrors the integration-check job in
# .github/workflows/test.yml so a developer can reproduce the
# CI integration gate locally with one command. The three subcommands
# match the `Bench reproducer` / `Seeded-error experiment` / `Report`
# steps the workflow runs.
bench-full:  ## bench + bench-seeded-errors + report (mirrors CI integration gate).
	uv run python -m agentsla bench --seeds 2 --out bench/results/results.parquet
	uv run python -m agentsla bench-seeded-errors \
	    --strategies 0,50 --trials 5 \
	    --out bench/results/seeded_errors.parquet \
	    --report-section-out bench/results/seeded_errors_section.md
	uv run python -m agentsla report --out bench/results/REPORT.md

clean:  ## Remove caches + build artifacts but keep .venv.
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -exec rm -rf {} +
