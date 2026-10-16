PYTHON ?= .venv/bin/python
PYTHON_BOOTSTRAP ?= python3
NPM ?= npm

.PHONY: setup build test run docker-build docker-run

setup:
	@$(PYTHON_BOOTSTRAP) -c "import sys; assert sys.version_info[:2] == (3, 12), 'ClipForge requires Python 3.12 for the pinned clean-build path'"
	$(PYTHON_BOOTSTRAP) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -r requirements-dev.lock
	$(NPM) --prefix frontend ci

build:
	$(NPM) --prefix frontend run build

test:
	$(PYTHON) -B -m pytest -q --tb=short tests

run:
	$(PYTHON) main.py --web

docker-build:
	docker build -t clipforge:local .

docker-run:
	docker run --rm -p 5174:5174 -v clipforge-data:/data clipforge:local
