PYTHON ?= python

.PHONY: install test run lint

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m quant_agent.cli --events ./events

lint:
	$(PYTHON) -m compileall -q src tests
