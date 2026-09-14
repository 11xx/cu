# Installing puts `cu` on PATH so it is callable from anywhere, which is what a
# harness driving the desktop needs — `bin/cu` works, but only if the caller
# knows where the checkout is.
#
# The install is deliberately editable. A snapshot install would keep working
# after the source changed, and a tool whose whole subject is silent failure
# should not have a stale copy of itself as its own first failure mode.

.DEFAULT_GOAL := help
.PHONY: help install uninstall test test-native test-adapters lint build check node-modules

help:
	@echo 'make install    put cu on PATH (editable: tracks this checkout)'
	@echo 'make uninstall  take it off PATH'
	@echo 'make check      every gate: test, test-native, test-adapters, lint, build'
	@echo 'make test       uv run pytest'
	@echo 'make test-native  native playground Node task-state tests'
	@echo 'make test-adapters  Node.js adapter contract tests'
	@echo 'make lint       uv run ruff check .'
	@echo 'make build      uv build'

# Editable, so the installed command is this checkout rather than a copy of it.
# That also means the checkout has to stay where it is: installing from a
# throwaway worktree leaves a `cu` on PATH pointing at a directory that is
# about to be deleted.
install:
	uv tool install --force --editable .
	@echo
	@command -v cu >/dev/null 2>&1 \
		&& echo "installed: $$(command -v cu)" \
		|| echo "installed, but not on PATH yet — run: uv tool update-shell"
	@echo 'run `cu` with no arguments for the guide'

uninstall:
	uv tool uninstall cu

test:
	uv run pytest

test-native: node-modules
	node --test tests/native_playground.test.mjs

lint:
	uv run ruff check .

build:
	uv build

# Installed modules are current when the lockfiles' content matches the digest
# stamped at install time; checkout mtimes say nothing about that. A symlinked
# node_modules is never installed through, because npm ci would rewrite the
# checkout it points at.
NODE_STAMP := node_modules/.cu-lockfile-digest

node-modules:
	@digest=$$(cat package.json package-lock.json | sha256sum | cut -d' ' -f1); \
	if [ "$$(cat $(NODE_STAMP) 2>/dev/null)" = "$$digest" ]; then exit 0; fi; \
	if [ -L node_modules ]; then \
		echo 'node_modules is a symlink to modules for other lockfiles; copy it instead' >&2; \
		exit 1; \
	fi; \
	npm ci --ignore-scripts && echo "$$digest" > $(NODE_STAMP)

test-adapters: node-modules
	node --test adapters/observation.test.mjs browser-extension/*.test.mjs

check: test test-native test-adapters lint build
