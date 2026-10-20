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
14.

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
  when uv is absent or offline. `hermes-prereqs.sh` runs only from
  `install.sh`, never from `hal0 update`.
- Nothing serialises venv builds today. The only lock near this code is
  `hal0_config_file_lock` in `updater/updater.py`, which guards the config
  TOML.
- `hermes-gateway.service` is written by upstream's
  `hermes gateway install --system`, which `install.sh` runs as root. hal0
  does not write it. Its `ExecStart` comes from the running interpreter:
  - At `3ef6bbd2` it is `sys.prefix/bin/python -m hermes_cli.main … gateway run`,
    with `VIRTUAL_ENV` set to the same prefix (`hermes_cli/gateway.py`,
    `get_python_path`).
  - At `71d34bad` it is `sys.executable -I -c "…sys.path.insert(0, '<resolved checkout>')…"`
    (`hermes_cli/_launchers.py`, `runtime_command`), so the resolved checkout
    path is baked in too.

  hal0's own gateway drop-in cannot override `ExecStart`, because the
  `hal0-systemctl` drop-in allow-list rejects it (`test_gateway_rejects_execstart`).
- A uv console script execs the absolute path of the python in the venv it
  was built in. Python started through a symlinked venv path reports that
  symlink path as `sys.prefix`. Both were checked in scratch venvs on
  2026-10-20.
- Checked on 2026-10-20 in scratch venvs: `3ef6bbd2` (the current pin) installs
  editable with `uv pip install -e "<checkout>[web,mcp]"` on both Python 3.12
  and Python 3.14. `hermes --version` runs, and `openai`, `httpx`, `fastapi`,
  `pydantic` and `mcp.client.streamable_http` import.

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
     .lock                       flock for every builder operation (decision 6)
     mirror.git/                 bare mirror of the upstream repo, fetched by sha
     releases/<sha12>-<UTC stamp>/
       src/                      git worktree of mirror.git, detached at the sha
       venv/                     built at this final path, editable against src/
       release.json              ref, interpreter path and minor, hal0 version;
                                 written last
     current  -> releases/<id>   the live release; the only link a switch moves
     previous -> releases/<id>   the rollback target
     hold                        present after a rollback (decision 8)
   /var/lib/hal0/venvs/hermes -> /var/lib/hal0/hermes/current/venv
   ```

   The path the agent unit and the wrapper already use,
   `/var/lib/hal0/venvs/hermes`, becomes a fixed symlink through `current`,
   and only `current` moves. A release without `release.json` is incomplete.
   The release id includes a build time, so rebuilding the same ref (a
   repair) produces a new release directory rather than touching the live
   one.
5. **Ownership stays as §7.4 has it.** The tree is `hal0:hal0`, built by the
   `hal0` user, and writable by the agent process, as the venv is today. The
   root prelude adds `/var/lib/hal0/hermes` and `/var/lib/hal0/python`
   (`UV_PYTHON_INSTALL_DIR`) to the setgid `hal0:hal0` directories it creates
   before dropping, and creates them before it resolves the interpreter.
   `git` and `uv` run as `hal0`, with uv's cache at
   `/var/lib/hal0/.cache/uv` (`UV_CACHE_DIR`). `hal0-agent@.service` does not
   change. A host where `/var/lib/hal0/python` is still root-owned from an
   earlier root-run uv fetch gets the `hal0 doctor perms --fix` remedy from a
   failed build.
6. **One release builder, behind one lock, used by every build path.**
   - **The builder.** `build_release(ref)` in `hermes_provision.py`:
     1. fetches the sha into `mirror.git`;
     2. adds the worktree;
     3. builds the venv at its final path;
     4. verifies the release in place: it imports, `venv/bin/hermes --version`
        reports the ref, and the `mcp_wire` client probe passes;
     5. writes `release.json`.

     `activate_release(id)` then switches the live release (decision 7).
   - **The lock.** Every builder operation holds an exclusive `flock` on
     `/var/lib/hal0/hermes/.lock` from start to finish. That covers build,
     activation, the first migration, rollback and prune. Only a lock holder
     deletes an incomplete release, so a release without `release.json` seen
     under the lock was left by a builder that died, and it is removed.
     - CLI callers (`provision`, `upgrade`, `rollback`) wait for the lock, and
       print who holds it.
     - `converge_hermes` and `repair_hermes_mcp_client` do not wait. They
       report `busy`, and the next `hal0 update` retries.
   - **When a build is needed.** Three triggers apply. The builder targets
     the pinned ref unless a caller names another vetted one.
     - the live ref differs from the pinned ref;
     - `venvs/hermes` is a real directory (the legacy layout, decision 11);
     - the live `release.json` interpreter minor differs from the target minor
       (decision 9).
   - **The callers.**
     - `_phase_install`: builds and activates when no live release exists, on
       any trigger, under `--repair`, and when the MCP-client probe fails.
     - `converge_hermes`: builds and activates on any trigger.
     - `repair_hermes_mcp_client`: builds and activates when the live
       release's MCP client cannot import. With `install=False` (the
       boot-time check) it still only diagnoses.
     - `hal0 agent upgrade hermes`: builds and activates the pinned ref, or
       the `--to=<sha>` ref, which must be in `VETTED_HERMES_REFS`. A version
       string or an `==X.Y.Z` spec is rejected. `upgrade_hermes_runtime` and
       its in-place `pip install --upgrade` are deleted. A root caller drops to
       `hal0` through `_provision_hermes`, like every other provisioning entry
       point.

     `hold` changes what these callers build (decision 8).
   - **Repairs.** Repairing the live ref means building a fresh release of
     the same ref and activating it. Nothing writes into a release after
     `release.json` exists, and `_install_venv` and its in-place
     minor-version rebuild are deleted.
   - **Offline.** `--offline` never builds. When a build is needed offline,
     the builder refuses and names the step that needs the network. A host
     that is already running keeps its live release.
7. **Activation: switch, migrate, restart, verify, and flip back on failure.**
   1. Copy `$HERMES_HOME/config.yaml` into the outgoing release as
      `config.yaml.before-<new id>`.
   2. Create the new `current` link at a temporary name and `rename()` it
      over `current`, so `venvs/hermes` always resolves. Point `previous` at
      the outgoing release the same way.
   3. Run provisioning against the new release, including
      `hermes config migrate`. Units still running finish on their old
      release, because their console scripts exec that release's python by
      absolute path.
   4. Restart the units that were active before the switch, with the verbs
      `converge_hermes` uses today: `try-restart-agent` for the agent and
      `svc-restart` for the gateway.
   5. Re-verify. Decision 6's checks run through `venvs/hermes`, and every
      unit that was active before the switch must be active again after a
      settle period, with no new restart in it (`systemctl show` of
      `ActiveState` and `NRestarts`, which needs no privilege). A
      crash-looping unit therefore fails the re-verify.
   6. If the re-verify fails, switch `current` back, restore the copied
      `config.yaml`, restart the same units, and report `activation_failed`
      with the `hal0 agent rollback hermes` and `--repair` remedies.
   7. On success, write the stamp from `release.json` and prune
      (decision 10).

   The automatic flip back restores the config only because the copy was
   taken moments earlier. Apart from that, a flip back covers code and venv,
   not `HERMES_HOME`.
8. **Rollback is `hal0 agent rollback hermes`, and `hold` keeps it.**
   - The command swaps `current` and `previous` (decision 7's rename),
     restarts and re-verifies as in decision 7, and creates
     `/var/lib/hal0/hermes/hold`. It restores code and venv, not
     `HERMES_HOME`. The `config.yaml` copy is left for a manual restore, and
     the command says so in its output.
   - **While `hold` exists:**
     - `converge_hermes` reports `held` instead of building, for every
       trigger. When the pinned ref differs from the held ref, the
       `hal0 update` result carries a warning that names both refs.
     - `repair_hermes_mcp_client`, `--repair` and the MCP-probe-failure path
       in `_phase_install` rebuild the held ref, not the pin. A repair never
       moves a held host to a different ref.
     - `hal0 agent status hermes` shows the install as held.
   - **Clearing it.** Only `hal0 agent upgrade hermes` removes `hold`, and
     then it converges to the pin. Uninstall removes it with the tree.
9. **Interpreter: 3.14, uv shipped with hal0, and hal0's own selection told
   apart from an operator's.**
   - `PYTHON_MIN`, `PYTHON_MAX_EXCLUSIVE`, `UV_PYTHON_FALLBACK` and the
     exact-minor check in `_validate_hermes_python` move to 3.14. A system
     `python3.14` wins. Otherwise uv installs a managed 3.14 under
     `/var/lib/hal0/python`.
   - **uv comes from hal0's own venv.** It becomes a pinned dependency in
     `pyproject.toml` (`uv>=X,<Y`, with a ceiling like the other
     dependencies), and the builder runs the `uv` in hal0's own venv. Every
     hal0 install and every `hal0 update` therefore delivers uv, with no
     Hermes-specific prerequisite. A host with system 3.12 and no uv on
     `PATH` can build after a plain `hal0 update`. `hermes-prereqs.sh` would
     not help there, because only `install.sh` runs it. Its `ensure_uv` and
     the pipx fallback lookup in `_uv_available` are removed.
   - A host with no system 3.14 needs the network to fetch one, as decision 6
     says for `--offline`.
   - **The operator override moves to its own variable,
     `HAL0_HERMES_PYTHON_OVERRIDE`.** `HAL0_HERMES_PYTHON` becomes a value only
     hal0 writes, whether it arrives from the file directly or through
     `EnvironmentFile=`. The resolver takes `HAL0_HERMES_PYTHON_OVERRIDE`
     first, from the environment and then from the file, and rejects one that
     fails validation, as today.
   - **A `HAL0_HERMES_PYTHON` value that fails validation is discarded and
     re-probed.** This covers the environment and the file, and both the new
     format and a legacy one-line file, which is the 3.12 path every existing
     install holds.
   - **After re-probing.** The resolver persists the new value when it can
     write the file. It also sets the value in its own environment, as the
     prelude does today, so later calls in the same process agree. When it
     cannot write the file (a build running as `hal0` against a root-owned
     file), it uses the re-probed interpreter for this build and leaves the
     file for the next root-run provision.
   - **The file format.** `_read_hermes_python_env` changes to accept the two
     keys, each at most once, and nothing else. `_persist_hermes_python`
     rewrites only the `HAL0_HERMES_PYTHON` line and keeps an operator's
     override line.
   - **Operators who pinned an interpreter through `HAL0_HERMES_PYTHON`**
     have to move the pin to the new variable.
     - A value that still validates is kept. A legacy file carries no marker,
       so hal0 cannot tell a hand-set value from its own and cannot warn about
       it. The release note for PR 1 carries that warning instead.
     - A value that fails validation is replaced, with a warning that names
       `HAL0_HERMES_PYTHON_OVERRIDE`.
     - A value set in a unit drop-in rather than in the file is re-probed,
       and the warning is repeated, on every build that sees it.
     - `docs/reference/env-vars.mdx` documents the new variable.
10. **Retention: the live release and `previous`, nothing else.** After a
    successful activation, still under the lock, the builder:
    - deletes every other release, running `git worktree remove` on its
      `src/` and then `git worktree prune`;
    - runs `uv cache prune`;
    - runs `uv python uninstall 3.12` under `/var/lib/hal0/python` once no
      remaining release's `release.json` names a 3.12 interpreter there.

    A re-upgrade to a ref that already has a release directory, such as an
    upgrade after a rollback, builds a fresh release; the builder never reuses
    an old one. The shared bare mirror keeps the second release's checkout to
    a worktree, not a second clone.
11. **The first migration stops the agent while the venv directory moves.**
    On an existing install, `venvs/hermes` is a real directory, the 3.12 venv
    built from the `git+` line. Decision 6's layout trigger starts the
    migration on the next `hal0 update` or provision.
    1. The builder takes the lock, then builds and verifies the new release
       with the old venv still serving.
    2. It records whether `hal0-agent@hermes` and `hermes-gateway` are
       active, then stops both (`stop-agent`, `svc-stop`). A running process
       was started with `sys.prefix` at that path, and after the move its
       `lib/python3.12/site-packages` would resolve into the new release.
    3. It `rename()`s the directory to `releases/legacy-<sha12>/venv` (the
       two paths are on one filesystem). It writes that release's
       `release.json` with `legacy: true`, the ref from the stamp, and the
       interpreter from the venv's `pyvenv.cfg` (`home` and `version`).
    4. It creates `current` pointing at the new release and `previous`
       pointing at the legacy release, then `rename()`s the
       `venvs/hermes -> /var/lib/hal0/hermes/current/venv` symlink into
       place.
    5. It continues with decision 7 from step 3. Only the units that were
       active are started again.

    Between the two renames the path is missing for a moment. With both units
    stopped, only a `hermes` CLI call started in that moment can see it, and
    the wrapper fails loudly ("venv binary missing") rather than running the
    wrong code. A build or migration failure before step 2 leaves the host
    exactly as it was.

    **The legacy release** is the one exception to "a release is `src/` plus
    `venv/`": it has no `src/`, because the 3.12 venv was a non-editable
    install. It is still a valid rollback target. Its console scripts exec
    `/var/lib/hal0/venvs/hermes/bin/python`, the path it was built at. While
    legacy is live, that path resolves back into the legacy venv. While it is
    not, nothing runs it, because it is reachable only as `previous`. It stays
    until the next successful activation prunes it, and decision 8's `hold`
    keeps it live across `hal0 update`.
12. **The gateway unit is normalised once, as root, when it is generated.**
    No seam change is needed.
    - `install.sh` already runs `hermes gateway install --system` as root.
      Straight after it, still as root, it rewrites every
      `/var/lib/hal0/hermes/releases/<live id>/` prefix in the generated unit
      to `/var/lib/hal0/hermes/current/`, and runs `daemon-reload`. The
      prefixes are in the `ExecStart` interpreter, the baked checkout path,
      and `VIRTUAL_ENV`.
    - If any `releases/` path remains, `install.sh` warns and leaves the
      gateway disabled, the same posture it takes today when the generator
      fails.
    - After that the unit names only stable paths. A switch of `current` plus
      the restart in decision 7 moves the gateway, and `hal0 update` never
      has to touch the unit.
    - Existing hosts need nothing. Their unit was generated with the venv at
      `/var/lib/hal0/venvs/hermes`, which stays valid as a symlink.
    - PR 5 must check that a unit generated at `3ef6bbd2`
      (`python -m hermes_cli.main … gateway run`) still starts the new pin. If
      it does not, PR 5 adds the same normalised regeneration to the root
      prelude and names `sudo hal0 agent provision hermes` in its release
      note.
13. **Teardown and audit cover the new tree.**
    - `hal0 agent uninstall hermes` removes the `venvs/hermes` symlink and
      all of `/var/lib/hal0/hermes`. The driver's `_remove_venv` refuses a
      symlink today, so it would otherwise report success and leave every
      release behind.
    - `installer/uninstall.sh` removes `${VAR_DIR}/hermes` next to
      `${VAR_DIR}/venvs`.
    - `hal0 doctor perms` adds `/var/lib/hal0/hermes` and
      `/var/lib/hal0/python` to its `hal0`-owned rows. Its existing
      `hermes venv` row keeps passing, because it follows the symlinks to a
      `hal0`-owned release.
14. **#2615 lands as five PRs, in order, and each one ships on its own.**
    1. **Interpreter provenance.** Decision 9's override variable, the
       two-key file format and parser, re-probing a stale hal0-written value,
       the env-vars doc and the release note. The target stays 3.12. Unit
       tests only. Size M.
    2. **Release layout, builder and everything the symlink needs.**
       - Decisions 4 to 7 and 11 to 13, with every build path in decision 6
         rerouted in the same PR. A path left on `_install_venv` would write
         into a live release.
       - The lock, the layout trigger and uv shipped in hal0's venv.
       - The teardown and audit, because the symlink lands here.
       - The pin stays `3ef6bbd2` and the interpreter 3.12.

       The layout trigger migrates every host on its next `hal0 update`. The
       PR proves the editable install of `3ef6bbd2` on 3.12 in CI or a scratch
       venv before the operator runs the upgrade lane on the test fleet. A
       human merges. Size L.
    3. **Rollback, hold and retention.** Decisions 8 and 10. Until this PR,
       releases accumulate but nothing breaks. Size M.
    4. **Python 3.14.** Decision 9's interpreter move. The interpreter-minor
       trigger rebuilds every host on its next `hal0 update`, as an ordinary
       new release on the same pin. `3ef6bbd2` installs on 3.14 (Context), so
       this PR does not wait for PR 5. The upgrade lane runs again, and a
       human merges. Size S to M.
    5. **Pin bump.** A newer vetted ref, the re-vendored contract fixtures
       (decision 15), the gateway check in decision 12, and the
       watched-surface review #2615 started. Size M.
15. **Contract tests are re-vendored at each pin bump.** The fixtures under
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
  unit's grants, the `hal0-systemctl` allow-list and the §7.4 privilege model
  do not change.
- uv becomes a dependency of hal0 itself, so it adds to hal0's own install
  footprint and its dependency review.
- Network needs:
  - Building a release needs GitHub for the source and PyPI for the
    dependencies, as the `git+` line already did.
  - A host with no system Python 3.14 also needs the network to fetch one.
  - Air-gapped Hermes installs were never supported and still are not.
    `--offline` can no longer install Hermes from scratch.
- The Hermes venv is on Python 3.14 while the rest of hal0 stays on its own
  interpreter. The two are independent.
- Every existing install migrates on its first `hal0 update` after PR 2, with
  a short agent and gateway stop, and rebuilds once more after PR 4.
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
  `installer/agents/hermes-prereqs.sh`, `installer/wrappers/hal0-systemctl`
  (`test_gateway_rejects_execstart` in
  `tests/installer/test_systemctl_dropin_validation.py`), `installer/install.sh`
  (`hermes gateway install --system`), `installer/agents/hermes/requirements.txt`.
- Upstream unit generator: `hermes_cli/gateway.py` (`get_python_path`,
  `generate_systemd_unit`) at `3ef6bbd2`; `hermes_cli/_launchers.py`
  (`runtime_command`, `installation_command`) at `71d34bad`.
