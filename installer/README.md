# hal0 installer

Non-interactive installer for hal0 — the open-source home AI inference platform.

## Quick start

```sh
# From a clone of this repo:
sudo bash installer/install.sh

# Or point HuggingFace pulls at a larger disk:
sudo bash installer/install.sh --models-dir=/mnt/ai-models
```

> The one-liner `curl -fsSL https://hal0.dev/install.sh | bash` ships
> with v1.0 once the `hal0.dev/install.sh` endpoint is wired; until then,
> `git clone` + `sudo bash` is the supported entry point.

The toolbox container images (`ghcr.io/hal0ai/hal0-toolbox-*:v1`) are
public on GHCR — `docker pull` works without a `docker login`. The
installer pulls them in the background after the API comes up.

## What the installer does

1. **Pre-flight checks** — confirms Python 3.11–3.14 is on `$PATH`, systemd is present (skipped in `--dev`), and probes for Docker (a soft warning if missing; slot launches will fail until it's installed).
2. **Privilege model** — runs as `root` (re-execs under `sudo` if needed). The slot template runs the toolbox containers as root by design — the container itself is the sandbox boundary, not the host user. Override with `HAL0_USER=...` if you want a different unit user, but the default is intentional.
3. **Layout** — creates `/opt/hal0/` (code + venv), `/etc/hal0/{,slots}` (config), `/var/lib/hal0/{models,registry,slots,openwebui,cache}` (state). In `--dev` everything lands under `$PWD/.hal0ai/` instead.
4. **Installs hal0** — creates a venv at `/opt/hal0/.venv/` and `pip install -e`'s the checkout into it. There is no versioned install dir or `current` symlink yet — the venv tracks the checkout in editable mode.
5. **Builds the dashboard UI** — runs `npm install && npm run build` in `ui/` if `ui/dist/` is missing and `npm` is available. Skipped (with a warning) when `npm` is absent.
6. **Config defaults** — writes `/etc/hal0/hal0.toml`, `api.env`, `openwebui.env` (rendered by `hal0.openwebui.env_writer`), and slot skeletons for `primary`, `embed`, `stt`, `tts`. Existing files are **never clobbered** on re-run.
7. **systemd units** — writes `hal0-api.service`, copies `hal0-openwebui.service` and `hal0-slot@.service` from `packaging/systemd/` to `/etc/systemd/system/`, reloads the daemon, enables and starts `hal0-api` + `hal0-openwebui` (unless `--no-start`).
8. **Hardware probe + final summary** — prints detected backends and reachable URLs. Skip the probe with `HAL0_NO_PROBE=1`.

The installer is **idempotent** — safe to re-run after a partial failure or to update configuration defaults.

### Pulling toolbox images

Toolbox images live under `ghcr.io/hal0ai/hal0-toolbox-*` and are **public**;
the installer (and `hal0 slot load` at runtime) pull them anonymously. No
`docker login` is required — even on a hardened LAN box that has never seen
a GHCR credential.

The pinned image digests are tracked in [`manifest.json`](../manifest.json)
and refreshed by `.github/workflows/toolbox.yml` after every build. Run
`hal0 doctor toolbox-pull` to verify each pinned image is reachable from
this host (anonymous OCI v2 token-exchange + HEAD on the manifest URL —
no `docker pull` needed).

## Environment variables

These are the variables `installer/install.sh` actually reads:

| Variable | Default | Description |
|---|---|---|
| `HAL0_PREFIX` | `/opt/hal0` (or `$PWD/.hal0ai` in `--dev`) | Installation root (venv + code) |
| `HAL0_PORT` | `8080` | hal0 API port |
| `HAL0_USER` | `root` | systemd unit user (see §What the installer does) |
| `HAL0_PYTHON` | `python3` | Python interpreter used to build the venv |
| `HAL0_MODELS_DIR` | _(unset)_ | Absolute path where HuggingFace pulls land; same as `--models-dir=PATH`. When unset, models live at `/var/lib/hal0/models` (or `$PWD/.hal0ai/var/lib/hal0/models` under `--dev`). |
| `HAL0_NO_PROBE` | _(unset)_ | Set to `1` to skip the hardware probe at the end |
| `HAL0_TOOLBOX_IMAGE_VULKAN` | _(unset)_ | Override the Vulkan toolbox image ref written into `api.env` |
| `HAL0_TOOLBOX_IMAGE_ROCM` | _(unset)_ | Override the ROCm toolbox image ref written into `api.env` |
| `HAL0_PUBLIC_HOST` | `hal0.local` | Public hostname rendered into the Caddyfile (TLS default install path) |
| `HAL0_TLS_EMAIL` | `admin@$HAL0_PUBLIC_HOST` | Contact email for Let's Encrypt (when not `tls internal`) |
| `HAL0_OPENWEBUI_PORT` † | `3001` | OpenWebUI host port — **dev mode only** |

† `HAL0_OPENWEBUI_PORT` is honored by `scripts/dev-bootstrap.sh` (the dev-mode launcher). The installed `hal0-openwebui.service` hardcodes `:3001`; to change it post-install, edit `/etc/systemd/system/hal0-openwebui.service` and reload.

Example:

```sh
HAL0_PORT=9090 sudo bash installer/install.sh
```

## Authentication

Per [ADR-0001](../docs/adr/0001-collapse-edge-auth-into-fastapi.md), **all auth
now lives in FastAPI** — there is no edge-auth layer in Caddy. The Caddyfile is
a dumb TLS terminator + reverse proxy (`packaging/caddy/Caddyfile.template`,
~42 lines, no `basicauth`, no path matchers, no allowlist).

As of v1.0 (security review §36, 2026-05-21), a fresh install **starts
locked**. The API rejects anonymous requests on every admin route and
on `/v1/*` with `401 auth.required`. Only the first-run wizard claim
paths (`/api/install/*`, `POST /api/auth/password`) stay reachable —
and only while the installer's one-time-OTP lockfile
(`/var/lib/hal0/.first-run.lock`, mode 0600) is present on disk.

The installer prints the OTP in the post-install summary; the wizard's
"Set a password" step asks for it before minting the owner password.
Once the password is set, the wizard's success path deletes the
lockfile and the claim window closes — from then on every request
needs a session cookie (browser) or Bearer token (programmatic
client).

To opt back into the pre-v1 trusted-LAN open posture (single-user dev
boxes only), uncomment `HAL0_AUTH_DISABLED=1` in `/etc/hal0/api.env`
and restart `hal0-api`. The dashboard and `/v1/*` will be reachable
without credentials. **Do not use this on a multi-tenant network.**

Once a password is set:

- Browsers authenticate via `POST /api/auth/login`, which issues a signed
  `hal0_session` cookie (HttpOnly, SameSite=Lax, Secure-when-TLS).
  `POST /api/auth/logout` clears it.
- Programmatic clients keep using Bearer tokens (`Authorization: Bearer hal0_...`)
  unchanged — the FastAPI middleware accepts both the session cookie and
  Bearer tokens against the same `require_token` / `require_writer` deps.

Mint a Bearer token via the Settings UI (Authentication panel → Create token)
or directly:

```sh
curl -k -H 'Authorization: Bearer hal0_<admin-token>' \
  https://hal0.local/api/auth/tokens \
  -H 'Content-Type: application/json' \
  -d '{"label": "openwebui-bridge", "scope": "all"}'
```

The raw token is in the response **once** — copy it immediately. To revoke:

```sh
curl -k -H 'Authorization: Bearer hal0_<admin-token>' -X DELETE \
  https://hal0.local/api/auth/tokens/<token-id>
```

### TLS

The default install path runs Caddy in front of FastAPI for TLS termination.
The `tls internal` directive in the rendered Caddyfile mints a self-signed
certificate via Caddy's internal CA — picked automatically when
`HAL0_PUBLIC_HOST` ends in `.local` or is `localhost`. For a real
DNS-resolvable hostname, set `HAL0_PUBLIC_HOST` and `HAL0_TLS_EMAIL`; Caddy
provisions a Let's Encrypt cert on the first reload.

If avahi-daemon is running, the installer drops `/etc/avahi/services/hal0.service`
so `hal0.local` resolves on the LAN. Without avahi, add a static `/etc/hosts`
entry on each client: `<hal0-ip>  hal0.local`.

### Skip Caddy entirely (`--no-tls`)

```sh
sudo bash installer/install.sh --no-tls
```

`--no-tls` skips the Caddy install and Caddyfile render. FastAPI binds
`0.0.0.0:8080` and is reachable directly at `http://<host>:8080/`. This is
the right path when hal0 sits behind an existing reverse proxy (for example,
the staging deployment behind Traefik) — front it with whatever TLS and auth
layer your edge already provides.

`--dev` implies `--no-tls`; there is no system Caddy install in a dev tree.

### Upgrade notes

Existing installs that used the old `--auth=basic` path lose **edge auth**
on next install upgrade — the Caddyfile no longer carries `basicauth`.
And as of v1.0 (security review §36), the FastAPI gate is on by
default: anonymous requests get 401 on admin and `/v1/*` routes. To
recover:

- **Set a password in the dashboard wizard.** On first load after upgrade,
  the wizard's password-setup step calls `POST /api/auth/password` and
  writes the bcrypt hash into the FastAPI auth store. The installer
  also prints a one-time OTP that the wizard requires for first-run
  claim; copy it out of the installer transcript or read it directly
  from `/var/lib/hal0/.first-run.lock`.
- **Install with `--no-tls`** and front hal0 with your own reverse proxy
  (Traefik, nginx, Caddy you manage outside hal0, Cloudflare Tunnel, etc.).
  Your edge owns auth. Setting `HAL0_AUTH_DISABLED=1` in
  `/etc/hal0/api.env` collapses hal0's own gate back to pass-through;
  do this only when the outer proxy is the trust boundary.

Bearer tokens minted under the prior install continue to work — token storage
moved with the rest of auth into the FastAPI store (no migration required).
The Caddy `basicauth` credentials themselves are not migrated; re-entry via
the wizard's password-setup step is the supported path.

## Dev mode (`--dev`)

Runs the full installer logic but lays everything under `$PWD/.hal0ai/` instead of FHS paths. systemd units are **not** installed or enabled — they are written to `$PWD/.hal0ai/etc/systemd/system/` for inspection only.

```sh
bash installer/install.sh --dev
```

Use `scripts/dev-bootstrap.sh` to actually start services during development.

### `--dev` mode limitations

`--dev` is a contributor convenience, not a runtime path. The installer writes the same systemd units (`hal0-api.service`, `hal0-slot@.service`, `hal0-openwebui.service`) into the dev tree, but it does **not** register them with the host's systemd. Concretely:

- Units land in `$PWD/.hal0ai/etc/systemd/system/`.
- The host's `systemctl` only searches `/etc/systemd/system/` and `/usr/lib/systemd/system/`, so it cannot see them.
- Any flow that ends in `systemctl start hal0-slot@<name>` will fail with:

  ```
  Failed to start hal0-slot@primary.service: Unit hal0-slot@primary.service not found.
  ```

  In particular, `hal0 slot create && hal0 slot load` cannot bring a slot up in `--dev`.

Two ways to resolve this, depending on what you're trying to do:

1. **Just do a real install.** This is the supported runtime path:

   ```sh
   sudo bash installer/install.sh
   ```

   Real install puts units under `/etc/systemd/system/`, runs `systemctl daemon-reload`, and `hal0 slot load` works end-to-end.

2. **Or link the dev units into the system search path.** Keeps the dev tree as the source of truth, but tells the host systemd where to find the units:

   ```sh
   sudo systemctl link "$PWD/.hal0ai/etc/systemd/system/hal0-slot@.service"
   sudo systemctl link "$PWD/.hal0ai/etc/systemd/system/hal0-api.service"
   sudo systemctl link "$PWD/.hal0ai/etc/systemd/system/hal0-openwebui.service"
   sudo systemctl daemon-reload
   ```

   After that, `hal0 slot create && hal0 slot load` works against the dev tree. Edits to the linked unit files take effect after another `systemctl daemon-reload`.

The installer prints the same warning block at the end of every `--dev` run as a reminder. Tracked under harness finding #6 / task #24.

## Uninstall

```sh
# Prompts before deleting config + model data
sudo bash installer/uninstall.sh

# Keep /etc/hal0 and /var/lib/hal0 (models, registry, OpenWebUI state)
sudo bash installer/uninstall.sh --keep-data

# No confirmation prompt (CI / scripted teardown)
HAL0_FORCE=1 sudo bash installer/uninstall.sh
```

## Troubleshooting

### Port already in use

```
✗ pre-flight failed: port 8080 is already in use.
```

Find the process: `lsof -i :8080`  
Then either stop it or re-run with a different port:

```sh
HAL0_PORT=8090 sudo bash installer/install.sh
```

After changing the port, update `/etc/hal0/api.env` and `/etc/hal0/openwebui.env` to match, then `systemctl restart hal0-api hal0-openwebui`.

### Docker not installed

```
✗ pre-flight failed: docker not installed.
```

Install Docker:

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

### Docker daemon not accessible

```
✗ pre-flight failed: docker daemon not running or not accessible.
```

Start the daemon: `sudo systemctl start docker`  
Or add your user to the docker group and re-login.

### Not enough disk space

```
✗ pre-flight failed: less than 20GB free in /var/lib
```

Free up space, or redirect HuggingFace pulls to a larger disk:

```sh
sudo bash installer/install.sh --models-dir=/mnt/large-disk/hal0-models
```

The installer records this in `/etc/hal0/hal0.toml` under
`[models].pull_root` so subsequent `hal0 model pull` calls honor it too.

### Services won't start

Check logs:

```sh
journalctl -fu hal0-api
journalctl -fu hal0-openwebui
systemctl status hal0-api hal0-openwebui
```

### OpenWebUI can't reach the API

OpenWebUI is configured to talk to `http://127.0.0.1:8080/v1`. If you changed `HAL0_PORT`, update `/etc/hal0/openwebui.env`:

```
OPENAI_API_BASE_URLS=http://127.0.0.1:<new-port>/v1
```

Then `systemctl restart hal0-openwebui`.
