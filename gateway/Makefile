# hal0 Bifrost gateway — build + deploy.
#
# Go's -buildmode=plugin requires the .so and the host bifrost-http binary to be
# built from an IDENTICAL module graph (same Go toolchain + same dep versions).
# We guarantee that by building BOTH from one pinned Bifrost checkout joined to
# this plugin via a go.work workspace. Do not mix a prebuilt bifrost-http with a
# locally-built .so — it will fail to load with a version-skew panic.

BIFROST_REF ?= v1.5.16            # core module version this plugin is written against
BIFROST_REPO ?= https://github.com/maximhq/bifrost.git
SRC := bifrost-src
OUT := build
PLUGIN := $(OUT)/hal0_normalize.so
GATEWAY := $(OUT)/bifrost-http

# Deploy target (CT105). Held until the live Hermes cutover is approved.
DEPLOY_HOST ?= hal0
PREFIX_BIN  := /opt/hal0/bin
PREFIX_ETC  := /etc/hal0/bifrost

.PHONY: test plugin-check deps gateway plugin build clean deploy

test: ## Unit-test the pure transform logic (no Bifrost needed)
	go test ./normalize/...

plugin-check: ## Compile the plugin .so against the pinned core (fast sanity, not ABI-matched)
	@mkdir -p $(OUT)
	go build -buildmode=plugin -o $(PLUGIN) ./plugin
	@echo "plugin compiles: $(PLUGIN)"

deps: ## Clone/checkout the pinned Bifrost source for an ABI-matched build
	@if [ ! -d $(SRC) ]; then git clone --depth 1 --branch $(BIFROST_REF) $(BIFROST_REPO) $(SRC); \
	else git -C $(SRC) fetch --depth 1 origin $(BIFROST_REF) && git -C $(SRC) checkout -q $(BIFROST_REF); fi

# go.work unites this module + the checkout so the plugin and transport share
# one dependency graph -> loadable .so.
go.work: deps
	go work init . $(SRC)/core $(SRC)/transports/bifrost-http 2>/dev/null || true
	go work sync

gateway: go.work ## Build bifrost-http from the pinned source
	@mkdir -p $(OUT)
	go build -o $(GATEWAY) ./$(SRC)/transports/bifrost-http
	@echo "gateway built: $(GATEWAY)"

plugin: go.work ## Build the ABI-matched plugin .so
	@mkdir -p $(OUT)
	go build -buildmode=plugin -o $(PLUGIN) ./plugin
	@echo "plugin built: $(PLUGIN)"

build: gateway plugin ## Build both, ABI-matched

clean:
	rm -rf $(OUT) $(SRC) go.work go.work.sum

deploy: build ## (HELD) push artifacts + config to CT105; does NOT touch Hermes config
	ssh $(DEPLOY_HOST) 'mkdir -p $(PREFIX_BIN) $(PREFIX_ETC)/plugins'
	scp $(GATEWAY) $(DEPLOY_HOST):$(PREFIX_BIN)/bifrost-http
	scp $(PLUGIN)  $(DEPLOY_HOST):$(PREFIX_ETC)/plugins/hal0_normalize.so
	scp config/config.json $(DEPLOY_HOST):$(PREFIX_ETC)/config.json
	scp systemd/hal0-bifrost.service $(DEPLOY_HOST):/etc/systemd/system/hal0-bifrost.service
	@echo "deployed. Enable with: ssh $(DEPLOY_HOST) systemctl daemon-reload && systemctl enable --now hal0-bifrost"
	@echo "Hermes cutover is a SEPARATE, manual step — see README."
