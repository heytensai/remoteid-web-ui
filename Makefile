.PHONY: test test-py test-js test-py-cov lint lint-py lint-js build vendor vendor-check install

PYTHON = env/bin/python
NPM = npm
NODE = node

test: test-py test-js vendor-check

build: vendor
	$(NPM) run build

vendor:
	$(NODE) scripts/vendor-deps.mjs

vendor-check:
	$(NODE) scripts/vendor-deps.mjs --check

test-py:
	$(PYTHON) -m pytest tests/ -v

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
