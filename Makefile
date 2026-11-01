.PHONY: test test-all test-py test-py-db test-py-all test-js test-py-cov lint lint-py lint-js build vendor vendor-check install

PYTHON = env/bin/python
NPM = npm
NODE = node

# Fast local gate: no PostgreSQL required.
test: test-py test-js

# Full gate: complete Python suite (including DB tests) + JS + vendor check.
test-all: test-py-all test-js vendor-check

# DB-independent Python tests only.
test-py:
	$(PYTHON) -m pytest tests/ -v

# PostgreSQL-backed Python tests only.
test-py-db:
	$(PYTHON) -m pytest tests/ -v --db-only

# Complete Python suite, including PostgreSQL-backed tests.
test-py-all:
	$(PYTHON) -m pytest tests/ -v --db

# Full gate, including the vendor sync check.
test-all: test vendor-check

build: vendor
	$(NPM) run build

vendor:
	$(NODE) scripts/vendor-deps.mjs

vendor-check:
	$(NODE) scripts/vendor-deps.mjs --check

test-py-cov:
	$(PYTHON) -m pytest tests/ --cov=. --cov-report=term --cov-report=html

test-js:
	$(NPM) test

lint: lint-py lint-js

lint-py:
	$(PYTHON) -m pylint *.py

lint-js:
	$(NPM) exec eslint -- static/js/

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install -r dev-requirements.txt
	$(NPM) install
