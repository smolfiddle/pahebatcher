VENV := venv
PYTHON := $(VENV)/bin/python

.PHONY: install run test lint typecheck clean

$(VENV):
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

install: $(VENV)

run: install
	$(PYTHON) -m pahebatcher

test: install
	$(PYTHON) -m pytest tests/ -v

lint: install
	$(PYTHON) -m ruff check src/

typecheck: install
	$(PYTHON) -m mypy src/

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache .mypy_cache src/pahebatcher.egg-info
