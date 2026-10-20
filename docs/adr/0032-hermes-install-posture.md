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
3. **The vetted-ref freeze stays, and applies to the checkout.** hal0 checks
   out only a commit in `VETTED_HERMES_REFS`, detached, and refuses a branch
   or an unlisted ref exactly as it refuses an unlisted `git+` pin today.
   `requirements.txt` keeps the pinned ref as data, but its format changes
   from a `git+` line to what the checkout step can consume, so
   `_hermes_requirement_line`, `hermes_pinned_ref` and `_hermes_version_pin`
   change with it.
4. **Checkout location and ownership.** The checkout lives under
   `/var/lib/hal0/` beside the venv (`/var/lib/hal0/venvs/hermes`), owned by
   the hal0 service user. The unit's `ReadWritePaths` gains it, because an
   editable install imports from the checkout at run time. Nothing outside
   the provisioner writes to it.
5. **`hal0 agent upgrade hermes` moves the checkout.** An upgrade fetches
   into the checkout, checks out the new vetted ref, and reinstalls editable
   into a venv built beside the live one (decision 7). It never follows a
   branch. Moving to a new ref is a code change that adds the ref to
   `VETTED_HERMES_REFS`, as today.
6. **The version stamp no longer comes from package metadata.** The stamp
   read by `hermes_arm` (`hermes_version` in the provisioner's state file, and
   the `venv.pin` stamp) is written from the vetted ref the provisioner
   installed, not from `importlib.metadata`, which reports `0.0.0`. Status
   and drift checks compare that recorded ref with `VETTED_HERMES_REFS`.
7. **Migration of existing 3.12 venvs.** Build the 3.14 venv beside the
   old one, install editable into it, verify it (import, `hermes --version`,
   the existing `mcp_wire` check), then swap. Keep the old 3.12 venv in place
   as the rollback target. Remove it at the next successful upgrade. If the
   new venv fails verification, the old one is never touched.
8. **Interpreter resolution targets 3.14 only.** `PYTHON_MAX_EXCLUSIVE`, the
   exact-minor check and `resolve_hermes_python` move from 3.12 to 3.14, with
   uv downloading a managed 3.14 when no system interpreter qualifies. The
   probe result keeps being persisted in `/etc/hal0/hermes-python.env`.
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
- hal0 owns one more directory and its permissions, and the unit grants write
  access to it.
- The Hermes venv is on Python 3.14 while the rest of hal0 stays on its own
  interpreter. The two are independent.
- Every existing install goes through the venv migration once. The old venv
  costs disk until the next successful upgrade.
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
