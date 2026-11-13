#!/bin/sh
# hal0 — pi bundled-agent smoke test (run on an installed box).
# Asserts the spec-D2 minimal profile, pins, and both memory wires.
set -u

PI_PIN="1.5.2"
ADAPTER_PIN="7.0.1"
HINDSIGHT_PIN="0.8.4"
FAIL=0
ok()   { printf 'PASS: %s\n' "$*"; }
bad()  { printf 'FAIL: %s\n' "$*" >&2; FAIL=1; }

AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"

# 1. Binary + pin
v="$(pi --version 2>/dev/null || true)"
case "$v" in *"$PI_PIN") ok "pi binary at pin $PI_PIN";; *) bad "pi version '$v' != $PI_PIN";; esac

# 2. Adapter pin
a="$(npm ls -g pi-mcp-adapter 2>/dev/null | grep -o 'pi-mcp-adapter@[0-9.]*' || true)"
case "$a" in *"@$ADAPTER_PIN") ok "pi-mcp-adapter at pin $ADAPTER_PIN";; *) bad "adapter '$a' != $ADAPTER_PIN";; esac

# 3. Managed settings keys
python3 - "$AGENT_DIR/settings.json" <<'EOF' && ok "settings.json managed keys" || bad "settings.json managed keys"
import json, sys
s = json.load(open(sys.argv[1]))
assert s["theme"] == "hal0" and s["defaultProvider"] == "hal0" and s["defaultModel"] == "agent"
pkgs = s["packages"]
assert "npm:pi-mcp-adapter@7.0.1" in pkgs, pkgs
# pi auto-discovers ~/.pi/agent/extensions/; explicit entries double-load.
for p in ("extensions/hal0-provider", "extensions/hindsight"):
    assert p not in pkgs, p
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

# 6b. Custom-message turns. Pi >= 1.2.0 fires before_agent_start for runs started
# by pi.sendMessage(..., {triggerTurn: true}); those events carry `message`, not
# `prompt` (vectorize-io/hindsight#5592, #6016). The extension must not read
# event.prompt unguarded. Static check on the installed dist/pi.js: no code is run.
pijs="$AGENT_DIR/extensions/hindsight/node_modules/@vectorize-io/hindsight-coding-agents/dist/pi.js"
if [ ! -f "$pijs" ]; then
  bad "hindsight dist/pi.js missing"
elif grep -q 'event\.prompt\.trim()' "$pijs" && ! grep -q 'typeof event\.prompt' "$pijs"; then
  bad "hindsight pi.js reads event.prompt unguarded: a custom-message turn throws TypeError (fixed upstream in 0.8.4)"
else
  ok "hindsight pi.js guards custom-message turns"
fi

# 6c. Self-update must be off for Pi, or the pin is not the version that runs.
python3 - "$HOME/.hindsight/coding-agent.json" <<'EOF' && ok "hindsight autoUpdate off for pi" || bad "hindsight autoUpdate not off for pi"
import json, sys
c = json.load(open(sys.argv[1]))
pi = (c.get("harnesses") or {}).get("pi") or {}
assert pi.get("autoUpdate", c.get("autoUpdate", True)) is False, "autoUpdate is on"
EOF

# 7. Memory MCP entry
python3 - "$AGENT_DIR/mcp.json" <<'EOF' && ok "hal0-memory MCP entry" || bad "hal0-memory MCP entry"
import json, sys
cfg = json.load(open(sys.argv[1]))
assert cfg["mcpServers"]["hal0-memory"]["url"].endswith("/mcp/memory/mcp")
EOF

# 8. Hindsight client config present (seeded or operator-owned)
[ -f "$HOME/.hindsight/coding-agent.json" ] && ok "hindsight client config" || bad "hindsight client config missing"

exit "$FAIL"
