VENV := venv
PYTHON := $(VENV)/bin/python

.PHONY: help install run config-show test lint typecheck clean

help: install
	@echo "Usage: make <target>"
	@echo ""
	@echo "Run:"
	@echo "  make run                           launch interactive wizard"
	@echo '  make run URL="https://animepahe.pw/anime/<uuid>" ARGS="--all -q 720"'
	@echo "  make config-show                   display current settings"
	@echo ""
	@echo "Config (set once, reused every session):"
	@echo "  $(PYTHON) -m pahebatcher config set quality 720"
	@echo "  $(PYTHON) -m pahebatcher config set audio_lang eng"
	@echo "  $(PYTHON) -m pahebatcher config set max_parallel 4"
	@echo "  $(PYTHON) -m pahebatcher config reset"
	@echo ""
	@echo "Dev:"
	@echo "  make test         run 98 tests"
	@echo "  make lint         ruff check"
	@echo "  make typecheck    mypy strict"
	@echo "  make clean        remove venv + caches"

$(VENV):
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

install: $(VENV)

run: install
	@if [ -f pahebatcher.toml ]; then \
		$(PYTHON) -c "from pahebatcher.config_manager import ConfigManager; c=ConfigManager(); c.load(); a='SUB' if c.get('audio_lang')=='jpn' else 'DUB'; print(f'  Config: {a}  {c.get(\"quality\")}p  ({c.get(\"max_parallel\")} concurrent)  —  make config-show for all')"; \
	else \
		echo "  First run — settings will be saved after the wizard."; \
	fi
	@echo ""
	$(PYTHON) -m pahebatcher $(URL) $(ARGS)

config-show: install
	$(PYTHON) -m pahebatcher config show

test: install
	$(PYTHON) -m pytest tests/ -v

lint: install
	$(PYTHON) -m ruff check src/

typecheck: install
	$(PYTHON) -m mypy src/

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache .mypy_cache src/pahebatcher.egg-info dist build
	find src tests -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
