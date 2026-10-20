# ADR-0032: Hermes installs from a pinned source checkout, editable, into a uv-built Python 3.14 venv

## Status

Proposed (2026-10-20). The operator chose option (a) on #2615 (2026-10-19): a
pinned source checkout plus an editable install on Python 3.14. This record
turns that choice into a design, and in two places it departs from the sketch
in that decision comment:

- The sketch had `_install_venv` build the new venv beside the old one and
  swap it into place. This record instead gives every build its own release
  directory and moves a symlink. A venv renamed after it is built keeps
  shebangs that point at the build directory, and an editable venv needs its
  checkout to move with it.
- The sketch had the unit's `ReadWritePaths` cover the checkout. The unit
  already grants `/var/lib/hal0`, so it does not change.

Merging this record accepts both departures, so it needs the operator's
sign-off. It replaces the install posture that ADR-0018 recorded: a
`python3.12 -m venv` followed by a `git+` requirement line. The
`VETTED_HERMES_REFS` freeze that ADR-0018 introduced stays. The implementation
on #2615 starts after this record merges and lands as the slices in decision
12.

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
   3.12 here: `PYTHON_MAX_EXCLUSIVE`, `_validate_hermes_python`,
   `_install_venv` (rebuilds any venv whose minor is not 3.12) and
   `resolve_hermes_python`, which persists its choice in
   `/etc/hal0/hermes-python.env`.

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

Facts about today's code that constrain the design:

- Four code paths build or modify the Hermes venv in place.
  - `_phase_install` (install, provision, reprovision, `--repair`) calls
    `install_venv` when the binary is missing, under `--repair`, or when the
    MCP-client probe fails.
  - `converge_hermes` (`components/hermes_arm.py`), which `hal0 update`
    reaches through the component registry, rebuilds when the stamp differs
    from the pin.
  - `repair_hermes_mcp_client` (`updater/updater.py`), also run by
    `hal0 update`, rebuilds when the MCP client cannot import.
  - `upgrade_hermes_runtime`, behind `hal0 agent upgrade hermes`, runs
    `pip install --upgrade` straight into the live venv, then
    `hermes config migrate`.
- Provisioning drops to the `hal0` user (§7.4, `_provision_hermes` and
  `_hermes_root_prelude` in `cli/agent_commands.py`). `hal0 update` runs
  inside `hal0-api` as `hal0`. The agent unit runs as `User=hal0` with
  `ReadWritePaths=… /var/lib/hal0`. The Hermes trees are therefore born
  `hal0:hal0` and the agent process can write them.
- `hal0-api` and the agent drop-in both load
  `EnvironmentFile=-/etc/hal0/hermes-python.env`, so every process systemd
  starts sees the persisted path as the variable `HAL0_HERMES_PYTHON`, the
  same name an operator uses to pin an interpreter. The resolvers cannot tell
  the two apart, and the file parser rejects anything but exactly one
  `HAL0_HERMES_PYTHON=` line.
- `hermes config migrate` runs against the shared `HERMES_HOME`, from
  `upgrade_hermes_runtime` and from provisioning.
- uv is optional today. `hermes-prereqs.sh` installs it through pipx only when
  no system Python 3.12 exists, and `_provision_python_via_uv` returns `None`
  when uv is absent or offline.

## Decision

1. **Option (a): a hal0-owned source checkout, installed editable.** hal0
   keeps a vetted commit of NousResearch/hermes-agent checked out in its own
   directory and installs it editable with
   `uv pip install -e "<checkout>[web,mcp]"` into a venv that `uv venv`
   creates on Python 3.14. uv is the build tool whichever interpreter is used
   (decision 9). This replaces the `python3.12 -m venv` plus `git+`
   requirement line.
2. **Why not upstream's `install.sh` or Docker image (option b).** Both hand
   interpreter and dependency control to upstream, which conflicts with the
   `VETTED_HERMES_REFS` freeze (a reviewed commit, not whatever the installer
   fetches) and with how the hal0 systemd unit, the wrapper and the plugins
   all reach one venv. Editable installs are an upstream-supported method, so
   option (a) is the smallest change that stays inside upstream's rules.
3. **The vetted-ref freeze stays, and the requirements format does not
   change.** `installer/agents/hermes/requirements.txt` keeps its single
   active line in today's format:
   `hermes-agent[web,mcp] @ git+https://github.com/NousResearch/hermes-agent.git@<40-hex sha>`.
   Only its consumer changes. The builder reads the URL, the extras and the
   ref from that line and checks the ref out, instead of handing the line to
   pip. `_hermes_requirement_line`, `hermes_pinned_ref` and
   `_hermes_version_pin` keep parsing it, so they and the
   `[tool.hal0.upstream-hermes]` record in `pyproject.toml` stay in lockstep
   under the existing contract test. The ref must be a full 40-hex commit in
   `VETTED_HERMES_REFS`; a tag, a branch or an unlisted commit is refused. The
   `>= HERMES_MIN_VERSION` floor path in `hermes_requirement_is_vetted` goes
   away, because a floor cannot name a commit to check out and package
   versions report `0.0.0`.
4. **Layout: one release directory per build, under a hal0-owned tree.**

   ```
   /var/lib/hal0/hermes/
     mirror.git/                 bare mirror of the upstream repo, fetched by sha
     releases/<sha12>-<UTC stamp>/
       src/                      git worktree of mirror.git, detached at the sha
       venv/                     built at this final path, editable against src/
       release.json              ref, interpreter, hal0 version; written last
     previous -> releases/<id>   the rollback target
     hold                        present after a rollback (decision 8)
   /var/lib/hal0/venvs/hermes -> /var/lib/hal0/hermes/releases/<id>/venv
   ```

   The path the unit, the wrapper and the gateway already use,
   `/var/lib/hal0/venvs/hermes`, becomes a symlink to the live release's
   `venv/`. A release without `release.json` is incomplete and is deleted the
   next time the builder runs. The release id includes a build time, so
   rebuilding the same ref (a repair) produces a new release directory rather
   than touching the live one.
5. **Ownership stays as §7.4 has it.** The tree is `hal0:hal0`, built by the
   `hal0` user, and writable by the agent process, as the venv is today. The
   root prelude adds `/var/lib/hal0/hermes` and `/var/lib/hal0/python`
   (`UV_PYTHON_INSTALL_DIR`) to the setgid `hal0:hal0` directories it creates
   before dropping, and creates them before it resolves the interpreter.
   `git` and `uv` run as `hal0`, with uv's cache at
   `/var/lib/hal0/.cache/uv` (`UV_CACHE_DIR`). `hal0-agent@.service` does not
   change. `hal0 doctor perms` adds `/var/lib/hal0/hermes` and
   `/var/lib/hal0/python` to its `hal0`-owned rows, and its existing
   `hermes venv` row keeps passing because it follows the symlink to a
   `hal0`-owned release. A host where `/var/lib/hal0/python` is still
   root-owned from an earlier root-run uv fetch gets the
   `hal0 doctor perms --fix` remedy from a failed build.
6. **One release builder, used by every build path.** `build_release(ref)`
   in `hermes_provision.py` fetches the sha into `mirror.git`, adds the
   worktree, builds the venv at its final path, verifies the release in place
   (it imports, `venv/bin/hermes --version` reports the ref, and the
   `mcp_wire` client probe passes), then writes `release.json`.
   `activate_release(id)` then switches the live release (decision 7). The
   four paths become callers:
   - `_phase_install`: builds and activates when no live release exists,
     under `--repair`, and when the MCP-client probe fails.
   - `converge_hermes`: builds and activates when the live ref differs from
     the pinned ref.
   - `repair_hermes_mcp_client`: builds and activates the pinned ref when the
     live release's MCP client cannot import. With `install=False` (the
     boot-time check) it still only diagnoses.
   - `hal0 agent upgrade hermes`: builds and activates the pinned ref, or the
     `--to=<sha>` ref, which must be in `VETTED_HERMES_REFS`. A version
     string or an `==X.Y.Z` spec is rejected. `upgrade_hermes_runtime` and
     its in-place `pip install --upgrade` are deleted. A root caller drops to
     `hal0` through `_provision_hermes`, like every other provisioning entry
     point.

   Repairing the live ref means building a fresh release of the same ref and
   activating it. Nothing writes into a release after `release.json` exists,
   and `_install_venv` and its in-place minor-version rebuild are deleted.
   `--offline` never builds. When a build is needed offline, the builder
   refuses and names the step that needs the network. A host that is already
   running keeps its live release.
7. **Activation is a symlink swap with a restart and an automatic flip back.**
   - Before the switch, the builder copies `$HERMES_HOME/config.yaml` into
     the outgoing release as `config.yaml.before-<new id>`.
   - The switch creates the new symlink at `venvs/.hermes.new` and
     `rename()`s it over `venvs/hermes`, so the path always resolves.
   - `previous` is then pointed at the outgoing release with the same
     rename.
   - The builder try-restarts `hal0-agent@hermes` and `hermes-gateway`
     through the existing seam verbs (`try-restart-agent`,
     `svc-try-restart`), so a running agent moves to the new release. A
     running agent that is not restarted keeps importing from its old
     release, which still exists.
   - Provisioning, including `hermes config migrate`, then runs against the
     new release, and the builder re-verifies through the symlink.
   - If the re-verify fails, the builder switches the symlink back, restarts
     both units again, and reports `activation_failed` with the
     `hal0 agent rollback hermes` and `--repair` remedies.
   - On success it writes the stamp from `release.json` and prunes
     (decision 10).

   The flip back restores code and venv only. `HERMES_HOME`, including a
   config that `hermes config migrate` moved to a newer schema, is not
   restored. The copied `config.yaml` is there for an operator to restore by
   hand.
8. **Rollback is `hal0 agent rollback hermes`.** It swaps the live symlink
   and `previous` (decision 7's rename), try-restarts both units,
   re-verifies, and creates `/var/lib/hal0/hermes/hold`. As in decision 7 it
   restores code and venv, not `HERMES_HOME`, and says so in its output.
   While `hold` exists, `converge_hermes` and `repair_hermes_mcp_client`
   report `held` instead of building, so a rollback survives `hal0 update`.
   `hal0 agent upgrade hermes` removes `hold` and converges to the pin. A
   rollback to a ref that is no longer pinned is a deliberate operator
   state, and `hal0 agent status hermes` shows it as held.
9. **Interpreter: 3.14, uv required, and hal0's own selection told apart from
   an operator's.**
   - `PYTHON_MIN`, `PYTHON_MAX_EXCLUSIVE`, `UV_PYTHON_FALLBACK` and the
     exact-minor check in `_validate_hermes_python` move to 3.14. A system
     `python3.14` wins. Otherwise uv installs a managed 3.14 under
     `/var/lib/hal0/python`.
   - uv is required. `hermes-prereqs.sh` runs `ensure_uv` unconditionally,
     not only on hosts without 3.12 as today, and its pipx install pins a uv
     version recorded in that script. An existing uv on `PATH` at or above
     that version is used as is. Without uv the build stops with the prereqs
     remedy.
   - A host with no system 3.14 needs the network to fetch one, as decision 6
     says for `--offline`.
   - The operator override moves to its own variable,
     `HAL0_HERMES_PYTHON_OVERRIDE`. `HAL0_HERMES_PYTHON` becomes a value only
     hal0 writes, whether it arrives from the file directly or through
     `EnvironmentFile=`. The resolver takes `HAL0_HERMES_PYTHON_OVERRIDE`
     first, from the environment and then from the file, and rejects one that
     fails validation, as today.
   - A `HAL0_HERMES_PYTHON` value that fails validation is discarded and
     re-probed. This covers the environment and the file, and both the new
     format and a legacy one-line file, which is the 3.12 path every existing
     install holds.
   - After re-probing, the resolver persists the new value when it can write
     the file. It also sets the value in its own environment, as the prelude
     does today, so later calls in the same process agree. When it cannot
     write the file (a build running as `hal0` against a root-owned file), it
     uses the re-probed interpreter for this build and leaves the file for the
     next root-run provision.
   - `_read_hermes_python_env` changes to accept the two keys, each at most
     once, and nothing else. `_persist_hermes_python` rewrites only the
     `HAL0_HERMES_PYTHON` line and keeps an operator's override line.
   - An operator who pinned an interpreter through `HAL0_HERMES_PYTHON` has
     to move it to the new variable. While they have not, a value that still
     validates is kept, and one that fails is replaced with a warning that
     names `HAL0_HERMES_PYTHON_OVERRIDE`. `docs/reference/env-vars.mdx`
     documents the new variable.
10. **Retention: the live release and `previous`, nothing else.** After a
    successful activation the builder deletes every other release, with
    `git worktree remove` for its `src/` and then `git worktree prune`, and
    runs `uv cache prune`. A re-upgrade to a ref that already has a release
    directory, such as an upgrade after a rollback, builds a fresh release;
    the builder never reuses an old one. Once no release's interpreter lives
    under `/var/lib/hal0/python/` at 3.12, the builder runs
    `uv python uninstall 3.12` there. The shared bare mirror keeps the second
    release's checkout to a worktree, not a second clone.
11. **The first migration stops the agent while the venv directory moves.**
    On an existing install, `venvs/hermes` is a real directory, the 3.12 venv
    built from the `git+` line.
    1. The builder builds and verifies the new release with the old venv
       still serving.
    2. It records whether `hal0-agent@hermes` and `hermes-gateway` are
       active, then stops both (`stop-agent`, `svc-stop`). A running process
       started with `sys.prefix` at that path, and after the move its
       `lib/python3.12/site-packages` would resolve into the new release.
    3. It `rename()`s the directory to `releases/legacy-<sha12>/venv` and
       writes that release's `release.json` with `legacy: true`, its ref
       taken from the stamp. The two paths are on one filesystem.
    4. It `rename()`s the new symlink into place and points `previous` at the
       legacy release.
    5. It starts whichever units were active, then continues with decision
       7's provisioning, re-verify and flip back.

    Between the two renames the path is missing for a moment. With both units
    stopped, only a `hermes` CLI call started in that moment can see it, and
    the wrapper fails loudly ("venv binary missing") rather than running the
    wrong code. The legacy release is the one exception to "a release is
    `src/` plus `venv/`": it has no `src/`, because the 3.12 venv was a
    non-editable install. It is a valid rollback target until the next
    successful activation prunes it, and decision 8's `hold` keeps it live
    across `hal0 update`. A build or migration failure before step 2 leaves
    the host exactly as it was.
12. **#2615 lands as five PRs, in order.** Each keeps main shippable.
    1. **Interpreter provenance.** Decision 9's override variable, the
       two-key file format and parser, re-probing a stale hal0-written value,
       and the env-vars doc. The target stays 3.12. Unit tests only. Size M.
    2. **Release layout and builder.** Decisions 4 to 7 and 11, with every
       build path in decision 6 rerouted in the same PR. A path left on
       `_install_venv` would write into a live release. The builder uses uv
       and still targets the current pin on 3.12. The v2026.7.20 commit
       predates the `setup.py` guard, so an editable install of it works.
       This PR migrates every host to the new layout without changing the
       interpreter. Before merge the operator runs the upgrade lane on the
       test fleet, and a human merges. Size L.
    3. **Rollback, hold and retention.** Decisions 8 and 10, plus the teardown
       and audit changes in decision 13. Size M.
    4. **Python 3.14.** Decision 9's interpreter move and unconditional uv in
       `hermes-prereqs.sh`. On a migrated host this is an ordinary new
       release on a different interpreter. If the v2026.7.20 commit does not
       install on 3.14, this PR lands together with PR 5. The upgrade lane
       runs again, and a human merges. Size S to M.
    5. **Pin bump.** A newer vetted ref, the re-vendored contract fixtures
       (decision 14), and the watched-surface review #2615 started. Size M.
13. **Teardown and audit cover the new tree.** `hal0 agent uninstall hermes`
    removes the `venvs/hermes` symlink and all of `/var/lib/hal0/hermes`. The
    driver's `_remove_venv` refuses a symlink today, so it would otherwise
    report success and leave every release behind. `installer/uninstall.sh`
    removes `${VAR_DIR}/hermes` next to `${VAR_DIR}/venvs`. The gateway
    unit's `ExecStart` must reference the `venvs/hermes` path, not a resolved
    release path, or a flip leaves the gateway on the old release. PR 2
    checks this and fixes it if needed.
14. **Contract tests are re-vendored at each pin bump.** The fixtures under
    `tests/fixtures/hermes/contracts/` and
    `tests/agents/hermes/test_contract_compatibility.py` are refreshed from
    the new ref. The 0.18.2 to 0.21.7 spot check on #2615 already names what
    will change: the `MemoryProvider` signature and annotation asserts
    (`unavailable_reason`, `recall_status`, `identity_signature`, `list` and
    `dict` annotations) and the frozen `RUN_ROUTES` tuple (new
    `POST /v1/runs/{run_id}/steer`). CI has no Hermes available, so those
    tests run against vendored fixtures, not a live install.

## Consequences

- hal0 can move past Hermes 0.19.0 again, and a pin bump becomes a routine
  vetted-ref change instead of a blocked one.
- hal0 owns one more `hal0`-owned tree, `/var/lib/hal0/hermes`. The agent
  unit's grants and the §7.4 privilege model do not change.
- uv becomes a hard prerequisite of the Hermes install. A host with no
  system Python 3.14 needs the network for the first build, and `--offline`
  can no longer install Hermes from scratch.
- The Hermes venv is on Python 3.14 while the rest of hal0 stays on its own
  interpreter. The two are independent.
- Every existing install goes through the layout migration once, with a short
  agent and gateway stop.
- At steady state the disk holds two releases (two venvs, two worktrees of
  one bare mirror), plus the uv cache. Nothing else is kept.
- A rollback is a symlink swap and a restart. It covers code and venv, not
  `HERMES_HOME`, and it holds the install until the next
  `hal0 agent upgrade hermes`.
- `HAL0_HERMES_PYTHON` stops being an operator knob. Operators who set it
  move to `HAL0_HERMES_PYTHON_OVERRIDE`.
- `--to=<version>` and the version-floor path stop working. `--to` takes a
  vetted sha.
- The version stamp is hal0's own record, written from `release.json`. A
  venv edited outside the builder can disagree with it, and status trusts the
  stamp.
- Pin bumps cost a contract-fixture refresh each time, as before.

## References

- #2615 (the implementation and the decision), #2566 (upstream drift watch),
  PR #2614 (moves the pin to 0.19.0, the ceiling until this lands), #2787
  (this record).
- NousResearch/hermes-agent d84e11af (#68217), `setup.py` guard; v2026.7.20
  `3ef6bbd2`; v0.21.7 `71d34bad`.
- `src/hal0/agents/hermes_provision.py` (`VETTED_HERMES_REFS`,
  `PYTHON_MAX_EXCLUSIVE`, `resolve_hermes_python`, `_ensure_supported_python`,
  `_read_hermes_python_env`, `_persist_hermes_python`, `_install_venv`,
  `_phase_install`, `upgrade_hermes_runtime`),
  `src/hal0/components/hermes_arm.py` (`converge_hermes`),
  `src/hal0/updater/updater.py` (`repair_hermes_mcp_client`),
  `src/hal0/cli/agent_commands.py` (`_hermes_root_prelude`,
  `_provision_hermes`), `src/hal0/agents/hermes/driver.py` (`_remove_venv`),
  `src/hal0/cli/doctor_commands.py` (`check_hermes_ownership`),
  `installer/agents/hermes-prereqs.sh`, `installer/wrappers/hal0-systemctl`,
  `installer/agents/hermes/requirements.txt`.
