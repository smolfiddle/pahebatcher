VENV := venv
PYTHON := $(VENV)/bin/python
FLARESOLVERR_IMAGE := ghcr.io/flaresolverr/flaresolverr:3.3.21
# To bump FlareSolverr after testing: update tag above (see https://github.com/FlareSolverr/FlareSolverr/releases)

.PHONY: help install run config-show watchlist-list watchlist-check test lint typecheck benchmark clean docker-daemon docker-up docker-down

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Run:"
	@echo "  make run                                interactive wizard (fully automated setup)"
	@echo "  make run https://animepahe.pw/anime/<uuid>               with URL"
	@echo '  make run URL="https://..." ARGS="--all -q 720"           with flags'
	@echo "  make config-show                        display current settings"
	@echo "  make watchlist-list                     list watchlist entries"
	@echo "  make watchlist-check                    check watchlist for new episodes"
	@echo ""
	@echo "Docker Management:"
	@echo "  make docker-up                          ensure FlareSolverr container is running"
	@echo "  make docker-down                        stop FlareSolverr container"
	@echo ""
	@echo "Config (set once, reused every session):"
	@echo "  $(PYTHON) -m pahebatcher config set quality 720"
	@echo "  $(PYTHON) -m pahebatcher config set audio_lang eng"
	@echo "  $(PYTHON) -m pahebatcher config set max_parallel 4"
	@echo "  $(PYTHON) -m pahebatcher config reset"
	@echo ""
	@echo "Watchlist (auto-download ongoing series):"
	@echo '  make run ARGS="watchlist add https://animepahe.pw/anime/<uuid> -q 720 --audio eng -o ~/anime"'
	@echo "  make watchlist-list"
	@echo "  make watchlist-check"
	@echo '  $(PYTHON) -m pahebatcher watchlist show 1'
	@echo '  $(PYTHON) -m pahebatcher watchlist remove 1 --yes'
	@echo ""
	@echo "Dev:"
	@echo "  make test         run 195 tests"
	@echo "  make lint         ruff check (0 errors)"
	@echo "  make typecheck    mypy strict (0 errors)"
	@echo "  make benchmark    full coherence benchmark"
	@echo "  make clean        remove venv + caches"

# 1. Docker Application Layer
docker-daemon:
	@if ! docker info >/dev/null 2>&1; then \
		echo "ERROR: Docker daemon not running."; \
		echo "  WSL: sudo service docker start"; \
		echo "  SYSTEMD: sudo systemctl start docker"; \
		echo "  Docker Desktop: start Docker Desktop and wait for it to be running."; \
		echo ""; \
		echo "Then re-run: make docker-up"; \
		exit 1; \
	fi

# Use exact-name filter to avoid matching my-flaresolverr2 etc.
# --restart unless-stopped keeps FlareSolverr up across reboots.
docker-up: docker-daemon
	@echo "==> [Docker] Checking FlareSolverr container..."
	@if [ "$$(docker ps -q -f name=^/flaresolverr$$ 2>/dev/null)" ]; then \
		echo "    FlareSolverr already running."; \
	elif [ "$$(docker ps -aq -f name=^/flaresolverr$$ 2>/dev/null)" ]; then \
		echo "    Starting stopped FlareSolverr container..."; \
		if ! docker start flaresolverr; then \
			echo "ERROR: Failed to start existing container. Try: docker rm -f flaresolverr && make docker-up"; \
			exit 1; \
		fi; \
	else \
		echo "    Creating FlareSolverr container ($(FLARESOLVERR_IMAGE))..."; \
		if ! docker run -d --restart unless-stopped --name flaresolverr -p 8191:8191 $(FLARESOLVERR_IMAGE); then \
			echo "ERROR: Failed to create container. Check: docker images | grep flaresolverr"; \
			exit 1; \
		fi; \
	fi
	@echo "    FlareSolverr is up at http://localhost:8191/v1"

docker-down:
	@docker stop flaresolverr > /dev/null 2>&1 || true
	@echo "==> [Docker] FlareSolverr stopped."

# 2. Python Application Layer
$(VENV):
	@echo "==> [Python] Preparing virtual environment..."
	@if [ ! -d "$(VENV)" ]; then \
		if ! python3 -m venv $(VENV) 2>&1; then \
			echo "ERROR: python3 -m venv failed."; \
			echo "  Fix: sudo apt update && sudo apt install -y python3-venv"; \
			echo "  Then re-run: make run"; \
			exit 1; \
		fi; \
		echo "    Installing package dependencies..."; \
		if ! $(PYTHON) -m pip install --upgrade pip; then \
			echo "ERROR: pip upgrade failed. Check network/proxy."; \
			exit 1; \
		fi; \
		if ! $(PYTHON) -m pip install -e ".[dev]"; then \
			echo "ERROR: pip install -e .[dev] failed. See output above."; \
			exit 1; \
		fi; \
		echo "    Environment ready."; \
	fi

install: $(VENV)

# 3. Execution Layer — only run/watchlist-check need Docker, never lint/typecheck/test
run: install docker-up
	@if [ -f pahebatcher.toml ]; then \
		$(PYTHON) -c "from pahebatcher.config_manager import ConfigManager; c=ConfigManager(); c.load(); a='SUB' if c.get('audio_lang')=='jpn' else 'DUB'; print(f'  Config: {a}  {c.get(\"quality\")}p  ({c.get(\"max_parallel\")} concurrent)')"; \
	else \
		echo "  First run — settings will be saved after the wizard."; \
	fi
	@echo ""
	@$(PYTHON) -m pahebatcher $(URL) $(ARGS) $(wordlist 2,99,$(MAKECMDGOALS))

config-show: install
	@$(PYTHON) -m pahebatcher config show

watchlist-list: install
	@$(PYTHON) -m pahebatcher watchlist list

watchlist-check: install docker-up
	@$(PYTHON) -m pahebatcher watchlist check

test: install
	@$(PYTHON) -m pytest tests/ -v

lint: install
	@$(PYTHON) -m ruff check src/

typecheck: install
	@$(PYTHON) -m mypy src/

benchmark: install
	@echo "=== Benchmark: Coherence Pass ==="
	@echo "--- ruff ---"
	@$(PYTHON) -m ruff check src/ && echo "ruff: OK (0 errors)"
	@echo "--- mypy ---"
	@$(PYTHON) -m mypy src/ && echo "mypy: OK (0 errors)"
	@echo "--- pytest ---"
	@$(PYTHON) -m pytest tests/ -q
	@echo "--- coverage ---"
	@$(PYTHON) -m pytest tests/ --cov=pahebatcher --cov-report=term-missing --cov-report=html -q || echo "coverage: install pytest-cov for report"
	@echo "=== Benchmark Complete ==="

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache .mypy_cache src/pahebatcher.egg-info dist build
	find src tests -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# Catch-all prevents make from treating URL as a build target
%::
	@true
