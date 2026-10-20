# ADR-0032: Hermes installs from a pinned source checkout, editable, on uv-managed Python 3.14

## Status

Accepted (2026-10-19, decided by the operator on #2615). Replaces the install
posture that ADR-0018 recorded: a `python3.12 -m venv` followed by a `git+`
requirement line. The `VETTED_HERMES_REFS` freeze that ADR-0018 introduced
stays. The implementation PR on #2615 is the evidence for this record and
starts after this one merges.

ADR-0018 is cited by `pyproject.toml` (`[tool.hal0.upstream-hermes]`) and
`scripts/hermes-sdk-diff.sh`, but no `docs/adr/0018-*` file is present in
this tree or its history. This record therefore restates the posture it
replaces instead of linking to it.

## Context

hal0 builds the Hermes venv with `python3.12 -m venv`, then
`pip install -r installer/agents/hermes/requirements.txt`. That file holds
one line,
`hermes-agent[web,mcp] @ git+https://github.com/NousResearch/hermes-agent.git@<sha>`,
and the sha must be in `VETTED_HERMES_REFS`
(`src/hal0/agents/hermes_provision.py`). Upstream broke both halves:

1. **Wheel and sdist builds are banned.** NousResearch/hermes-agent d84e11af
   (#68217, 2026-07-22) added a `setup.py` guard that raises on
   `bdist_wheel` and `sdist` unless `HERMES_NIX_BUILD=1`. The first release
   carrying it is v2026.7.30 (0.19.1). A `git+` requirement builds a wheel,
   so it fails with `RuntimeError: Building wheels or sdists for
   hermes-agent is not supported.` Upstream supports `install.sh`, Docker,
   Nix, and editable installs from a source checkout.
2. **Python 3.14 only, in practice.** From v0.21.6 `requires-python` is
   `>=3.11,<3.15`, but every core dependency carries a
   `python_version >= '3.14'` marker (46 of them, for example
   `openai==2.24.0; python_version >= '3.14'`). On 3.12 the package installs
   without `openai`, `httpx`, `fastapi` or `pydantic`. hal0 hard-requires
   3.12 here: `PYTHON_MAX_EXCLUSIVE`, `_install_venv` (rebuilds any venv
   whose minor is not 3.12) and `resolve_hermes_python`, which persists its
   choice in `/etc/hal0/hermes-python.env`.

Upstream also reports the package version as `0.0.0` from v0.21.6, so
nothing that reads the version from package metadata still works.

Checked on 2026-10-14 in scratch venvs (#2615): v0.21.7 on 3.12 through the
`git+` line fails with the guard error; v0.21.7 as an editable install from a
local checkout on uv-managed 3.14 works, `hermes --version` reports the
commit, and the `web` and `mcp` extras resolve. `uv pip install -e "git+..."`
is rejected ("Editable must refer to a local directory"), so a local checkout
is required. v2026.7.20 (0.19.0, `3ef6bbd2`) still installs the old way; it
is the pin hal0 holds until the implementation lands (PR #2614). Staying on
it leaves the drift watch (#2566) unclosable by a pin bump and keeps
upstream fixes after 2026-07-20 out of hal0.

## Decision

1. **Option (a): a hal0-owned source checkout, installed editable.** hal0
   keeps a vetted commit of NousResearch/hermes-agent checked out in its own
   directory and runs `uv pip install -e "<checkout>[web,mcp]"` into a
   uv-managed Python 3.14 venv. This replaces the `python3.12 -m venv` plus
   `git+` requirement line.
2. **Why not upstream's `install.sh` or Docker image (option b).** Both hand
   interpreter and dependency control to upstream, which conflicts with the
   `VETTED_HERMES_REFS` freeze (a reviewed commit, not whatever the installer
   fetches) and with how the hal0 systemd unit, the wrapper and the plugins
   all reach one venv. Editable installs are an upstream-supported method, so
   option (a) is the smallest change that stays inside upstream's rules.
3. **The vetted-ref freeze stays, and applies to the checkout.** hal0
   checks out only a commit in `VETTED_HERMES_REFS`, detached, and refuses a
   branch or an unlisted ref as it refuses an unlisted `git+` pin today. One
   thing narrows: `hermes_requirement_is_vetted` also accepts a
   `>= HERMES_MIN_VERSION` floor today. That path goes away, because a floor
   cannot name a commit to check out and package versions report `0.0.0`.
   `requirements.txt` keeps the pinned ref as data, but its format changes
   from a `git+` line to what the checkout step can consume, so
   `_hermes_requirement_line`, `hermes_pinned_ref` and `_hermes_version_pin`
   get a parser for the new format.
4. **Layout: one checkout and one venv per candidate ref.** Each vetted ref
   gets its own release directory under `/var/lib/hal0/hermes/releases/`,
   holding `src/` (the checkout at that ref) and `venv/` (built at that final
   path, editable against that `src/`). The path the unit and the wrapper
   already use, `/var/lib/hal0/venvs/hermes`, becomes a symlink to the live
   release's `venv/`. The provisioner (root, via the CLI) owns the tree and
   leaves it readable by the hal0 service user. The agent process only
   imports from it, so it needs read access, which `ProtectSystem=strict`
   already gives; `ReadWritePaths` in `hal0-agent@.service` already lists
   `/var/lib/hal0` and does not change. Nothing outside the provisioner
   writes to the tree.
5. **`hal0 agent upgrade hermes` installs a new release beside the live
   one.** An upgrade clones or fetches into a new release's `src/`, checks out
   the new vetted ref there, builds `venv/` there, and never touches the live
   release's checkout or venv. It never follows a branch. Moving to a new ref
   is a code change that adds the ref to `VETTED_HERMES_REFS`, as today.
   `--to=<version>` (`upgrade_hermes_runtime(version=...)`) changes meaning:
   it accepts only a ref in `VETTED_HERMES_REFS`, and a bare version string
   or an `==X.Y.Z` spec is rejected.
6. **The version stamp comes from the recorded ref.** The stamp is not read
   from `importlib.metadata`, which reports `0.0.0`. Today it already comes
   from the `requirements.txt` pin (`_hermes_version_pin`,
   `hermes_arm.py`); the change is the parser for the new requirements format
   and recording the installed ref in the release directory. Status and drift
   keep comparing the recorded ref with the single pinned ref from
   `requirements.txt`, as `converge_hermes` does today
   (`components/hermes_arm.py`), so a host on an older ref that is still in
   `VETTED_HERMES_REFS` reads as stale, not converged.
   `VETTED_HERMES_REFS` gates what may be installed; it is not the drift
   reference.
7. **Migration and swap are one unit, done by symlink.** The 3.14 venv is
   built at its final release path, so console-script shebangs and the
   editable `.pth` point at paths that survive the swap (renaming a venv built
   elsewhere leaves every `bin/` entry point pointing at the deleted build
   directory; `_retarget_scripts` in `memory/engine_upgrade.py` exists for
   that reason). Steps: build the release, verify it in place (import,
   `hermes --version`, the existing `mcp_wire` check), then flip the
   `venvs/hermes` symlink to it atomically and re-verify through the symlink.
   The previous release (checkout and venv together) stays as the rollback
   target: rolling back is flipping the symlink back. The first migration
   moves the existing 3.12 venv directory into `releases/` as the rollback
   release, then creates the symlink. A release older than the previous one
   is removed at the next successful upgrade. If verification fails, the
   symlink is not flipped and the live release is untouched, source included.
8. **Interpreter resolution targets 3.14 only, and replaces stale
   selections.** `PYTHON_MAX_EXCLUSIVE`, the exact-minor check and
   `resolve_hermes_python` move from 3.12 to 3.14, with uv downloading a
   managed 3.14 when no system interpreter qualifies. Today a persisted path
   in `/etc/hal0/hermes-python.env` is read and validated before any probe,
   and the root prelude in `cli/agent_commands.py` raises on a failure before
   it persists a replacement, so an upgraded host holding a 3.12 path would
   never start the migration. The two sources are therefore treated
   differently. A selection hal0 wrote itself (the file records provenance)
   that no longer validates is discarded and re-probed, then overwritten. An
   explicit `HAL0_HERMES_PYTHON`, or a file edited by hand, that fails
   validation is still rejected with the error.
9. **Contract tests are re-vendored at each pin bump.** The fixtures under
   `tests/fixtures/hermes/contracts/` and
   `tests/agents/hermes/test_contract_compatibility.py` are refreshed from the
   new ref. The 0.18.2 to 0.21.7 spot check on #2615 already names what will
   change: the `MemoryProvider` signature and annotation asserts
   (`unavailable_reason`, `recall_status`, `identity_signature`, `list` and
   `dict` annotations) and the frozen `RUN_ROUTES` tuple (new
   `POST /v1/runs/{run_id}/steer`). CI has no Hermes available, so those
   tests run against vendored fixtures, not a live install.

## Consequences

- hal0 can move past Hermes 0.19.0 again, and a pin bump becomes a routine
  vetted-ref change instead of a blocked one.
- hal0 owns one more directory tree. The unit's grants do not change, and the
  agent process only reads it.
- The Hermes venv is on Python 3.14 while the rest of hal0 stays on its own
  interpreter. The two are independent.
- Every existing install goes through the migration once. The previous
  release (checkout and venv) costs disk until the next successful upgrade.
- A release directory per ref means a rollback is a symlink flip, at the cost
  of two checkouts and two venvs on disk at steady state.
- `--to=<version>` and the version-floor path stop working.
- The version stamp is hal0's own record. A venv edited outside the
  provisioner can disagree with it, and status trusts the stamp.
- Pin bumps cost a contract-fixture refresh each time, as before.

## References

- #2615 (the implementation and the decision), #2566 (upstream drift watch),
  PR #2614 (moves the pin to 0.19.0, the ceiling until this lands), #2787
  (this record).
- NousResearch/hermes-agent d84e11af (#68217), `setup.py` guard; v2026.7.20
  `3ef6bbd2`; v0.21.7 `71d34bad`.
- `src/hal0/agents/hermes_provision.py` (`VETTED_HERMES_REFS`,
  `PYTHON_MAX_EXCLUSIVE`, `resolve_hermes_python`, `_install_venv`),
  `src/hal0/components/hermes_arm.py`, `installer/agents/hermes/requirements.txt`.
