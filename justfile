[parallel]
check: check-format lint check-types test

check-format:
    uv run ruff format --check

check-types:
    uv run basedpyright

fix: format lint-fix

format:
    uv run ruff format

lint:
    uv run ruff check

lint-fix:
    uv run ruff check --fix-only

test:
    uv run pytest
