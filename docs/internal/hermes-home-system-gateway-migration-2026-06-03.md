# Hermes home-normalization + system-scope gateway — design/spec

**Date:** 2026-06-03
**Branch:** `feat/hermes-home-normal-location-system-gateway`
**Status:** approved design, pre-implementation

## Goal

Run Hermes entirely as the `hal0` system user, out of `root`, on the
normalized hermes default home, with the messaging gateway as a
system-scope service. Two outcomes:

1. **Fresh install lands correctly** — a clean `install.sh` produces the
   normalized layout with Telegram + Discord connected on first boot.
2. **This box is cut over** — the live hal0 box (currently a hybrid:
   dashboard already `User=hal0`, gateway still a `root` user-service) is
   migrated in-place, preserving all live agent state.

There are **no other existing installs**, so we do NOT ship permanent
"migrate an existing install" machinery — fresh-install correctness in
the repo, plus a one-time cutover script for this box.

## Target end-state

| Aspect | Target |
|---|---|
| Runtime user | `hal0` (uid 996) for both dashboard and gateway |
| `HERMES_HOME` | `/var/lib/hal0/.hermes` (hermes default for hal0) |
| Dashboard svc | `hal0-agent@hermes.service` (system, `User=hal0`) — already there |
| Gateway svc | `/etc/systemd/system/hermes-gateway.service` (system, `User=hal0`) |
| Gateway secrets | drop-in `…/hermes-gateway.service.d/10-hal0-secrets.conf` → `EnvironmentFile=/var/lib/hal0/secrets/agents/hermes.env` |
| Canonical CLI | `/usr/local/bin/hermes` (no HERMES_HOME pin); `hal0-hermes` → symlink |
| `root` involvement | none (no `/root/.config/systemd/user`, no linger) |

## Already built in the WIP (keep)

- `hermes_home` default → `/var/lib/hal0/.hermes` (provisioner, override.conf,
  installer script, both wrappers, personas/shim path refs).
- Canonical `/usr/local/bin/hermes` wrapper (untracked `installer/wrappers/hermes`)
  + `hal0-hermes` back-compat symlink via `_install_backcompat_symlink`.
- `_phase_gateway_secrets_wire` (#437): idempotent system-scope secrets
  drop-in + `daemon-reload`, non-root SKIP guard, hash-skip.
- Updated tests (`test_hermes_provision`, new `test_hermes_wrapper`,
  `test_unit_files`) + docs (SERVICE.md, CONFIG.md, ADRs).

## Gap to fix in the repo (fresh-install correctness)

The installer enables the dashboard (`install.sh` →
`systemctl enable --now hal0-agent@hermes.service`) but **never installs
the system gateway**. `_phase_gateway_secrets_wire` writes a drop-in for a
main unit that nothing creates.

**Fix:** in the install flow, after the provisioner has run (secrets vault
+ drop-in written) and the dashboard is up, run:

```
hermes gateway install --system --run-as-user hal0   # writes /etc/systemd/system/hermes-gateway.service
systemctl daemon-reload                               # picks up the 10-hal0-secrets.conf drop-in
systemctl enable --now hermes-gateway.service
```

Ordering requirement: the secrets drop-in must be present before first
`start` so the gateway connects platforms on boot. The drop-in survives
`hermes gateway install` regenerating the main `.service` (hermes_cli
rewrites the `.service` body, never the `.d/` tree).

Explicitly **out of scope** (no existing users): a permanent
`home_migrate` phase and a permanent `legacy_gateway_teardown` phase.

## One-time cutover for this box (preserve data)

Ordered to avoid the two failure modes — (a) two gateways sharing one bot
token (Telegram HTTP 409 on `getUpdates`, double Discord replies), and
(b) copying WAL-mode `state.db` while a writer is live (corruption):

1. **Backup**: `cp -a /var/lib/hal0/agents/hermes /var/lib/hal0/agents/hermes.bak-cutover` (+ the WIP patch already saved).
2. **Stop both writers**: `systemctl --user stop hermes-gateway` (root scope) and `systemctl stop hal0-agent@hermes`.
3. **Migrate state** → `/var/lib/hal0/.hermes` (preserve `hal0:hal0`, keep `.hal0-managed` marker):
   `config.yaml`, `.env`, `state.db`(+`-wal`/`-shm`), `sessions/`, `memories/`,
   `kanban.db`, `channel_directory.json`, `discord_threads.json`,
   `.hermes_history`, `personas/`, `profiles/`, `cron/`, `auth.json`,
   `SOUL.md`, `skills/`.
4. **Deploy** the new `hal0-agent@hermes` override.conf (`HERMES_HOME=.hermes`); `systemctl daemon-reload`.
5. **Install system gateway**: `hermes gateway install --system --run-as-user hal0`; ensure the `10-hal0-secrets.conf` drop-in is present; `systemctl daemon-reload`.
6. **Tear down the root user-gateway** (this box only): `systemctl --user disable --now hermes-gateway`; remove `/root/.config/systemd/user/hermes-gateway.service` + its `.d/`.
7. **Start**: `systemctl enable --now hermes-gateway.service`; `systemctl start hal0-agent@hermes`.

## Verification

- Both services `active`, `MainPID` owned by `hal0` (not root).
- `gateway_state.json` (live pid): telegram + discord `connected`; `getMe` ok.
- Bot tokens present in the gateway process env (now read by pid1/root for a `User=hal0` system unit).
- Dashboard reachable; `state.db`/sessions/kanban/memories present + intact under `.hermes`.
- No process under `user@0.service`; `/root/.config/systemd/user/hermes-gateway.service` gone.

## Rollback

Old home preserved at `agents/hermes(.bak-cutover)`. Revert override.conf,
re-enable the root user-gateway from the backed-up unit, restart. The old
gateway is *stopped, not deleted*, until verification passes.

## Risks

- **Two-gateway token clash** → teardown-old strictly precedes start-new.
- **SQLite WAL copy** → both writers stopped before the `state.db` copy.
- **Brief messaging downtime** during the cutover window (expected, short).
