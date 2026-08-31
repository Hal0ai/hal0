#!/bin/sh
# hal0 — pi bundled-agent smoke test (run on an installed box).
# Asserts the spec-D2 minimal profile, pins, and both memory wires.
set -u

PI_PIN="0.84.4"
ADAPTER_PIN="2.31.0"
HINDSIGHT_PIN="0.4.3"
FAIL=0
ok()   { printf 'PASS: %s\n' "$*"; }
bad()  { printf 'FAIL: %s\n' "$*" >&2; FAIL=1; }

AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"

# 1. Binary + pin
v="$(pi --version 2>/dev/null || true)"
case "$v" in *"$PI_PIN"*) ok "pi binary at pin $PI_PIN";; *) bad "pi version '$v' != $PI_PIN";; esac

# 2. Adapter pin
a="$(npm ls -g pi-mcp-adapter 2>/dev/null | grep -o 'pi-mcp-adapter@[0-9.]*' || true)"
case "$a" in *"@$ADAPTER_PIN") ok "pi-mcp-adapter at pin $ADAPTER_PIN";; *) bad "adapter '$a' != $ADAPTER_PIN";; esac

# 3. Managed settings keys
python3 - "$AGENT_DIR/settings.json" <<'EOF' && ok "settings.json managed keys" || bad "settings.json managed keys"
import json, sys
s = json.load(open(sys.argv[1]))
assert s["theme"] == "hal0" and s["defaultProvider"] == "hal0" and s["defaultModel"] == "agent"
pkgs = s["packages"]
for p in ("extensions/hal0-provider", "extensions/hindsight", "npm:pi-mcp-adapter@2.31.0"):
    assert p in pkgs, p
EOF

# 4. No sprawl packages
python3 - "$AGENT_DIR/settings.json" <<'EOF' && ok "no sprawl packages" || bad "sprawl package present"
import json, sys
pkgs = json.load(open(sys.argv[1]))["packages"]
sprawl = ("observational-memory", "honcho", "pi-subagents", "pi-statusline", "pi-lens")
assert not any(any(s in p for s in sprawl) for p in pkgs), pkgs
EOF

# 5. Theme + extensions on disk
[ -f "$AGENT_DIR/themes/hal0.json" ] && ok "theme deployed" || bad "theme missing"
[ -f "$AGENT_DIR/extensions/hal0-provider/index.ts" ] && ok "provider extension" || bad "provider extension missing"
[ -f "$AGENT_DIR/extensions/hindsight/index.ts" ] && ok "hindsight extension" || bad "hindsight extension missing"

# 6. Hindsight dependency resolved at pin
h="$(cd "$AGENT_DIR/extensions/hindsight" && npm ls @vectorize-io/hindsight-coding-agents 2>/dev/null | grep -o '@vectorize-io/hindsight-coding-agents@[0-9.]*' || true)"
case "$h" in *"@$HINDSIGHT_PIN") ok "hindsight-coding-agents at pin $HINDSIGHT_PIN";; *) bad "hindsight dep '$h' != $HINDSIGHT_PIN";; esac

# 7. Memory MCP entry
python3 - "$AGENT_DIR/mcp.json" <<'EOF' && ok "hal0-memory MCP entry" || bad "hal0-memory MCP entry"
import json, sys
cfg = json.load(open(sys.argv[1]))
assert cfg["mcpServers"]["hal0-memory"]["url"].endswith("/mcp/memory")
EOF

# 8. Hindsight client config present (seeded or operator-owned)
[ -f "$HOME/.hindsight/coding-agent.json" ] && ok "hindsight client config" || bad "hindsight client config missing"

exit "$FAIL"
