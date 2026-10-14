PY ?= $(CONDA_PREFIX)/bin/python

install:
	uv pip install --python $(PY) -r pyproject.toml --group dev

fmt:
	$(PY) -m isort logipilot tests scripts
	$(PY) -m black logipilot tests scripts

lint:
	$(PY) -m ruff check logipilot tests scripts
	$(PY) -m isort --check-only logipilot tests scripts
	$(PY) -m black --check logipilot tests scripts

test:
	$(PY) -m pytest -q

run:
	$(PY) -m uvicorn logipilot.tools_api.main:app --port 8080
