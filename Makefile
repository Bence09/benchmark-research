PYTHON ?= .venv-agent/bin/python
PYTHON_SYSTEM ?= python3
RUFF := $(PYTHON) -m ruff

.PHONY: all setup requirements build test format-check format lint check help

all: build test

setup:
	bash scripts/setup.sh

.venv-agent:
	$(PYTHON_SYSTEM) -m venv .venv-agent

requirements: .venv-agent
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements/requirements-agent.txt
	$(PYTHON) -m pip install "fuzzywuzzy==0.18.0"
	$(PYTHON) -m pip freeze > installed-agent.txt

build:
	$(PYTHON) -m compileall -q benchkit indextool metrics oracle toolpolicy tests run_demo.py

test:
	SWE_EXPLORE_SOURCE="$(CURDIR)/explore-evaluator-src" \
	REPOBENCH_SOURCE="$(CURDIR)/repobench-src" \
	LITELLM_LOCAL_MODEL_COST_MAP=True \
	$(PYTHON) -m unittest discover -s tests -v

ruff:
	$(RUFF) format --check .

format:
	$(RUFF) format .

lint:
	$(RUFF) check .

check: format-check lint test

help:
	@printf '%s\n' \
		'make setup         - create both environments and vendored sources' \
		'make requirements  - install the pinned agent requirements' \
		'make build         - compile Python sources' \
		'make test          - run the unittest suite' \
		'make ruff  	      - check Ruff formatting without changing files' \
		'make format        - format Python files with Ruff' \
		'make lint          - run Ruff lint checks' \
		'make check         - run format check, lint, and tests'
