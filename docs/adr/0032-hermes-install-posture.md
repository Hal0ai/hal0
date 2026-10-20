# ADR-0032: Hermes installs from a pinned source checkout, editable, into a uv-built Python 3.14 venv

## Status

Proposed (2026-10-20). The operator chose option (a) on #2615 (2026-10-19): a
pinned source checkout plus an editable install on Python 3.14. This record
turns that choice into a design. On 2026-10-20 the operator cut two pieces
from it: the manual rollback command with its `hold` marker (deferred to
#2805, decision 8) and a rename of the `HAL0_HERMES_PYTHON` variable
(decision 9). The implementation on #2615 starts after this record merges and
lands as the four PRs in decision 14.

Merging this record accepts every departure from the sketch in that
decision comment, so it needs the operator's sign-off. After the cuts these
departures remain:

1. **Release directories behind a `current` symlink, not build beside and
   swap.** The sketch had `_install_venv` build the new venv beside the old
   one and swap it into place. A venv renamed after it is built keeps
   shebangs that point at the build directory, and an editable venv needs its
   checkout to move with it. So every build gets its own release directory
   and only a symlink moves (decision 4). Upgrades fetch into a shared bare
   mirror and add one worktree per release, rather than updating one
   checkout in place.
2. **`ReadWritePaths` does not change.** The sketch had the unit's
   `ReadWritePaths` cover the checkout. The unit already grants
   `/var/lib/hal0`.
3. **uv becomes a dependency of hal0 itself.** The sketch said "uv-managed
   3.14" and nothing about where uv comes from. It is now a pinned dependency
   in `pyproject.toml`, and a system `python3.14` is preferred over a
   uv-managed one (decision 9). `--offline` can no longer install Hermes.
4. **`hal0 agent upgrade hermes --to` takes only a vetted sha.** A version
   string, an `==X.Y.Z` spec and the `>= HERMES_MIN_VERSION` floor are
   rejected, and `upgrade_hermes_runtime` is deleted (decisions 3 and 6).
   This is a breaking CLI change.
5. **hal0 post-edits the gateway unit that upstream generates.**
   `install.sh` and `hal0 agent install hermes` both run upstream's
   `hermes gateway install --system` as root, and one shared helper then
   rewrites the release path baked into the unit (decision 12).
6. **Every activation stops the agent and the gateway briefly.** The units
   are stopped across the switch and started again (decision 7), and every
   existing host migrates on its next `hal0 update` after PR 2 (decision 11).
7. **Only an automatic rollback.** The sketch said to keep the old venv for
   rollback. The old release is kept as `previous`, and a failed activation
   flips back to it by itself. There is no manual rollback command, so a
   manual rollback lasts only until the next `hal0 update` (decision 8,
   #2805).
8. **An invalid `HAL0_HERMES_PYTHON` is discarded, not refused.** Today a
   value that fails validation stops the build. It is now discarded with a
   warning and the interpreter is re-probed (decision 9).
9. **Four PRs instead of one L PR plus a pin bump.** Python 3.14 is split
   out from the layout work (decision 14). PRs 2 and 3 need the upgrade lane
   on the test fleet and a human merge.

`pyproject.toml` (`[tool.hal0.upstream-hermes]`) and
`scripts/hermes-sdk-diff.sh` cite an ADR-0018, but no `docs/adr/0018-*` file
is present in this tree or its history. This record therefore restates the
install posture it replaces (a `python3.12 -m venv` followed by a `git+`
requirement line) instead of linking to it.

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
  the two apart. The file parser rejects anything but exactly one
  `HAL0_HERMES_PYTHON=` line, and a value that fails validation stops the
  build.
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
  `hermes gateway install --system`. hal0 runs it as root in two places:
  `install.sh`, and `_install_hermes_gateway` in `cli/agent_commands.py`,
  which the default `--gateway` path of `hal0 agent install hermes` reaches
  (the deferred install after `HAL0_SKIP_HERMES=1`, #1102). hal0 does not
  write the unit, and the generator starts it at once. Its `ExecStart` comes
  from the running interpreter:
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
  2026-10-20. So a process started through a path that a switch moves (the
  gateway's `ExecStart`, or any console script of the legacy venv in
  decision 11) resolves its later imports into whatever release the link
  names by then.
- Checked on 2026-10-20 in scratch venvs: `3ef6bbd2` (the current pin) installs
  editable with `uv pip install -e "<checkout>[web,mcp]"` on both Python 3.12
  and Python 3.14. `hermes --version` runs (whether it prints the commit at
  that ref was not checked), and `openai`, `httpx`, `fastapi`, `pydantic` and
  `mcp.client.streamable_http` import.
- hal0's own updater already installs into a release directory behind a
  `current` symlink and swaps the link (`apply()` in `updater/updater.py`,
  as the `_reinstall_into_venv` docstring describes). Decision 4 follows that pattern.

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
     previous -> releases/<id>   the flip-back target (decision 7)
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
     4. verifies the release in place:
        - `git -C src rev-parse HEAD` equals the ref;
        - the package imports;
        - `venv/bin/hermes --version` exits 0. This is a smoke test only.
          Its output is not compared with the ref, because at `3ef6bbd2` it
          may print a package version rather than the commit;
        - the `mcp_wire` client probe passes;
     5. writes `release.json`.

     `activate_release(id)` then switches the live release (decision 7).
   - **The lock.** Every builder operation holds an exclusive `flock` on
     `/var/lib/hal0/hermes/.lock` from start to finish. That covers build,
     activation, the first migration and prune.
     - Once it holds the lock, the holder truncates the lock file and writes
       its pid, its caller name and its start time into it. `flock` cannot
       name its holder, so this record is how a waiter does. The record is
       advisory: a holder that dies releases the lock, and the next holder
       overwrites the record.
     - Only a lock holder deletes an incomplete release, so a release without
       `release.json` seen under the lock was left by a builder that died,
       and it is removed.
     - CLI callers (`hal0 agent install`, `reprovision` and `upgrade`) wait
       for the lock. While they wait they print the recorded pid, caller and
       start time.
     - `converge_hermes` and `repair_hermes_mcp_client` do not wait, and the
       next `hal0 update` retries them. `converge_hermes` returns the
       component status `busy` with the recorded pid and logs
       `components.hermes_busy` as a warning. The component runner records
       it in `components.json` like every other arm result. `busy` is not a
       failure status, so the component row derives `pending` (the live ref
       still differs from the pin) and never `build_failed`, and
       `hal0 update` does not fail: the component pass is non-fatal and the
       update result does not carry arm results. `repair_hermes_mcp_client`
       returns `False` and logs the same warning, as it does for any repair
       that does not run.
   - **When a build is needed.** Three triggers apply. The builder targets
     the pinned ref unless a caller names another vetted one.
     - the live ref differs from the pinned ref;
     - the legacy layout (decision 11): `venvs/hermes` is a real directory,
       or the live release is the legacy one, which a flip-back during the
       first migration leaves live;
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
       point. A ref other than the pin lasts only until the next
       `hal0 update`, which converges back to the pin (decision 8).
   - **Repairs.** Repairing the live ref means building a fresh release of
     the same ref and activating it. Nothing writes into a release after
     `release.json` exists, and `_install_venv` and its in-place
     minor-version rebuild are deleted.
   - **Offline.** `--offline` never builds. When a build is needed offline,
     the builder refuses and names the step that needs the network. A host
     that is already running keeps its live release.
7. **Activation: stop, switch, migrate, start, verify, and flip back on
   failure.** The units are stopped across the switch. The gateway's
   `ExecStart` runs Python through `current` (decision 12), so a gateway
   still running when `current` moves resolves its later imports into the
   new release; on PR 3's 3.12-to-3.14 rebuild its `lib/python3.12` vanishes
   under it. Running `hermes config migrate` from the new release's own path
   before the switch would shorten that window but not close it, since the
   switch and the restart are still two steps apart. Stopping closes it, and
   the restart this replaces already took the units down, so the only added
   downtime is the time `config migrate` takes.
   1. Record whether `hal0-agent@hermes` and `hermes-gateway` are active
      (`systemctl show -p ActiveState`, which needs no privilege). These are
      "the recorded units" for the rest of this decision.
   2. Stop the recorded units: `stop-agent hermes` and
      `svc-stop hermes-gateway` through `hal0-systemctl` when running as
      `hal0`, or `systemctl stop` directly as root, the same split
      `converge_hermes` makes today.
   3. Copy `$HERMES_HOME/config.yaml` into the outgoing release as
      `config.yaml.before-<new id>`.
   4. Create the new `current` link at a temporary name and `rename()` it
      over `current`, so `venvs/hermes` always resolves. Point `previous` at
      the outgoing release the same way.
   5. Run provisioning against the new release, including
      `hermes config migrate`. Nothing from the old release is running.
   6. Start the recorded units with `start-agent hermes` and
      `svc-start hermes-gateway` (or `systemctl start` as root). Both start a
      stopped unit. The `try-restart-agent` verb `converge_hermes` uses
      today would not: it is a no-op on a stopped unit.
   7. Re-verify. Decision 6's checks run through `venvs/hermes`, and every
      recorded unit must be active after a settle period, with no new restart
      in it (`systemctl show` of `ActiveState` and `NRestarts`). A
      crash-looping unit therefore fails the re-verify.
   8. If the re-verify fails, stop the recorded units, switch `current` and
      `previous` back, restore the copied `config.yaml`, start the recorded
      units with the step-6 verbs, and report `activation_failed` with the
      `sudo hal0 agent reprovision hermes --repair` remedy.
      `activation_failed` joins the component failure statuses, so the
      component row shows it until a later activation succeeds.
   9. On success, write the stamp from `release.json` and prune
      (decision 10).

   The automatic flip back restores the config only because the copy was
   taken moments earlier. Apart from that, a flip back covers code and venv,
   not `HERMES_HOME`.
8. **No manual rollback command; the deferred design is #2805.** `previous`
   exists only as decision 7's flip-back target and as the second release
   decision 10 keeps. hal0 adds no `hal0 agent rollback hermes` command and
   no `hold` marker, so nothing freezes a host against `hal0 update`.
   - **The cost.** A manual rollback lasts only until the next `hal0 update`.
     An operator who finds a regression after an activation passed its
     re-verify can swap `current` and `previous` by hand and restart the
     units, or run `hal0 agent upgrade hermes --to=<older vetted sha>`.
     Either way, the next `hal0 update` sees the live ref differ from the pin
     and builds the pin again. The lasting fix is a hal0 patch that reverts
     the pin.
   - #2805 tracks the command and the hold, with the caller rules they need.
9. **Interpreter: 3.14, uv shipped with hal0, and a stale value re-probed.**
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
   - **`HAL0_HERMES_PYTHON` keeps its name and its file format.** It stays
     both the value hal0 persists and the operator's pin.
   - **A value that fails validation is discarded with a warning, then the
     interpreter is re-probed.** This applies to a value from the
     environment, a value from the file, and a file the parser rejects. Every
     existing install holds a 3.12 path, so after PR 3 every host takes this
     path once. The warning names the discarded value, why it failed, and the
     interpreter chosen instead.
   - **After re-probing.** The resolver persists the new value when it can
     write the file. It also sets the value in its own environment, as the
     prelude does today, so later calls in the same process agree. When it
     cannot write the file (a build running as `hal0` against a root-owned
     file), it uses the re-probed interpreter for this build and leaves the
     file for the next root-run provision.
   - **The cost.** hal0 cannot tell its own stale value from an operator's
     bad pin, so a bad pin no longer stops the build. The operator learns
     about it from the warning, not from a failure. A pin set in a unit
     drop-in rather than the file is re-probed, and the warning repeated, on
     every build that sees it.
10. **Retention: the live release and `previous`, nothing else.** After a
    successful activation, still under the lock, the builder:
    - deletes every other release, running `git worktree remove` on its
      `src/` and then `git worktree prune`;
    - runs `uv cache prune`;
    - runs `uv python uninstall 3.12` under `/var/lib/hal0/python` once no
      remaining release's `release.json` names a 3.12 interpreter there.

    **Backstop for the gateway.** Before deleting a release, the builder
    reads `/etc/systemd/system/hermes-gateway.service`, which needs no
    privilege. It keeps any release whose `releases/<id>/` path the unit
    still names, and the activation result carries a warning with the
    `sudo hal0 agent install hermes` remedy, which regenerates and
    normalises the unit (decision 12). This covers a unit decision 12 never
    saw, for example one an operator regenerated by hand.

    A re-upgrade to a ref that already has a release directory builds a
    fresh release; the builder never reuses an old one. The shared bare
    mirror keeps the second release's checkout to a worktree, not a second
    clone.
11. **The first migration moves the venv directory with both units
    stopped.** On an existing install, `venvs/hermes` is a real directory, the
    3.12 venv built from the `git+` line. Decision 6's layout trigger starts
    the migration on the next `hal0 update` or provision.
    1. The builder takes the lock, then builds and verifies the new release
       with the old venv still serving.
    2. It runs decision 7 from step 1, so the units are recorded and stopped.
       A running process was started with `sys.prefix` at the old path, and
       after the move its `lib/python3.12/site-packages` would resolve into
       the new release.
    3. Between decision 7's steps 2 and 3, it `rename()`s the directory to
       `releases/legacy-<sha12>/venv` (the two paths are on one filesystem).
       It writes that release's `release.json` with `legacy: true`, the ref
       from the stamp, and the interpreter from the venv's `pyvenv.cfg`
       (`home` and `version`).
    4. Decision 7's step 3 then copies `config.yaml` into the legacy release,
       and its step 4 creates `current` pointing at the new release and
       `previous` pointing at the legacy release. The builder then
       `rename()`s the `venvs/hermes -> /var/lib/hal0/hermes/current/venv`
       symlink into place.
    5. Decision 7 continues from step 5. Only the recorded units are started
       again, with `start-agent` and `svc-start`, and a failed re-verify
       flips back to the legacy release with the config restored.

    Between the two renames the path is missing for a moment. With both units
    stopped, only a `hermes` CLI call started in that moment can see it, and
    the wrapper fails loudly ("venv binary missing") rather than running the
    wrong code. A build failure before decision 7's step 2 leaves the host
    exactly as it was.

    **The legacy release** is the one exception to "a release is `src/` plus
    `venv/`": it has no `src/`, because the 3.12 venv was a non-editable
    install. It is still a valid flip-back target. Its console scripts exec
    `/var/lib/hal0/venvs/hermes/bin/python`, the path it was built at, which
    resolves back into the legacy venv while legacy is live. That path moves
    on a switch, which is a second reason decision 7 stops the units. While
    legacy is not live, nothing runs it, because it is reachable only as
    `previous`. A legacy release left live by a flip-back triggers a fresh
    migration attempt on the next `hal0 update` (decision 6). It stays until
    the next successful activation prunes it.
12. **The gateway unit is normalised by one helper, as root, after every
    generator run.** No seam change is needed.
    - **The helper.** `normalise_gateway_unit()` in a new
      `hal0.agents.hermes_gateway_unit` module. `_install_hermes_gateway`
      imports it, and `install.sh` runs it as
      `"${VENV_DIR}/bin/python" -m hal0.agents.hermes_gateway_unit`, the
      pattern `install.sh` already uses for other hal0 modules. As root, it:
      1. rewrites every `/var/lib/hal0/hermes/releases/<id>/` prefix in
         `/etc/systemd/system/hermes-gateway.service` to
         `/var/lib/hal0/hermes/current/`. The prefixes are in the `ExecStart`
         interpreter, the baked checkout path, and `VIRTUAL_ENV`;
      2. runs `daemon-reload`;
      3. runs `systemctl try-restart hermes-gateway`. The generator already
         started the unit from the un-normalised path, and the callers'
         `enable --now` does not restart an active unit, so without this the
         gateway keeps running from `releases/<id>` until its next restart.
    - **Its callers.** Both root runs of the generator call it straight
      after the generator and before their `enable --now`: `install.sh`, and
      `_install_hermes_gateway` behind `hal0 agent install hermes`.
    - If any `releases/` path remains, the helper reports it, and the caller
      warns and leaves the gateway disabled, the posture both callers take
      today when the generator fails.
    - After that the unit names only stable paths. A switch of `current` plus
      the stop and start in decision 7 moves the gateway, and `hal0 update`
      never has to touch the unit. Decision 10's backstop catches a unit that
      did not pass through the helper.
    - Existing hosts need nothing. Their unit was generated with the venv at
      `/var/lib/hal0/venvs/hermes`, which stays valid as a symlink.
    - PR 4 must check that a unit generated at `3ef6bbd2`
      (`python -m hermes_cli.main … gateway run`) still starts the new pin. If
      it does not, PR 4 adds the same normalised regeneration to the root
      prelude and names `sudo hal0 agent install hermes` in its release note.
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
14. **#2615 lands as four PRs, in order, and each one ships on its own.**
    1. **Stale interpreter re-probe.** Decision 9's discard, warning and
       re-probe for an invalid `HAL0_HERMES_PYTHON`, and persisting the
       re-probed value. The target stays 3.12, the variable name and the file
       format do not change. Unit tests only. Size S.
    2. **Release layout, builder, retention and everything the symlink
       needs.**
       - Decisions 4 to 7 and 10 to 13, with every build path in decision 6
         rerouted in the same PR. A path left on `_install_venv` would write
         into a live release.
       - The lock, the layout trigger and uv shipped in hal0's venv.
       - Retention, including the gateway backstop, so releases never
         accumulate.
       - The gateway helper and both of its callers.
       - The teardown and audit, because the symlink lands here.
       - The pin stays `3ef6bbd2` and the interpreter 3.12.

       The layout trigger migrates every host on its next `hal0 update`. The
       PR proves the editable install of `3ef6bbd2` on 3.12 in CI or a scratch
       venv before the operator runs the upgrade lane on the test fleet. A
       human merges. Size L.
    3. **Python 3.14.** Decision 9's interpreter move. The interpreter-minor
       trigger rebuilds every host on its next `hal0 update`, as an ordinary
       new release on the same pin. `3ef6bbd2` installs on 3.14 (Context), so
       this PR does not wait for PR 4. The upgrade lane runs again, and a
       human merges. Size S to M.
    4. **Pin bump.** A newer vetted ref, the re-vendored contract fixtures
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
- Every activation stops the agent and the gateway for the length of a
  `config migrate`, in place of today's restart. Every existing install
  migrates on its first `hal0 update` after PR 2 and rebuilds once more
  after PR 3.
- At steady state the disk holds two releases (two venvs, two worktrees of
  one bare mirror), plus the uv cache. Nothing else is kept.
- A failed activation flips back by itself, and that covers code and venv,
  not `HERMES_HOME` beyond the config copy. There is no manual rollback
  command: a rollback done by hand lasts only until the next `hal0 update`
  (decision 8, #2805).
- `HAL0_HERMES_PYTHON` keeps its name. A value that fails validation is
  discarded with a warning instead of stopping the build.
- hal0 post-edits the gateway unit upstream generates, through one helper
  that both root generator runs call.
- `--to=<version>` and the version-floor path stop working. `--to` takes a
  vetted sha.
- The version stamp is hal0's own record, written from `release.json`. A
  venv edited outside the builder can disagree with it, and status trusts the
  stamp.
- Pin bumps cost a contract-fixture refresh each time, as before.

## References

- #2615 (the implementation and the decision), #2566 (upstream drift watch),
  PR #2614 (moves the pin to 0.19.0, the ceiling until this lands), #2787
  (this record), #2805 (the deferred manual rollback and hold).
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
  `src/hal0/cli/agent_commands.py` (`_install_hermes_gateway`),
  `src/hal0/components/runner.py` and `src/hal0/components/status.py`
  (component failure statuses),
  `installer/agents/hermes-prereqs.sh`, `installer/wrappers/hal0-systemctl`
  (`stop-agent`, `start-agent`, `try-restart-agent`, `svc-start`, `svc-stop`;
  `test_gateway_rejects_execstart` in
  `tests/installer/test_systemctl_dropin_validation.py`), `installer/install.sh`
  (`hermes gateway install --system`), `installer/agents/hermes/requirements.txt`.
- Upstream unit generator: `hermes_cli/gateway.py` (`get_python_path`,
  `generate_systemd_unit`) at `3ef6bbd2`; `hermes_cli/_launchers.py`
  (`runtime_command`, `installation_command`) at `71d34bad`.
