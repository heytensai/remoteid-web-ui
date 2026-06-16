.PHONY: test test-py test-js test-py-cov

PYTHON = env/bin/python
NPM = npm

test: test-py test-js

test-py:
	$(PYTHON) -m pytest tests/ -v

test-py-cov:
	$(PYTHON) -m pytest tests/ --cov=. --cov-report=term --cov-report=html

test-js:
	$(NPM) test

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install -r dev-requirements.txt
	$(NPM) install

.PHONY: test test-py test-js test-py-cov install
