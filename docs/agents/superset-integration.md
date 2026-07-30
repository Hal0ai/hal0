# Superset.sh: local dev environment

[Superset](https://docs.superset.sh/overview) (desktop app + CLI + MCP server)
is the workspace manager this team uses to run coding agents against hal0. It
sits **on top of** the existing tracker split — it doesn't replace it.

## Three-system model

- **GitHub Issues** — canonical backlog. Single source of truth; see
  `docs/agents/issue-tracker.md`. Create/triage here with `gh`.
- **Linear** (team "Hal0", workspace `thinmintdev`) — read-only mirror for
  higher-level views, cycles, and for kicking work off via its **Custom
  script** coding-tool. Never the place work is tracked exclusively.
- **Superset** — where an issue becomes a live workspace: a git worktree on
  the issue's branch with an agent already running.

Superset's own `tasks` object (CLI: `superset tasks list/create/update`; MCP:
`tasks_*`) is **Linear-backed** — its `--project`/`--cycle` flags take Linear
project/cycle IDs. Once the hal0 Superset project is linked to the Hal0
Linear team, `superset tasks list --assignee-me` and the Linear board surface
the same items.

## One-time setup (run on your Mac — not from a Cowork sandbox)

1. Install the Superset desktop app (macOS only): <https://docs.superset.sh/overview>.
2. Confirm `gh auth status` is already green — Superset shells out to `gh`.
3. `superset auth login` (you're on the PRO plan).
4. Register this repo as a project:
   ```sh
   superset projects create --name hal0 --local --import /path/to/your/hal0/checkout
   # fresh machine, no local checkout yet:
   superset projects create --name hal0 --local --clone https://github.com/Hal0ai/hal0.git --parent-dir ~/code
   ```
   Note the returned project id — every command below needs it.
5. `superset projects list` any time you forget the id.

## Wire Linear's "Work on issue" to Superset

`scripts/superset/` holds templates for your `~/.linear/` directory — see
`scripts/superset/README.md` for the copy/chmod/enable steps. Once installed,
**Work on issue → Custom script** on any Hal0 Linear issue creates a Superset
workspace on that issue's branch with Claude already working the prompt.

## Workspace lifecycle scripts (`.superset/`)

`.superset/config.json` tells Superset how to build, run, and dismantle a
workspace. Superset resolves each key from the user override
(`~/.superset/projects/<repo-path>/config.json`), then the worktree's own
`.superset/config.json`, then this committed one; a local
`.superset/config.local.json` merges `before`/`after` around it and is
gitignored.

| Key | Runs when | Script |
|---|---|---|
| `setup` | workspace created | `.superset/setup.sh` |
| `run` | Run button (restartable from the UI) | `.superset/run.sh` |
| `teardown` | workspace deleted | `.superset/teardown.sh` |

**`setup.sh`** — `uv sync --frozen --extra dev` (the lockfile path CI uses, so
a workspace can't skew from it), `npm ci` in `ui/`, the Playwright chromium
download, and `ui/.env` copied from `$SUPERSET_ROOT_PATH` if the primary
checkout has one. Warm, it takes a couple of seconds. Skip pieces with
`HAL0_SETUP_SKIP_UI=1` or `HAL0_SETUP_SKIP_PLAYWRIGHT=1`.

**`run.sh`** — assigns this workspace's ports and hands off to
`scripts/dev-bootstrap.sh`, which stays the single owner of "start hal0
locally". OpenWebUI is off by default (`HAL0_DEV_SKIP_OPENWEBUI=1`) because one
container per concurrent workspace is a lot of memory for something most tasks
never touch; set it to `0` to get it.

**`teardown.sh`** — stops this workspace's services and container, then removes
the rebuildable artifacts (`hal0-home`, `.venv`, `ui/node_modules`, caches) so
`git worktree remove` doesn't trip over untracked files. It **refuses to run**
if the worktree still holds uncommitted changes or commits that are on no
remote, which surfaces in Superset as an error toast with a **Delete Anyway**
button — a speed bump, not a lock. `HAL0_TEARDOWN_FORCE=1` skips the check;
`HAL0_TEARDOWN_KEEP_DEPS=1` keeps `.venv` and `node_modules`.

### Ports are derived per workspace

Several worktrees are alive at once, so a fixed `8080`/`5173`/`3001` means the
second workspace attaches to the first one's server and you test a branch you
never checked out. `.superset/ports.sh` hashes the workspace path into a
stable, distinct port per service — the same rule `ui/tests/e2e/port.ts`
already applies to the Playwright suite (#1399), in windows that don't overlap
it:

| Service | Window | Variable |
|---|---|---|
| hal0-api | 18000–18499 | `HAL0_PORT` |
| UI dev server | 6100–6599 | `UI_PORT` |
| OpenWebUI | 3300–3799 | `HAL0_OPENWEBUI_PORT` |

`HAL0_HOME` points at `<worktree>/hal0-home` (gitignored) and the OpenWebUI
container name carries the same hash, so nothing is shared between workspaces.
`VITE_API_TARGET` is exported to match the derived API port — a Vite config
file reads `process.env`, and `.env` files never reach it, so this has to be
set in the shell that runs `npm run dev`.

Setting any of those variables explicitly wins. `setup.sh` writes the
assignment to `.superset/workspace.env` (gitignored) for use in a shell:

```sh
source .superset/workspace.env   # or: source .superset/ports.sh
```

## MCP: let an agent drive Superset directly

The root `.mcp.json` registers the Superset v2 MCP server. Open this repo in
an interactive Claude Code session and authorize it (`/mcp`) to get
`tasks_*`, `workspaces_*`, and `automations_*` tools — list/triage
Linear-backed tasks, spin up workspaces, and manage automations without
leaving the chat. A non-interactive Cowork session can't complete the OAuth
handshake for this — do it from a terminal, or use an API key
(`sk_live_…`, from the desktop app's **Settings → API Keys**) in the
`Authorization` header per <https://docs.superset.sh/mcp-server>.

## Automations (scheduled agent runs)

`scripts/superset/automations/nightly-triage.md` is a ready-to-use prompt
body. Wire it up once the project id is known:

```sh
superset automations create \
  --name "Hal0 nightly triage" \
  --project <hal0-project-id> \
  --rrule "FREQ=DAILY;BYHOUR=7;BYMINUTE=0" \
  --prompt-file scripts/superset/automations/nightly-triage.md \
  --agent claude
```

It cross-checks open `needs-triage` GitHub issues against the Linear mirror
and flags `ready-for-agent` candidates, using the same vocabulary as
`docs/agents/triage-labels.md`.

## Keeping Linear labels in sync

`scripts/linear/setup-hal0-labels.sh` ensures the Hal0 Linear team carries the
same 5 canonical labels as GitHub (`docs/agents/triage-labels.md`). It
resolves the team by name ("Hal0") so it doesn't need the team key
hardcoded, and is idempotent — safe to re-run.

## What's blocked from a Cowork session

- **Linear API** needs OAuth (`plugin:productivity:linear`) — not authorized
  in this sandbox. `setup-hal0-labels.sh` is written and ready to go; run it
  once the connector is authorized (claude.ai connector settings, or `/mcp`
  in an interactive Claude Code session), or from any shell with
  `LINEAR_API_KEY` set to a personal API key.
- **No `gh` CLI / GitHub auth** in this sandbox — GitHub-side changes (issue
  creation, label sync) need a session where `gh` is authenticated.
- **Superset is a macOS desktop app + local daemon** — everything under
  "One-time setup" and "Wire Linear" runs on your machine, not in a sandbox.
