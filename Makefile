.PHONY: help install dev test test-unit release-test release-test-report \
        harness harness-report harness-clean harness-install \
        lint fmt typecheck ui-install ui-dev ui-build clean \
        proto-ttft proto-ttft-live

# ── hal0-test LXC connection knobs (release-gate) ───────────────────────────
# Override on the command line, e.g.:
#   make release-test HAL0_TEST_HOST=10.0.1.231 HAL0_TEST_SSH_KEY=~/.ssh/my-test-key
HAL0_TEST_HOST    ?= 10.0.1.230
HAL0_TEST_USER    ?= root
HAL0_TEST_SSH_KEY ?= ~/.ssh/id_ed25519
# Unique-per-run slot prefix so concurrent agents don't collide on the
# shared LXC. Falls back to "local-$$" when not run from CI.
HAL0_TEST_PREFIX  ?= ci-h-$(if $(GITHUB_RUN_ID),$(GITHUB_RUN_ID),local-$$$$)
HAL0_TEST_REPORT  ?= tests/release-gate-report.json

help:
	@echo "hal0 — common dev tasks"
	@echo ""
	@echo "  Local"
	@echo "    make install              Install python package in editable mode with dev extras"
	@echo "    make dev                  Run hal0-api with --reload"
	@echo "    make test                 Run unit tests (tier α, ~3s, every commit)"
	@echo "    make lint                 Ruff check"
	@echo "    make fmt                  Ruff format"
	@echo "    make typecheck            Mypy"
	@echo "    make ui-install           Install UI deps"
	@echo "    make ui-dev               Vite dev server"
	@echo "    make ui-build             Production UI build"
	@echo "    make clean                Remove caches"
	@echo ""
	@echo "  Local harness (full install -> CLI -> slot -> uninstall)"
	@echo "    make harness              Drive every public surface on this host"
	@echo "    make harness-report       Pretty-print last harness run"
	@echo "    make harness-clean        Drop tests/harness/reports/"
	@echo ""
	@echo "  Release-gate (tier γ, hal0-test LXC at $(HAL0_TEST_HOST))"
	@echo "    make release-test         Run full NPU + ROCm + Vulkan matrix over SSH"
	@echo "    make release-test-report  Pretty-print $(HAL0_TEST_REPORT)"

install:
	pip install -e ".[dev]"

dev:
	uvicorn hal0.api:app --reload --host 127.0.0.1 --port 8080

# ── Test tiers (PLAN §10) ────────────────────────────────────────────────────
# α — unit. Pure pytest. No systemd, no docker. ~3s.
# β — integration. Retired in v0.2 — lemond is supervised by systemd
#     directly; per-slot template + toolbox containers are gone (ADR-0008 §2).
#     Replacement integration shape lands with PR-8 / PR-10.
# γ — release-gate. NPU + ROCm + Vulkan on hal0-test LXC. Not per-commit.

test: test-unit

test-unit:
	pytest tests/ -v

harness:
	bash scripts/harness.sh

harness-report:
	@if [ ! -f tests/harness/reports/harness.json ]; then \
	    echo "!  tests/harness/reports/harness.json not found. Run 'make harness' first."; \
	    exit 1; \
	fi
	@python3 scripts/harness-report.py tests/harness/reports/harness.json

harness-clean:
	rm -rf tests/harness/reports/

# Fresh-install smoke (#407): clone the CT-200 hal0-test-template, run a full
# install -> smoke -> uninstall -> destroy cycle on a clean box, and append the
# JSON result line to tests/harness/reports/install-smoke.jsonl. Pass harness
# args via ARGS, e.g.:  make harness-install ARGS="--from-tree $(PWD)"
# Requires SSH to the Proxmox host (HAL0_PVE, default alias 'pve') + the
# CT-200 template. See docs/internal/install-test-harness.md.
harness-install:
	@mkdir -p tests/harness/reports
	bash scripts/fresh-test-ct.sh $(ARGS) | tee -a tests/harness/reports/install-smoke.jsonl

release-test:
	HAL0_TEST_HOST="$(HAL0_TEST_HOST)" \
	HAL0_TEST_USER="$(HAL0_TEST_USER)" \
	HAL0_TEST_SSH_KEY="$(HAL0_TEST_SSH_KEY)" \
	HAL0_TEST_PREFIX="$(HAL0_TEST_PREFIX)" \
	HAL0_TEST_REPORT="$(HAL0_TEST_REPORT)" \
	bash scripts/release-test.sh

release-test-report:
	@if [ ! -f "$(HAL0_TEST_REPORT)" ]; then \
	    echo "!  $(HAL0_TEST_REPORT) not found. Run 'make release-test' first."; \
	    exit 1; \
	fi
	@python3 scripts/release-test-report.py "$(HAL0_TEST_REPORT)"

lint:
	ruff check src tests

fmt:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy src

ui-install:
	cd ui && npm install

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

# ── throwaway prototypes ──────────────────────────────────────────────────
# scripts/prototype_ttft/ — TTFT + KV-cache % measurement and aggregation.
# Delete once the model is validated and lifted into src/hal0/slots/.
proto-ttft:
	cd scripts/prototype_ttft && python3 tui.py

proto-ttft-live:
	cd scripts/prototype_ttft && python3 live_probe.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info
