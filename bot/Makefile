# Local quality gates for the bot. Run `make check` before pushing.
#
# Mirrors what CI runs in .github/workflows/ci.yaml so a green local
# `make check` means CI will be green too. Saves the round-trip of
# pushing, watching CI fail, fixing, force-pushing.

.PHONY: check format-check format lint typecheck test lock-check fix help

help:
	@echo "Targets:"
	@echo "  check         All quality gates (use this before pushing)"
	@echo "  format-check  ruff format --check (CI-style: fail if unformatted)"
	@echo "  format        ruff format (rewrite files in place)"
	@echo "  lint          ruff check (lint rules)"
	@echo "  typecheck     mypy"
	@echo "  test          pytest unit tests"
	@echo "  lock-check    poetry.lock in sync with pyproject.toml"
	@echo "  fix           ruff format + ruff check --fix (autofixes)"

check: lock-check format-check lint typecheck test
	@echo ""
	@echo "All checks passed. Safe to push."

format-check:
	poetry run ruff format --check src tests

format:
	poetry run ruff format src tests

lint:
	poetry run ruff check src tests

typecheck:
	poetry run mypy src --ignore-missing-imports

test:
	poetry run pytest tests/unit -q --no-cov

# Verify poetry.lock is in sync with pyproject.toml. `poetry lock --check`
# is poetry 1.x; `poetry check --lock` is the 1.8+ replacement. Try both
# so the target works regardless of the user's local poetry version.
lock-check:
	@poetry lock --check 2>/dev/null \
	  || poetry check --lock 2>/dev/null \
	  || (echo "ERROR: poetry.lock is out of sync with pyproject.toml."; \
	      echo "       Run: cd bot && poetry lock --no-update"; exit 1)

fix:
	poetry run ruff format src tests
	poetry run ruff check --fix src tests
