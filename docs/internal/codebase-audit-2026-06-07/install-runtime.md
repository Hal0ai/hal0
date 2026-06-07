# A3 — Install & Runtime Path Audit (2026-06-07)

The signed one-liner install path (`bootstrap.sh` → `install.sh`) is well-structured: cosign-verified
tarball, FHS layout (`/usr/lib/hal0/hal0-<ver>` + `current` symlink + shared venv), idempotent re-runs.
The headline fragility is that **`hal0 update` on an editable `/opt/hal0` install (the live CT105
deployment) silently no-ops** — `apply()` does not refuse, it extracts + swaps a symlink the running
process never reads and reports success — directly contradicting install.sh's own comment. Two other
clean-install BLOCKERs: the Lemonade SHA-256 is an all-zeroes placeholder (skips inference silently),
and `hal0-api` runs as **root** bound to `0.0.0.0:8080` by default.

---

## 1. Install flow, end to end (signed one-liner)

### Stage A — `bootstrap.sh` (the trust boundary)
File: `installer/bootstrap.sh`. Served at `https://hal0.dev/install.sh` (mirrored copy in hal0-web,
warned at `installer/bootstrap.sh:3-6`).

1. `preflight()` — Linux-only; requires `curl tar sha256sum python3` (`installer/bootstrap.sh:65-71`).
2. `fetch_manifest()` — downloads `hal0.releases.v1` manifest from GH Releases `latest/download/${CHANNEL}.json` (`installer/bootstrap.sh:39-40, 74-81`).
3. `parse_manifest_field()` — pulls `version,url,sig_url,cert_url,digest_sha256,signer_identity,signer_issuer` via inline python3 (`installer/bootstrap.sh:83-95, 170-177`).
4. `fetch_and_hash_check()` — downloads tarball, **sha256-verifies against manifest digest**, dies on mismatch (`installer/bootstrap.sh:98-112`).
5. `cosign_verify()` — keyless OIDC `cosign verify-blob` against the cert's SAN identity-regexp + issuer; dies on failure. Skippable **only** when cosign is absent AND `HAL0_UPDATE_SKIP_COSIGN=1` (loud warning) (`installer/bootstrap.sh:123-152`).
6. Extract tarball, assert `installer/install.sh` is present+executable (`installer/bootstrap.sh:189-193`).
7. `export HAL0_BOOTSTRAP_VERIFIED=1` and `exec` into `install.sh`, passing stdin through for interactive prompts (`installer/bootstrap.sh:201-206`).

### Stage B — `install.sh` setup
File: `installer/install.sh`.

8. Sources `lib/ui.sh` (banner/spinner/box) and `lib/preflight.sh` (re-runnable checks) (`installer/install.sh:26, 34`).
9. **Release-verification gate**: refuses to run as root against an unverified tree unless `HAL0_BOOTSTRAP_VERIFIED=1`, a `.git` checkout, `--dev`, or explicit `HAL0_INSTALL_SKIP_VERIFY=1` (`installer/install.sh:204-222`).
10. **Layout resolution** (`installer/install.sh:167-193`):
    - Prod/FHS: `HAL0_FHS_ROOT=/usr/lib/hal0`, `PREFIX=$ROOT/hal0-<VERSION>` (version scraped from `pyproject.toml`), `CURRENT_LINK=$ROOT/current`, `ETC_DIR=/etc/hal0`, `VAR_DIR=/var/lib/hal0`, `VENV_DIR=$ROOT/venv`.
    - Dev (`--dev`): everything under `${PWD}/.hal0ai`, `CURRENT_LINK=""`, editable pip install.
11. Legacy `/opt/hal0/.venv` detection — warns it is orphaned, does **not** auto-delete (`installer/install.sh:229-232`).
12. Models pull-dir prompt/flag, seeds `[models].pull_root` into `hal0.toml` via an awk/python regex patch — **not a real TOML parser** (`installer/install.sh:238-442`).
13. **Source copy**: prod rsyncs (or tar-pipes) `REPO_ROOT` → `PREFIX` excluding `.venv/.git/__pycache__/node_modules`, then atomic-swaps `current` → `PREFIX` (temp symlink + `mv -T`) (`installer/install.sh:369-401`).
14. **venv + pip**: `python -m venv $VENV_DIR`; upgrade pip/setuptools/wheel; prod `pip install $REPO_ROOT` (NON-editable), dev `pip install -e` (`installer/install.sh:444-471`). Binaries `hal0`, `hal0-agent` land in venv; `/usr/local/bin/hal0` symlink for PATH (`installer/install.sh:481-484`).

### Stage C — systemd units
15. `hal0-api.service` written inline; `User=${HAL0_USER}` (**default root**, `installer/install.sh:160, 640`), `WorkingDirectory=$CURRENT_LINK` (follows update swaps), `ExecStart=$HAL0_BIN serve --host 0.0.0.0 --port $HAL0_PORT` (`installer/install.sh:629-653`).
16. `hal0-openwebui.service` copied from `packaging/systemd/`, image pinned by sha256 digest (`installer/install.sh:660-672`).
17. `hal0-agent@.service` template + `hal0-agent@hermes.service.d/override.conf` from `installer/systemd/`; Hermes session-state hook installed to hard-coded `/usr/lib/hal0/hermes-hooks/` (`installer/install.sh:680-713`).
18. The legacy `hal0-slot@.service` template was **removed in PR-9** — v2 hands process lifecycle to lemond (`installer/install.sh:655-658`).

### Stage D — Lemonade (lemond) bootstrap
19. **System user/group** `hal0` created idempotently: `groupadd --system hal0` + `useradd --system --gid hal0 --home-dir /var/lib/hal0 --shell /usr/sbin/nologin` (`installer/install.sh:1068-1078`).
20. GPU access: `usermod -aG render,video hal0` for `/dev/kfd` + `/dev/dri` (only groups that exist) (`installer/install.sh:1086-1095`).
21. **Lemonade tarball**: download → **sha256 verify** (placeholder logic, see BLOCKER below) → extract to `/opt/lemonade` with version marker for idempotent re-runs (`installer/install.sh:1097-1158`).
22. `--threads` formula `max(2,(nproc-2)/4)` to dodge the documented multi-llama-server CPU-oversubscription deadlock (`installer/install.sh:1039-1048`).
23. `lemonade/config.json` written: `port 13305`, `max_loaded_models 8`, `extra_models_dir /var/lib/hal0/models`, llamacpp `--no-mmap` + Vulkan backend, chown `hal0:hal0` (`installer/install.sh:1245-1272`).
24. `hal0-lemonade.service`: `User=hal0`, `ExecStart=/opt/lemonade/lemond <cache>`, `ExecStop` curl to `/internal/shutdown`, `LimitMEMLOCK=infinity`, `CPUQuota=80%` (`installer/install.sh:1279-1298`).
25. Two drop-ins: `kfd-perms.conf` re-chgrps `/dev/kfd` to `render` every boot via `ExecStartPre=+-` (root, non-fatal); `20-vulkan-radv.conf` pins `AMD_VULKAN_ICD=RADV` (`installer/install.sh:1312-1332`).

### Stage E — first run / service start
26. Bundle manifests (Lite/Default/Pro/Max/Omni) shipped from `installer/manifests/omni/` (`installer/install.sh:1550`).
27. `systemctl enable --now hal0-lemonade` then **polls `:13305/api/v1/health` (20× 1s)** and pre-warms the Vulkan backend via `/api/v1/install` so first model load doesn't block on a binary pull (`installer/install.sh:1589-1627`).
28. `systemctl enable --now hal0-api`, wait_active 15s (`installer/install.sh:1630-1635`).
29. `hal0-openwebui` enabled if unit present (`installer/install.sh:1637-1647`).
30. `hal0-agent@hermes` + `hermes-gateway` enabled **only if** `/var/lib/hal0/venvs/hermes/bin/hermes` exists (gated — fresh installs skip; upgrades resume) (`installer/install.sh:1655-1681`).
31. Reachability discovery prints LAN/Tailscale/IPv6 dashboard URLs (`installer/install.sh:1688-1719`).

---

## 2. First-run / ownership flow (where the wizard hands off)

`first_run_lock()` → `/var/lib/hal0/.first-run.lock` (mode 0600, single-use OTP) is documented as
"dropped by `installer/install.sh`" at `src/hal0/config/paths.py:150-169`. **It is NOT** — install.sh
contains no `.first-run`/OTP logic (grep confirms only "first-run bundle manifests" at L1550 and a wizard
mention at L1834). The lock is actually dropped/consumed API-side in
`src/hal0/api/routes/installer.py`. **Doc-drift** (see findings). `bundle_chosen_marker()`
(`/var/lib/hal0/.bundle-chosen`) is dropped by `POST /api/bundles/{name}`, not the installer.

---

## 3. Update / rollback path

`src/hal0/updater/updater.py:843` `class Updater`. `apply()` (`:935-1109`) runs the §9 sequence:
fetch+validate manifest → confirm version → download tarball/sig/cert to `/var/lib/hal0/cache/<ver>/`
→ sha256 (`:1011-1022`) → cosign verify-blob (`:1024-1033`) → extract to
`_versioned_install_dir = /usr/lib/hal0/hal0-<ver>` (`:1038, :769-773`) → config migrations (`:1041-1057`)
→ atomic swap `current` symlink (`:1059-1070`) → **re-pip the swapped tree into the venv**, skipped for
editable installs (`:1072-1089`) → record `/var/lib/hal0/hal0.previous` (`:1091-1092`).
`rollback()` (`:1113`) reads `hal0.previous` and swaps `current` back.

`_is_editable_install()` (`:781-795`) returns True when `hal0.__file__` is outside `sys.prefix`
(i.e. `/opt/hal0/src/hal0`). `_reinstall_into_venv()` (`:798-827`) is
`pip install --no-deps --force-reinstall <install_dir>`.

---

## 4. FHS-vs-editable layout — the central trap (LIVE, not reconciled)

The task flagged a mismatch: runtime is editable `/opt/hal0` but the Updater targets
`/usr/lib/hal0/current`. **Fresh installs no longer hit it** — install.sh now lays down the FHS layout
(`/usr/lib/hal0` + `current` + shared non-editable venv, `installer/install.sh:179-192`). **But the
deployed CT105 reality is still an editable `/opt/hal0` checkout, and there the mismatch is a live trap:**

- `Updater.apply()` does **not refuse** on editable installs — it only skips the re-pip step
  (`src/hal0/updater/updater.py:1079`). Steps 6–8 still run: it extracts to
  `/usr/lib/hal0/hal0-<ver>/` and swaps the `/usr/lib/hal0/current` symlink — **neither of which an
  editable install (importing from `/opt/hal0/src`) ever reads** — then returns a success breadcrumb.
- The apply **route** (`src/hal0/api/routes/updater.py:433 apply_update`, `:202 _run_apply_job`) does
  not gate on `_is_editable_install()`.
- The **CLI** (`src/hal0/cli/update_commands.py:155 update`) does not refuse either — it only emits a
  yellow version-drift *warning* (`_warn_editable_version_drift`, `:77-102`).
- install.sh's own comment **falsely** claims "The updater refuses apply() in this mode (re-run
  `git pull && pip install -e .`)" (`installer/install.sh:169-170`).

Net: on the live editable deployment, `hal0 update` downloads + verifies + extracts + swaps a symlink
to no effect, skips the venv re-pip, and reports success. This is the exact mismatch the task wanted
surfaced — it is **not fixed**, only routed around for fresh FHS installs. (Matches memory note
`hal0_lxc_install_layout_mismatch`.)

---

## 5. Service-user model

- `hal0-api.service` + OpenWebUI prewire run as `HAL0_USER`, **default `root`** (`installer/install.sh:160, 640, 1225`). API binds `0.0.0.0:8080` (`installer/install.sh:165, 643`). Root + all-interfaces + no auth (auth removed, ADR-0012) is a notable posture for an OSS install.
- `lemond`, `hal0-agent@hermes`, and `hermes-gateway` run as the unprivileged system user `hal0` (nologin, home `/var/lib/hal0`) (`installer/install.sh:1291-1292, 1670-1671`). Matches memory `hal0_service_user_model`.
- `/dev/kfd` perms reset to root:root 0660 each boot inside the LXC; the `kfd-perms.conf` ExecStartPre re-chgrps it before lemond starts (`installer/install.sh:1301-1318`).

---

## 6. preflight.sh checks
`installer/lib/preflight.sh` (sourced; `hal0 doctor` execs the same file): `preflight_systemd` (:58),
`preflight_python` (:67), `preflight_arch` (:90), `preflight_venv` (:104), `preflight_writable` (:120),
`preflight_network` (:143), `preflight_docker` (:154), `preflight_disk` (:237), `preflight_ports` (:293),
`preflight_all` (:319). install.sh calls `preflight_systemd` (die-on-fail) + `preflight_writable` over
`$PREFIX /usr/lib/hal0 $ETC_DIR $UNIT_DIR` (`installer/install.sh:308, 339`).

---

## 7. packaging/
- `packaging/systemd/hal0-openwebui.service` — the only host-systemd unit shipped as a file (copied at install, image digest must be kept in sync with install.sh L666).
- `packaging/avahi/hal0.service` — mDNS `hal0.local` advert (memory `hal0_mdns_avahi_ct105`).
- `packaging/proxmox/hal0-test-template/provision.sh` — bakes a fresh-install **test** LXC template (operator user + bootstrap packages + **apparmor purged** so docker runs in an unconfined LXC + per-boot readiness oneshot). Test/CI tooling, not part of the user install path.
- `packaging/toolbox/{cpu.Dockerfile,kokoro,moonshine}` — legacy per-modality toolbox images (largely superseded by Lemonade in v2).

---

## Findings (fragility / rookie-mistake / gaps)

| Sev | Kind | Title | Location |
|-----|------|-------|----------|
| high | seam | `hal0 update` on editable `/opt/hal0` silently no-ops (extract+swap unread paths, skip re-pip, report success); apply route+CLI never refuse | `src/hal0/updater/updater.py:1079`, `src/hal0/api/routes/updater.py:433`, `src/hal0/cli/update_commands.py:155` |
| high | oss-blocker | `LEMONADE_SHA256` is all-zeroes placeholder → clean install without `HAL0_SKIP_LEMONADE_SHA=1` silently SKIPS Lemonade → no inference, dashboard up but chat dead | `installer/install.sh:1131-1138` |
| high | oss-blocker | `hal0-api` runs as **root** (HAL0_USER default) bound `0.0.0.0:8080` with no auth — privilege/exposure posture for a home/OSS box | `installer/install.sh:160, 640, 643` |
| med | doc-drift | install.sh comment claims "the updater refuses apply() in this mode" — it does not; only skips re-pip | `installer/install.sh:169-170` |
| med | doc-drift | `paths.py` docstring says `.first-run.lock` is "dropped by installer/install.sh" — installer has no such logic; it's API-side (`routes/installer.py`) | `src/hal0/config/paths.py:150-169` |
| med | doc-drift | install.sh header still says "standard install at /opt/hal0" / `HAL0_PREFIX default /opt/hal0` while the body defaults to `/usr/lib/hal0` | `installer/install.sh:5, 10` vs `:183` |
| med | coupling | OpenWebUI image digest hard-coded in two places that must be hand-synced (install.sh + packaging unit) | `installer/install.sh:664-666` |
| med | gap | Updater re-pip uses `--no-deps`; a release that changes deps installs new code against stale deps with no guard | `src/hal0/updater/updater.py:798-814` |
| low | fragility | `hal0.toml` `pull_root` patched by regex, not a TOML parser (acknowledged limitation; breaks on nested `[models.*]`) | `installer/install.sh:416-435` |
| low | fragility | Lemonade tarball download failure is non-fatal — install "succeeds" with inference silently unavailable | `installer/install.sh:1117-1121` |

---

## Cross-cutting seams (for other agents)

- **Slots agent**: legacy `hal0-slot@.service` was removed in PR-9 (`installer/install.sh:655-658`). At runtime `lemond` (`/opt/lemonade/lemond`, `:13305`) owns the child llama-server processes (8001+); `SlotManager` (`src/hal0/slots/manager.py:182`) dispatches via lemond `/v1/load` rather than spawning systemd units. Install lays down config + the lemond unit; **runtime spawn ownership is the slots agent's.**
- **API agent**: first-run OTP/`.first-run.lock` + bundle-chosen marker are dropped/consumed in `src/hal0/api/routes/installer.py`, not the installer — the wizard/ownership flow lives there. Update job orchestration + hal0-api self-restart-after-apply lives in `src/hal0/api/routes/updater.py:202 _run_apply_job`.
- **Config agent**: `src/hal0/config/paths.py` is the single FHS resolver; `HAL0_HOME` override is the test/dev seam. install.sh and the Updater must agree with it on `/usr/lib/hal0` + `/etc/hal0` + `/var/lib/hal0` roots.
- **CLI agent**: `hal0 update` / `hal0 doctor` (`doctor` execs `installer/lib/preflight.sh`) are the user-facing front doors to this subsystem; the editable-no-op trap surfaces through `cli/update_commands.py`.
