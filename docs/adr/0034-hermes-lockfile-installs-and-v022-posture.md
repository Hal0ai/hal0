# ADR-0034: Hermes releases install from upstream's lockfile, on a uv-managed Python 3.14

## Status

**ACCEPTED (2026-10-21).** The operator accepted every recommendation in the
v0.22.2 review on 2026-10-21. This record amends ADR-0032, decisions 1, 6, 9
and 12, and adds two decisions that the pin bump to v0.22.2 needs. All other
parts of ADR-0032 stand, including the four-PR split in its decision 14. The
work is tracked on #2615 and ships in v1.6.0.

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
   - When the vetted ref ships a `uv.lock`, the builder installs the release
     in three steps:
     1. `uv export --frozen --extra web --extra mcp --no-emit-project` from
        the checkout;
     2. `uv pip sync --require-hashes` of that export into the release venv;
     3. `uv pip install --no-deps -e "<checkout>"`.
   - A ref without a lock (`3ef6bbd2`) keeps ADR-0032's
     `uv pip install -e "<checkout>[web,mcp]"`.
   - The extras stay `web` and `mcp`, read from the requirements line as
     ADR-0032 decision 3 says.
2. **Amends ADR-0032 decision 9: a uv-managed 3.14 is preferred.**
   - The builder uses a uv-managed CPython 3.14 under `/var/lib/hal0/python`.
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
7. **New: hal0 turns session pruning off.** The config overlay writes
   `sessions.auto_prune: false` at the pin bump, so an upgrade deletes no
   session history. Retention policy is a later decision with its own
   setting.
8. **The pin moves to v0.22.2, `d923a5eb`.** The bump is ADR-0032 PR 4,
   with its contract-fixture refresh (decision 15). A pre-release tag (`rc.*`
   or `+canary.*`) is never vetted.

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
- NousResearch/hermes-agent `d923a5eb` (v0.22.2): `pyproject.toml`,
  `uv.lock`, `hermes_state_wal.py`, `hermes_cli/version_info.py`,
  `hermes_cli/main.py`, `hermes_cli/gateway.py`, `hermes_cli/_launchers.py`,
  `hermes_cli/source_build.py`, `hermes_cli/config_defaults.py`.
