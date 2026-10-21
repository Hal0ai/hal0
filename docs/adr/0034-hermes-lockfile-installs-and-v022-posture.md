# ADR-0034: Hermes releases install from upstream's lockfile, on a uv-managed Python 3.14

## Status

**ACCEPTED (2026-10-21).** The operator accepted every recommendation in the
v0.22.2 review on 2026-10-21. This record amends ADR-0032, decisions 1, 6, 9
and 12, and adds two decisions that the pin bump to v0.22.2 needs. All other
parts of ADR-0032 stand, including the four-PR split in its decision 14. The
work is tracked on #2615 and ships in v1.6.0.

Amended for v1.6.1 (#2874): decision 8's pin moves to v0.22.4. See
"Amendment: the pin moves to v0.22.4" below.

## Context

ADR-0032 was written against hermes-agent v0.21.7 (`71d34bad`). This record
checks its design against v0.22.2. In what follows, `NEW` means
`d923a5eb487a17c0a68801bba9ef0f56c81040a8`, the commit behind the v0.22.2
tag and the latest stable release on 2026-10-21. `OLD` means `3ef6bbd2`,
today's pin. Six findings change ADR-0032.

1. **Upstream ships a hash-locked `uv.lock`, and an unlocked install drifts
   from it.**
   - NEW's `uv.lock` holds 327 packages with sha256 hashes. It resolves for
     Python 3.14 only (`pyproject.toml:603`) and holds new versions back for
     a 3-day quarantine (`:621-627`).
   - ADR-0032 installs with `uv pip install -e "<checkout>[web,mcp]"`, which
     resolves freely. In a scratch venv that resolve differed from the lock
     on 18 transitive packages, for example opentelemetry-api 1.45.1 against
     the locked 1.39.1.
   - Installing from the lock gives each release the dependency set upstream
     tested, with hashes checked. Verified in a scratch venv:
     `uv export --frozen --extra web --extra mcp --no-emit-project`, then
     `uv pip sync --require-hashes`, then `uv pip install --no-deps -e <checkout>`.
     `uv pip check` reported no problems afterwards.
2. **The system Python 3.14 on Ubuntu 26.04 links an SQLite that Hermes
   works around.**
   - Ubuntu 26.04's `python3.14` (3.14.4) links SQLite 3.46.1.
   - Hermes treats any SQLite older than 3.51.3 as having the WAL-reset bug.
     It forces DELETE journal mode on every new database and logs a warning
     for each one (NEW `hermes_state_wal.py:132`, `:255-285`).
   - A uv-managed CPython 3.14.7 links SQLite 3.53.1.
   - ADR-0032 decision 9 prefers a system `python3.14`, which on 26.04 is
     the worse choice. On 24.04 no 3.14 exists in apt, so a uv-managed one
     is used there anyway.
3. **The version string shown to users needs the tags.**
   - `hermes --version` resolves its version through
     `version_info.get_version_info()` (NEW `version_info.py:208-277`). That
     tries an install stamp, then the highest stable tag merged into HEAD,
     then `git.<sha7>`.
   - A clone without tags reports `git.d923a5e`; the same commit with tags
     reports `0.22.2`.
   - There is no machine-readable form: `--json` is rejected.
   - `hermes --version` also makes a network update check (NEW
     `main.py:2241-2243`), so the release check in ADR-0032 decision 6 is
     not offline-safe.
4. **The gateway unit has a new shape, and the generator now enables
   linger.**
   - At NEW, `gateway.py:3256-3346` and `_launchers.py:38-75` write
     `ExecStart`, a new `ExecStop` and `ExecStopPost`, all as
     `"<venv>/bin/python" "-I" "-c" "…sys.path.insert(0, '<checkout>')…"`.
     They set no `VIRTUAL_ENV`. The release path therefore appears six times
     on three lines, inside quoted `-c` strings.
   - `--system --run-as-user hal0` also adds
     `After=`/`Wants=user@<uid>.service` and enables systemd linger for that
     user (`gateway.py:3292-3295`). Upstream's cron and kanban workers now
     run through `systemd-run --user`.
   - A unit generated at OLD (`python -m hermes_cli.main … gateway run`)
     still starts NEW. This was checked by booting the gateway with an
     OLD-shape command line against a NEW venv.
5. **A source checkout has no built dashboard UI.**
   - `hermes_cli/web_dist/` is gitignored at OLD and at NEW.
   - hal0's `_resolve_web_dist` (`src/hal0/cli/agent_shim.py`) looks only
     under `site-packages`. On installed hosts it finds nothing even today,
     so `--skip-build` is never passed and every dashboard start runs an
     npm build.
   - At NEW that build calls `pm.ensure("npm")` (`source_build.py:29-46`),
     which downloads a pinned Node into `HERMES_HOME/tools` when the unit
     starts.
   - Under ADR-0032's editable layout the `site-packages` path can never
     exist.
6. **Sessions are pruned by default.** From v0.22, Hermes deletes ended
   sessions that have been idle for 90 days, at startup, unless
   `sessions.auto_prune` is false (NEW `config_defaults.py:515`, `:2269`).
   hal0 does not set the key.

Both the dashboard WebSocket contract that ADR-0033 relies on (`?token=` on a
loopback bind) and the `window.__HERMES_SESSION_TOKEN__` injection that the
board executor harvests are unchanged at NEW.

## Decision

1. **Amends ADR-0032 decision 1: dependencies come from upstream's lock.**
   - When the vetted ref ships a `uv.lock` whose `requires-python` admits the
     venv's interpreter minor, the builder installs the release in three
     steps:
     1. `uv export --frozen --extra web --extra mcp --no-emit-project` from
        the checkout;
     2. `uv pip sync --require-hashes` of that export into the release venv;
     3. `uv pip install --no-deps -e "<checkout>"`.
   - A ref without a lock, or whose lock cannot serve that minor, keeps
     ADR-0032's `uv pip install -e "<checkout>[web,mcp]"`, and `release.json`
     records which path ran (`install: lock|editable`). Correction
     (2026-10-21): this line first said `3ef6bbd2` has no lock. It does ship
     a `uv.lock`, with `requires-python = ">=3.11, <3.14"`, so it takes the
     lock path on 3.12 and the editable path on 3.14.
   - The extras stay `web` and `mcp`, read from the requirements line as
     ADR-0032 decision 3 says.
   - **Amendment (2026-10-21): the gateway's platform extras are added at
     build time.** v0.22.2 installs a messaging platform's SDK lazily, at
     first use (`pm.ensure_import`), and that lazy install refuses to run
     from a process outside pm's own environment, which a hal0 release is
     (`pm/client.py:208-223` at d923a5eb). The built-in adapters, the API
     server among them, need aiohttp and have no lazy path at all. So the
     builder adds upstream's extra for each platform the box configures
     (the adapter's own lazy extra, such as `telegram`, `discord`, `slack` or
     `teams`, and `sms`, which is aiohttp alone, for the aiohttp adapters),
     read from the gateway's secrets vault, `HERMES_HOME/.env` and the
     `config.yaml` platform blocks, plus the ones the live venv already has
     installed. The API server always counts, because hal0 provisions its key
     on every box, after a fresh install's first build. An unprivileged
     caller learns which vault keys are set from the `hal0-agentenv`
     `secret-keys` verb, which prints names and never values. They install only through the lock
     path, with `--require-hashes`; the no-lock fallback keeps the pin's
     extras alone. `release.json` records them as `platform_extras`, and a
     configured platform whose extra is missing from a lock-built live
     release is a build trigger. `matrix` is never added: its `python-olm`
     builds from source, and upstream's adapter degrades without it. hal0 does
     not rely on upstream's lazy installs instead. In a release, a lazy
     install succeeds at most once per process, and only takes effect after
     a restart. It commits a pm "generation" under `HERMES_HOME/installs`
     holding that one extra, without the pin's, and every later Hermes
     process for the release boots from that generation instead of the
     verified release venv. Such a generation for the live release is
     therefore a build trigger too. The rebuild carries every extra upstream
     recorded for the generation (`packages.venv.extras`), not only the
     platform ones, and `release.json` keeps them for every later rebuild,
     so a lazily installed feature costs one rebuild, never one per update
     (#2876). `matrix` and the umbrella extras are never carried.
2. **Amends ADR-0032 decision 9: a uv-managed 3.14 is preferred.**
   - The builder uses a uv-managed CPython 3.14 under
     `/var/lib/hal0/hermes/python` (see the amendment at the end of this
     decision; this line originally said `/var/lib/hal0/python`).
   - It accepts a system `python3.14` only if that interpreter's
     `sqlite3.sqlite_version` is at least 3.51.3.
   - `HAL0_HERMES_PYTHON` remains the operator's override, and an override
     with an older SQLite is used with a warning.
   - `hal0 doctor` reports the SQLite version of the live Hermes
     interpreter.
   - Everything else in decision 9 stands, including uv shipped in hal0's
     own venv and the discard-and-re-probe of a stale value. One addition:
     the builder finds uv next to the running interpreter
     (`Path(sys.executable).parent / "uv"`), not on `PATH`.
   - **Amendment (2026-10-21, #2615 PR 2): uv's interpreters and cache live
     inside the release tree.** The builder sets `UV_PYTHON_INSTALL_DIR` to
     `/var/lib/hal0/hermes/python` and `UV_CACHE_DIR` to
     `/var/lib/hal0/hermes/.cache/uv`, not `/var/lib/hal0/python` and
     `/var/lib/hal0/.cache/uv`. The reason: on existing installs
     `/var/lib/hal0/python` is `root:hal0 755`, with `root:root`
     interpreter directories under it, because an earlier root-run uv fetch
     created it, and the uv cache can be root-owned the same way. Builds run
     as `hal0` (§7.4), so `uv python install` there would fail on every
     existing host and leave its `hal0 update` stuck on the legacy release.
     `/var/lib/hal0/hermes` is the builder's own `hal0`-owned tree, created
     before any build. `/var/lib/hal0/python` is left alone: a legacy
     release, and a persisted `HAL0_HERMES_PYTHON`, may still use the 3.12
     in it. Retention prunes only interpreters under `hermes/python`. Only
     root paths remove the legacy directory: `installer/uninstall.sh`, and
     the root prelude of `hal0 agent install`/`reprovision`/`upgrade` once
     no release, legacy venv or `HAL0_HERMES_PYTHON` names an interpreter
     in it. The root prelude hands any files its own uv fetch writes under
     `hermes/python` or `hermes/.cache/uv` back to `hal0`. `hal0 doctor
     perms` audits the release tree and does not treat the root-owned
     legacy directory as an error.
   - **Amendment (2026-10-21, #2615 PR 3): the target minor follows the
     ref, and the interpreter order is override, uv, system.** This amends
     decisions 1 and 2 above. The builder never installs a ref on an
     interpreter its metadata excludes. A release is built on the newest of
     3.14 and 3.12 that the ref's `requires-python` admits. hal0 records that
     spec per vetted ref (`VETTED_HERMES_REQUIRES_PYTHON`), so the build
     trigger and the root prelude know the minor before a checkout exists,
     and the build refuses a checkout that disagrees with the record.
     `3ef6bbd2` (`>=3.11,<3.14`) therefore stays on 3.12 under its own lock,
     and the move to 3.14 arrives with the pin bump to a ref that requires it,
     under that ref's lock. A ref with no usable lock falls back to the
     editable `[extras]` install with `--exclude-newer` set to the commit
     date, so the resolve is reproducible. The interpreter for that minor is:
     a valid `HAL0_HERMES_PYTHON` override first; then a uv-managed CPython
     (`--managed-python` on `uv python install` and `uv python find`, and the
     found interpreter must link SQLite >= 3.51.3); then a system
     `python<minor>` with that SQLite. hal0's own venv interpreter is never
     used.
3. **Amends ADR-0032 decisions 4 and 6: the mirror fetches tags, and the
   release check is offline-safe.**
   - `mirror.git` fetches tags along with the sha.
   - `release.json` records the display version from
     `git describe --tags --match 'v[0-9]*' --abbrev=0`, computed at build
     time. The identity is still the sha, and status trusts the sha, not the
     display string.
   - Decision 6's `venv/bin/hermes --version` check is replaced by two
     checks:
     - `venv/bin/python -I -c "import hermes_cli.main, hermes_cli.web_server"`;
     - `venv/bin/hermes --help` exiting 0.
   - hal0 never reads Hermes's version from package metadata.
4. **Amends ADR-0032 decision 6: the dashboard UI is built once per
   release.**
   - After the venv step, `build_release` builds `hermes_cli/web_dist` in
     the release checkout with upstream's web build, using upstream's pinned
     Node.
   - A failed UI build fails the release before activation.
   - `_resolve_web_dist` finds the UI through the live release's checkout
     (`/var/lib/hal0/venvs/hermes` → `current/src/hermes_cli/web_dist`), so
     the agent passes `--skip-build` and never builds at unit start.
5. **Amends ADR-0032 decision 12: the normaliser rewrites the whole unit
   file.**
   - `normalise_gateway_unit()` replaces every
     `/var/lib/hal0/hermes/releases/<id>/` substring anywhere in the file
     with `/var/lib/hal0/hermes/current/`. That includes the
     `ExecStart`, `ExecStop` and `ExecStopPost` lines and the checkout path
     inside their `-c` strings.
   - Its tests use a unit generated at NEW as the fixture, as well as one
     generated at OLD.
6. **New: linger for `hal0` is accepted and made visible.**
   - hal0 accepts the linger that `hermes gateway install --system
     --run-as-user hal0` enables, because upstream's scheduled and kanban
     workers depend on it.
   - `hal0 doctor` reports whether linger is on for `hal0`.
   - `hal0 agent uninstall hermes` and `installer/uninstall.sh` disable it
     (`loginctl disable-linger hal0`).
   - The `hal0-systemctl` allow-list and the agent unit's sandbox do not
     change.
   - **Amendment (2026-10-21, #2615): `hal0 update` heals an old-shape
     gateway unit.** A box that ran Hermes before 1.6.0 keeps the unit
     Hermes 0.19.0 wrote, with no `user@<uid>.service` ordering and no
     linger, and `hal0 update` never reran the generator. The fix is a new
     no-argument `hal0-update hermes-gateway` verb. The sudoers grant does
     not change: it already names the wrapper with no argument list. Hermes's
     converge arm calls the verb through `UpdateSeam`, as `hal0`, once the
     live release is v0.22-shape (its checkout has `hermes_bootstrap`). It
     runs after both a release build and a converged no-op, so a box that
     already updated is healed by its next update. The call happens only
     when the unprivileged check behind the notice finds an enabled or
     running gateway whose unit is old-shape or whose linger is off.
   - As root the verb runs only hal0's own code from the root-owned
     `/usr/lib/hal0` tree, imported with `-I`. It never runs Hermes code:
     the Hermes venv and checkout are `hal0`-owned, so running `hermes
     gateway install --system` from them as root would let a process
     compromised as `hal0` edit that tree and then call the verb to run its
     code as root. Instead `hal0.agents.hermes_gateway_regen` renders the
     v0.22.2 unit from a template, with fixed `current/` paths and the `hal0`
     uid from `pwd`. It validates the result (`User=hal0`, no root `Exec*`,
     only allowed paths) and writes it `root:root 0644` atomically. It reads
     only the existing unit file and the state reported by `systemctl` and
     `loginctl`.
   - The verb then runs `loginctl enable-linger hal0`, and runs `systemctl
     restart hermes-gateway.service` only if the gateway was running. It
     never enables, disables, starts or stops the unit. A disabled, stopped
     or masked gateway is left alone, and a current unit with linger on is a
     no-op.
   - A failure, or a missing grant, does not fail the update. The `hal0
     update` notice and the `hal0_linger` doctor row stay as the fallback.
     `sudo hal0 agent install hermes` still runs upstream's generator,
     because that root run is the operator's choice.
   - **Amendment (2026-10-22, #2894): a unit that is not on hal0's launcher
     is stale too, and activation heals it before the new release starts.**
     Upstream can render the unit itself (`hermes gateway migrate
     --multiplex`, or `hermes gateway install` run outside hal0). That unit
     has the `user@<uid>.service` ordering but runs upstream's launcher shim
     in one release's checkout (`current/src/.hermes/bin/hermes`), which
     fails every start on the next release, so activation flipped back on
     every update. A unit is now stale when it is old-shape, or when any
     `ExecStart`, `ExecStop` or `ExecStopPost` is not hal0's launcher
     (`current/venv/bin/python -I -c` with `current/src` on `sys.path`). The
     release builder calls the same verb during activation, after the switch
     and while the units are stopped, before any unit starts or the release
     is re-verified. The converge arm's call after a build or a converged
     no-op stays. The verb, its sudoers grant, its validation and its
     never-run-Hermes posture do not change.
7. **New: hal0 turns session pruning off.** The config overlay writes
   `sessions.auto_prune: false` at the pin bump, so an upgrade deletes no
   session history. Retention policy is a later decision with its own
   setting.
8. **The pin moves to v0.22.2, `d923a5eb`.** The bump is ADR-0032 PR 4,
   with its contract-fixture refresh (decision 15). A pre-release tag (`rc.*`
   or `+canary.*`) is never vetted.

### Amendment (2026-10, #2874): the pin moves to v0.22.4

Decision 8's pin moves to v0.22.4, `4d04303818b61bf3aa09278af48356315515965b`.
The bump ships in v1.6.1, not v1.6.0. Everything else in this record stands.

- **v0.22.2 stays vetted.** `d923a5eb` remains in `VETTED_HERMES_REFS`, so
  the release flip-back and `hal0 agent upgrade hermes --to d923a5eb…` still
  build it. A box on v0.22.2 rebuilds onto v0.22.4 at its next `hal0 update`.
- **Nothing in this record changes with the version.** The lock still serves
  Python 3.14 (decisions 1 and 2), the extras are unchanged, and the dashboard
  build still works (decision 4). The gateway unit the generator writes is
  byte-identical (decision 5), so the template hal0 regenerates the unit from
  does not change. Decision 7 still holds: v0.22.3 made `sessions.auto_prune`
  opt-in again, but hal0 keeps writing `false`. That keeps history on a
  flip-back to v0.22.2 and keeps away the one-time low-disk prune question
  Hermes now asks while the key is unset.
- **New precondition.** From v0.22.3 the Hermes gateway owns chat turns and
  the session store. The dashboard's default Chat tab still runs in the
  dashboard process. A profile-scoped chat, and the classic `hermes chat`
  CLI, are gateway clients: with no gateway unit installed they start an
  unmanaged `gateway run --idle-exit`, which runs cron and the kanban
  dispatcher (upstream `hermes_cli/gateway_runtime.py:390-482`,
  `hermes_cli/gateway_runtime_start.py:35-81`). A gateway unit that is
  installed but masked, failed or stopped blocks that start. Before this pin
  merges, hal0 must make sure no client can start a gateway on a box where
  the operator disabled it. #2874 lists the work, and #2878 tracks a related
  gap: the preview-restart side agent ignores `agent.disabled_toolsets`.

## Consequences

- Each Hermes release installs exactly what upstream locked, with hashes,
  for refs that ship a lock.
- On Ubuntu 26.04 the Hermes venv uses a uv-managed 3.14, not the distro's,
  so its interpreter patches arrive when hal0 moves the managed patch
  release, not through apt.
- Building a release now also builds the dashboard UI. That needs the
  network and adds build time, but agent start no longer depends on npm or
  the network.
- The `hal0` user has a lingering systemd user manager once the gateway is
  installed. `hal0 doctor` shows it, and uninstall removes it.
- Session history is kept indefinitely, as it is today, until a retention
  setting exists.
- ADR-0032's checks are changed in one place only: the smoke test no
  longer prints a version, so a build log shows the display version from
  `release.json` instead.

## References

- ADR-0032 (amended here), ADR-0033 (unchanged), ADR-0004.
- #2615 (implementation), #2805 (deferred manual rollback).
- NousResearch/hermes-agent `4d043038` (v0.22.4, amendment):
  `hermes_cli/gateway_runtime.py`, `hermes_cli/gateway_runtime_start.py`,
  `hermes_cli/gateway_runtime_service.py`, `hermes_cli/web_server_chat.py`,
  `gateway/run_idle_exit.py`, `hermes_cli/config_defaults.py`.
- NousResearch/hermes-agent `d923a5eb` (v0.22.2): `pyproject.toml`,
  `uv.lock`, `hermes_state_wal.py`, `hermes_cli/version_info.py`,
  `hermes_cli/main.py`, `hermes_cli/gateway.py`, `hermes_cli/_launchers.py`,
  `hermes_cli/source_build.py`, `hermes_cli/config_defaults.py`.
