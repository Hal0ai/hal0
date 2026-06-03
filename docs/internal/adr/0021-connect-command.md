# ADR 0021 — `hal0 connect` command for external MCP client wiring

- **Status:** Proposed
- **Date:** 2026-06-02
- **Drivers:** Manual MCP configuration is error-prone (wrong URLs,
  wrong transport type — see Hermes diagnosis 2026-06-01). External
  clients (Claude Code, OpenWebUI) need a discoverable, repeatable
  way to wire into hal0's MCP servers without hal0 owning their
  config lifecycle.
- **Related:** ADR-0013 (MCP-client allow-list for bundled agents),
  ADR-0015 (MCP as host platform), ADR-0004 (agents).

## Context

hal0 exposes MCP servers (currently `hal0-admin` and `hal0-memory`)
that external tools can connect to. Today, wiring these into a
client like Claude Code requires manually editing `settings.json`
with the correct URLs, transport type, and permissions. This process
is:

1. **Undiscoverable** — new users don't know MCP servers exist
2. **Error-prone** — wrong URLs (`/mcp/admin` vs `/mcp/admin/mcp`),
   wrong transport (`streamable-http` vs `http`), both confirmed
   bugs found 2026-06-01
3. **Not verifiable** — no way to confirm the connection works
   after manual configuration

### Boundary principle

hal0 owns its managed agents (Hermes, pi-coder). External tools
(Claude Code, OpenWebUI) own their own config. hal0 should
**document the MCP contract and offer a connector**, not inject
config into external tools without consent.

## Decision

Add a top-level `hal0 connect <client>` CLI command that wires
external tools to hal0's MCP servers.

### Command hierarchy

```
hal0 connect <client>           # Wire a client to hal0's MCP servers
hal0 connect --list             # Show all supported connectors + status
hal0 connect <client> --repair  # Fix existing misconfigured connections
hal0 connect <client> --dry-run # Print what would be done, don't execute
hal0 connect <client> --yes     # Skip confirmation prompt
hal0 connect <client> --host X  # Override MCP host (default: 127.0.0.1)
```

Top-level (not under `hal0 agent`) because external clients are
not hal0-managed agents.

### Design decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Command hierarchy | Top-level `hal0 connect` | External clients ≠ hal0 agents; separate concern |
| 2 | Service discovery | API endpoint with hardcoded fallback | Forward-compatible; works if API is down during setup |
| 3 | Execution mode | Outline → confirm → execute | User sees what will happen, confirms, `--dry-run` for inspect-only |
| 4 | Existing config handling | Detect and repair | Catches stale URLs/types; `--repair` flag for explicit fix runs |
| 5 | Connector architecture | Plugin registry in `connectors/` | Extensible without modifying core; standard interface |
| 6 | Install responsibility | hal0 connects, doesn't install | Detect → advise install link → exit. No ownership creep |
| 7 | Claude Code scope | MCP servers + permissions | `claude mcp add` for servers; JSON merge for permissions |
| 8 | Permissions method | JSON edit with fallback to manual | Auto-configure with backup; print instructions if edit fails |
| 9 | Post-wire verification | Verify + tool count | Hit MCP endpoint, count tools, report. Catches misconfig immediately |
| 10 | Remote connections | `--host` flag, localhost default | No tunnel plumbing in v1; explicit override for LAN access |
| 11 | List command | Show all connectors with status | Discovery mechanism — shows available even if not installed |
| 12 | Disconnect | Defer to post-v1 | Ship connect first; users can manually remove |
| 13 | Output style | Rich panels | Consistent with existing hal0 CLI (uses Rich already) |
| 14 | V1 connectors | claude-code + openwebui | Two connectors prove the pattern; both already on hal0 |

### MCP discovery endpoint

New API endpoint `GET /api/mcp/servers` returns the list of
available MCP servers:

```json
[
  {"name": "admin", "path": "/mcp/admin/mcp", "type": "http", "description": "Slot/model management, system admin"},
  {"name": "memory", "path": "/mcp/memory/mcp", "type": "http", "description": "Cognee vector/graph memory"}
]
```

Fallback when API unreachable: hardcoded list of `admin` and
`memory` at default paths.

### Connector plugin interface

```
hal0/cli/connectors/
├── __init__.py          # Registry: discovers and loads connectors
├── _base.py             # BaseConnector abstract class
├── claude_code.py       # Claude Code connector
└── openwebui.py         # OpenWebUI connector
```

```python
class BaseConnector(ABC):
    name: str                          # CLI name ("claude-code")
    display_name: str                  # Human name ("Claude Code")
    install_hint: str                  # How to install if not found

    @abstractmethod
    def detect(self) -> bool:
        """Is this client installed on this machine?"""

    @abstractmethod
    def check(self, servers) -> list[Issue]:
        """Check existing config. Return list of issues (empty = all good)."""

    @abstractmethod
    def wire(self, servers, host) -> Result:
        """Wire MCP servers into the client's config."""

    @abstractmethod
    def verify(self, servers, host) -> list[ServerStatus]:
        """Verify each server is reachable and count tools."""
```

### Claude Code connector specifics

**Detection:** Check `claude` in PATH.

**Wiring:**
1. Run `claude mcp add hal0-<name> --type http --url <url>` for
   each MCP server
2. Read `~/.claude/settings.json`, merge `mcp__hal0-*__*` into
   `permissions.allow` (backup original first)
3. Validate JSON after write; on any error, fall back to printing
   manual instructions

**Repair:** Compare existing MCP server entries against discovery
endpoint. Fix URLs and transport types that don't match.

**Verification:** POST `{"jsonrpc":"2.0","method":"tools/list","id":1}`
to each MCP endpoint, count tools in response.

### OpenWebUI connector specifics

**Detection:** Check if OpenWebUI is running (port 3001 or docker
container `hal0-openwebui`).

**Wiring:** OpenWebUI connects to LLM backends. The connector
configures OpenWebUI to use hal0's inference endpoint as its model
source (Lemonade on port 9000, OpenAI-compatible API).

**Repair:** Verify endpoint URL and model availability.

**Verification:** Hit OpenWebUI health endpoint, confirm model
list includes hal0-served models.

### Execution flow

```
$ hal0 connect claude-code

  Discovering MCP servers... 2 found
  Checking Claude Code... installed (v2.1.159)

  Plan:
    1. Add hal0-admin  → http://127.0.0.1:8080/mcp/admin/mcp (http)
    2. Add hal0-memory → http://127.0.0.1:8080/mcp/memory/mcp (http)
    3. Add permissions  → mcp__hal0-admin__*, mcp__hal0-memory__*

  Proceed? [Y/n] y

  ╭─ Claude Code Connected ──────────────────────╮
  │  hal0-admin   ✓  29 tools                    │
  │  hal0-memory  ✓   4 tools                    │
  │  Permissions  ✓   2 rules added              │
  │                                               │
  │  Total: 33 tools available                    │
  │  Run `claude` to start using hal0 tools.      │
  ╰───────────────────────────────────────────────╯
```

Repair flow:
```
$ hal0 connect claude-code --repair

  Checking Claude Code configuration...
    hal0-admin:  type is "streamable-http" (should be "http") — will fix
    hal0-memory: ✓ correct

  Fix 1 issue? [Y/n] y

  ╭─ Claude Code Repaired ───────────────────────╮
  │  hal0-admin   ✓  type: http (was: stream...)  │
  │  hal0-memory  ✓  no change                    │
  ╰───────────────────────────────────────────────╯
```

Not installed:
```
$ hal0 connect cursor

  Cursor not found on this machine.
  Install: https://cursor.sh
  After installing, run: hal0 connect cursor
```

## Implementation

### Files to create

| File | Purpose |
|------|---------|
| `src/hal0/cli/connect_commands.py` | Top-level `connect` Typer app |
| `src/hal0/cli/connectors/__init__.py` | Connector registry |
| `src/hal0/cli/connectors/_base.py` | BaseConnector ABC |
| `src/hal0/cli/connectors/claude_code.py` | Claude Code connector |
| `src/hal0/cli/connectors/openwebui.py` | OpenWebUI connector |

### Files to modify

| File | Change |
|------|--------|
| `src/hal0/cli/main.py` | Add `app.add_typer(connect_app, name="connect")` |
| `src/hal0/api/routes.py` (or equivalent) | Add `GET /api/mcp/servers` endpoint |
| `src/hal0/agents/hermes_provision.py` | Fix `_default_mcp_servers()` URLs + type (separate PR) |
| `templates/config.yaml.j2` | Add `type: http` to MCP server blocks (separate PR) |

### Post-provision hook

The `context_link` phase should generate `/etc/hal0/MCP-CLIENTS.md`
documenting the MCP connection contract. This serves as the
reference doc for clients not yet in the connector registry.

## Consequences

### Positive
- Zero-friction MCP wiring for new users
- Self-repairing config catches transport/URL bugs automatically
- Plugin registry makes adding connectors trivial
- API discovery makes `connect` forward-compatible with new MCP servers

### Negative
- Writing to Claude Code's `settings.json` is a coupling risk if
  Anthropic changes the schema. Mitigated by fallback to manual
  instructions.
- Each new connector requires hal0-side code. Mitigated by the
  simple BaseConnector interface (~4 methods per client).

### Neutral
- `disconnect` deferred to post-v1. Users can manually remove
  MCP server entries.
- No install automation. hal0 connects, doesn't install.
