# Linear + Superset project tracking

Linear is the source of truth for what needs doing. [Superset](https://superset.sh) is the
execution layer that turns an issue into an isolated git worktree with a coding agent in it.

- **Linear workspace:** `thinmintdev`
- **Engineering project:** [Hal0](https://linear.app/thinmintdev/project/hal0-6d1e242fd961) —
  teams `HAL0` + `MINT`
- **Non-code chores:** [Ops & Admin](https://linear.app/thinmintdev/project/ops-and-admin-882619e2cef7) —
  team `MINT`
- **Superset org:** `thinMint's Team` · **project:** `hal0` (`1bc59c2e-5f50-40fd-9953-e540883c03e8`)

## Tracking conventions

| Label group | Cardinality | Meaning |
| --- | --- | --- |
| `Area/*` | exactly one | Subsystem, mirroring `owner_class` in `docs/rework/REWORK_BOARD.md` |
| `Checkpoint/R1`…`R5` | exactly one | Rework checkpoint, duplicating the milestone |
| `Flag/*` | at most one | `blocked`, `deferred`, `needs-real-hw` |
| `Ops/*` | exactly one | `billing`, `account-security`, `vendor` — Ops & Admin only |

`Area` is single-select **by design**: the board treats `owner_class` as a collision class, so two
issues sharing an Area must be serialized rather than worked in parallel. Linear's label groups
enforce single-select, which makes the rule mechanical instead of a convention people forget.

`Checkpoint/R*` looks redundant next to the milestone, and isn't: Superset's task schema carries
`labels`, `externalProjectName`, and `externalCycleName`, but **no milestone field**. The label is
what makes checkpoint grouping visible on the Superset side.

`Area` values: `SEC`, `MODEL`, `RUNNER`, `SLOT`, `INSTALL`, `HERMES`, `HTTP-API`, `UI`, `OBS`,
`DOCS`, `DEPLOY`, `BENCH`, `COMFY`, `INFRA`. `HTTP-API` carries the board's `API` owner_class — the
bare name `API` was already taken by an unrelated workspace-level label.

## Issue -> workspace bridge

`scripts/superset-linear.sh` is the bridge. Registered as a Linear **custom coding tool**, it:

1. reads `LINEAR_ISSUE_IDENTIFIER` and `LINEAR_ISSUE_BRANCH_NAME` from the environment Linear provides,
2. reuses an existing Superset workspace on that branch, or creates one forked from `main`,
3. spawns a coding agent primed with the issue title, URL, and description,
4. opens the workspace in the Superset desktop app.

### Register it in Linear

Linear -> Settings -> Features -> Custom coding tools -> add a local command:

```sh
/home/mint/hal0/scripts/superset-linear.sh
```

### Overrides

| Variable | Default | Purpose |
| --- | --- | --- |
| `SUPERSET_PROJECT_ID` | the `hal0` project | Target a different Superset project |
| `SUPERSET_AGENT` | `claude` | `codex`, `amp`, …, or `none` to skip the agent |
| `SUPERSET_BASE_BRANCH` | `main` | Branch to fork the issue branch from |
| `SUPERSET_API_KEY` | `~/.config/superset/api-key` | Auth; falls back to the CLI's OAuth login |
| `SUPERSET_NO_OPEN` | unset | Set to `1` to print the deep link instead of opening the app |
| `SUPERSET_BIN` | `~/.superset/bin/superset` | Override CLI discovery |

The API key lives at `~/.config/superset/api-key`, mode `600`, outside the repo. Never commit it.

### Try it without side effects

```sh
LINEAR_ISSUE_IDENTIFIER=MINT-68 \
LINEAR_ISSUE_BRANCH_NAME=thinmint/mint-68-hal0-1303-benchmarks-hardcode-devdriamdgpu-instead-of-using \
SUPERSET_AGENT=none SUPERSET_NO_OPEN=1 ./scripts/superset-linear.sh
```

## Sync direction

Superset polls Linear and mirrors issues as tasks — `externalProvider: "linear"`, plus
`externalId`, `externalKey`, `externalUrl`, `lastSyncedAt`, and `syncError`. Nothing in this repo
drives that sync; it is configured inside Superset.

Because the mirror keys on `externalId` (a stable UUID), renaming or renumbering an issue updates
the existing task rather than duplicating it. Moving an issue between Linear **teams** does change
its identifier and its `gitBranchName`, which orphans any worktree already created for the old
branch name — prefer adding a team to a project over moving issues between teams.

## Known gaps

- **Scheduled automations do not work yet.** `superset automations create` fails with `Not found`
  because the local host is unregistered with the organization: `superset status` reports a healthy
  daemon with a `hostId`, but `hostName` is `null` and `superset hosts list` returns `[]`. Name and
  register the host from the Superset desktop app, then re-check `superset hosts list` before
  creating automations.
- **No `blocked` / `deferred` workflow states.** The board legend uses `⛔ blocked` and
  `⏸ deferred`, but Linear only has Backlog / Todo / In Progress / In Review / Done / Canceled /
  Duplicate. The `Flag/*` labels stand in. Real states must be added in the Linear UI — there is no
  API for creating workflow states.
- **No cycles.** Both teams have zero cycles, so there is no velocity or throughput data for
  Superset to chart. Enable cycles in Linear team settings; there is no MCP tool for this.
- **`Area/API`.** Blocked by a pre-existing workspace-level `API` label; `HTTP-API` is used instead.
  Linear's MCP surface has no label-update tool, so regrouping the old label is a UI action.

## Race condition worth knowing

`superset workspaces create` can create the workspace record *and still exit non-zero*, reporting
`Workspace not found on host <id>` — it verifies against the host daemon before the record is
indexed there. The worktree does get created. `superset-linear.sh` treats a non-zero exit as
inconclusive and polls `workspaces list` for the branch instead of trusting the status code; `open`
retries for the same reason.
