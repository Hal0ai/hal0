# P3-quadlet: migrate slot + companion units to Podman Quadlet `.container`

**Repo:** `/home/mint/hal0` @ `rework/descar` · **Plan anchors:** `/home/mint/hal0-rework-plan.md` §7.2 (L453-474), §17 (L1060-1099), §23.4 (W4 sequencing) · **Mode:** READ-ONLY spec, verified against code · **Depends on:** **P3-perms** (HAL0-SUNSET ship gate; `hal0-systemctl` helper for the residual privileged writes; `hal0-api User=hal0` so writes under `/etc/containers/systemd/` happen with no perms detour).

## 0. Executive summary

hal0's per-slot runtime is hand-rendered text — `providers/container.py:_render_unit_from_plan` (`:436-543`) stringifies a `RuntimeLaunchPlan` into a `[Unit]/[Service]/[Install]` skeleton (`_unit_skeleton`, `:362-433`), writes it to `/etc/systemd/system/hal0-slot@<name>.service`, then runs the daemon-reload → enable → restart dance (`_write_and_start_unit`, `:1199-1231`). Every companion service (`hal0-openwebui.service`, `hal0-podman-forward.service`, the `hal0-slot@img.service` ComfyUI unit) repeats the same imperative `podman run …` pattern. **Target:** replace those hand-written ExecStart lines with declarative Podman **Quadlet `.container` units** (dropped under `/etc/containers/systemd/hal0-slot-<id>.container`); the generator emits the `[Unit]/[Service]/[Install]` skeleton and podman auto-replaces stale containers on start. Delete the entire `argv`-assembly code path in `_render_unit_from_plan` (lines `:466-535`, the `podman run --rm --name=… --replace --log-driver=none --network=… --device=… --group-add=… --security-opt=… --volume=… --env=… --publish=…` chain), the `ExecStartPre=-podman rm -f` cleanup (`:412`), the `ExecStop=podman stop -t 20` (`:427`), the `RequiresMountsFor=` ordering, and the `daemon-reload + enable + restart` triple (`_write_and_start_unit`). The companion service files (`hal0-openwebui.service`, `hal0-podman-forward.service`) collapse to ~30 lines each — Quadlet `.container` plus a `Restart=always` override drop-in. The `exec`-argv surface becomes a typed `[Container]` `Exec=` field; mount/device/env/port become first-class Quadlet keys; SELinux `:z` relabel is a `Volume=` option, not an exec flag. **Standalone deliverable + cross-lane seam contract:** the Quadlet unit is the *output*; `container_spec()` and the plan dataclass (`RuntimeLaunchPlan`, `Mount`, `HealthCheck`) remain the single input shape — slot providers don't change. **Docker is unsupported** — drop the docker-fallback in `_container_runtime()` (`:297-319`) and the docker-specific ExecStartPre block, both anchors of the misleading `.hal0ai`-shaped "we run docker too" UX.

**Hard prereqs (§23.4 W4 sequencing):** **P3-perms** must land first — without `User=hal0` on `hal0-api`, writes to `/etc/containers/systemd/` fail with EACCES and the unit's root-ownership-by-design seam collapses into the same chown-phase scar P3-perms was created to delete. **§11.1 slot id-keying** (slot `name` → stable opaque `id` as primary key) **must land before or alongside** this spec — the unit name becomes `hal0-slot-<id>` (`@` is illegal in Quadlet's filename syntax; the template-instance `hal0-slot@<x>.service` we use today is a systemd-only construct). **§11.2 PortAuthority** must own slot ports — Quadlet's `PublishPort=` consumes the same port authority via `port_claim`, and hand-set TOML ports disappear.

---

## PART 0 — Current-state map (the hand-rendered ExecStart scars, file:line)

### `src/hal0/providers/container.py` — the renderer to delete (260 lines, half the file)
- `ContainerProvider.container_spec` `container.py:1030-1102` — returns `RuntimeLaunchPlan`; **stays** (the input shape, every provider builds one).
- `_render_unit_from_plan` `container.py:436-543` — **THE renderer to delete.** Builds the argv list `runtime, "run", "--rm", "--name=…", "--replace"`, `--log-driver=none`, `--network=…`, `--device=…`, `--group-add=…`, `--security-opt=…`, `--volume=…`, `--env=…`, `--publish=…`, `--health-*`, `extra_args`, then joins with `shlex.quote` into a single ExecStart line `:534-535`. All of this evaporates.
- `_unit_skeleton` `container.py:362-433` — wraps ExecStart in `[Unit]/[Service]/[Install]`. Quadlet generates the equivalent skeleton; **delete with the renderer**.
- `_render_unit` `container.py:546-581` — scalar-arg back-compat shim; builds the plan, calls `_render_unit_from_plan`. Delete with it (back-compat only for tests; `tests/providers/test_container.py` mock-injects the renderer).
- `_render_unit_from_spec` `container.py:584-596` — back-compat alias for `_render_unit_from_plan`. Delete with it.
- `_container_runtime` `container.py:297-319` — docker fallback chain (`:313` candidates `["/usr/bin/podman", "/usr/bin/docker", "podman", "docker"]`). **Drop docker.** Podman is the only runtime Quadlet supports anyway; deleting docker ends the "do we support docker?" hand-wringing in `container.py:34-37` and `:468-473`.
- `_render_unit_text` `container.py:1233-1269` — calls `_render_unit_from_plan`, runs the full template text through the renderer. Becomes **`_render_quadlet_text`** writing to `/etc/containers/systemd/hal0-slot-<id>.container`.
- `ContainerProvider._write_and_start_unit` `container.py:1199-1231` — writes the unit, runs `daemon-reload`, `enable`, `restart`. Becomes **`_write_and_reload`** (`daemon-reload` only; `systemctl start` happens through the new quadlet-generated `hal0-slot-<id>.service` unit which is auto-enabled by `[Install] WantedBy=hal0.target`).
- `ContainerProvider.load_sync` `container.py:1271-1304` — calls `_render_unit_text` + `_write_and_start_unit`. Stays as the public load entry; its internals become "build plan → render quadlet text → drop file → `hal0-systemctl daemon-reload`" (via the P3-perms `SystemCtlSeam`).
- `ContainerProvider.unload_sync` `container.py:1362-1378` — `systemctl stop hal0-slot@<name>.service`, `reset-failed`, `disable`, `unlink`, `daemon-reload`. Becomes "delete `/etc/containers/systemd/hal0-slot-<id>.container` + `hal0-systemctl daemon-reload`" (Quadlet auto-stops and auto-disables via `WantedBy=` when the source file vanishes).
- `ContainerProvider.rerender_unit_sync` `container.py:1306-1339` — re-renders unit text, writes if changed, batches a `daemon_reload`. Stays; the renderer just becomes `_render_quadlet_text`. The "byte-identical fresh-vs-updated" invariant becomes a property of Quadlet itself (the generator is deterministic).
- `ContainerProvider._unit_name` `container.py:1106-1110` — returns `hal0-slot@<slot_name>.service`. Becomes a derivation of the slot `id` (not `name`), and the systemd unit name is the Quadlet-generated `hal0-slot-<id>.service` (no `@` template-instance; one unit per slot, since Quadlet can't generate template instances from one .container file).
- `ContainerProvider._unit_path` `container.py:1108-1110` — `Path("/etc/systemd/system") / unit_name`. Becomes `Path("/etc/containers/systemd") / f"hal0-slot-<id>.container"`. **Root-owned by design**, written through `hal0-systemctl write-quadlet <id>` (new subcommand on the P3-perms helper).
- `ContainerProvider.daemon_reload` `container.py:1341-1343` — `systemctl daemon-reload`. Becomes `hal0-systemctl daemon-reload` (still gated through the seam).
- `ContainerProvider.running_image` `container.py:1403-1431` — `<runtime> inspect hal0-slot-<name> --format {{.ImageName}}`. Container name **changes from `hal0-slot-<name>`** to Quadlet's auto-generated `<unit-basename>` (default = the unit's basename minus `.service` = `hal0-slot-<id>`). Verify Quadlet doesn't rename (it doesn't) and update the container-name lookup. (Podman Quadlet uses the unit basename as the container name by default; we don't override `ContainerName=`.)
- `ContainerProvider.running_argv` `container.py:1433-1464` — same `--format {{json .Config.Cmd}}`. Stays; the command is whatever Quadlet emits.
- `ContainerProvider.is_active` `container.py:1380-1383` — `systemctl is-active <unit>`. Stays, retargeted to `hal0-slot-<id>.service`.
- `ContainerProvider.image_present` `container.py:1385-1401` — `<runtime> image inspect <image>`. Stays.
- `ContainerProvider.pull_image_stream` `container.py:1466-1542` — async pull with layer-progress parse. Stays.

### Companion units (the same pattern, three files)
- `packaging/systemd/hal0-openwebui.service` — full hand-rendered `ExecStart=/usr/bin/podman run --rm --name hal0-openwebui --env-file /etc/hal0/openwebui.env -v /var/lib/hal0/openwebui:/app/backend/data -p 0.0.0.0:3001:8080 --add-host=host.docker.internal:host-gateway --security-opt apparmor=unconfined ghcr.io/open-webui/open-webui@sha256:7f1b0a1a…` plus `ExecStartPre=-podman pull …` + `ExecStartPre=-podman rm -f hal0-openwebui` + `ExecStop=podman stop hal0-openwebui`. Migrates to `packaging/quadlet/hal0-openwebui.container` + a thin `packaging/systemd/hal0-openwebui.service.d/override.conf` carrying only `[Service] Restart=always RestartSec=5` (the bespoke restart policy Quadlet doesn't model). The `--add-host=host.docker.internal:host-gateway` **dies** — podman's network namespace already gives the container `host.containers.internal` natively, and `host.docker.internal:host-gateway` is the docker-bridge hack. With Quadlet + podman, `host.containers.internal` resolves the same way; OpenWebUI's `OPENAI_API_BASE_URLS` config flips from `host.docker.internal` to `host.containers.internal` once, written by `openwebui.env_writer` (no operator action).
- `packaging/systemd/hal0-podman-forward.service` — iptables FORWARD ACCEPT repair (one-shot, not container-managed). **Stays as a regular systemd service** (no Quadlet applicability — no container to declare). Reference here so the spec doesn't accidentally fold it.
- `installer/systemd/hal0-api.service` (or the inline write at `install.sh:922-955`) — `hal0-api` runs the Python daemon, not a container. **Stays.**
- `installer/systemd/hal0-agent@.service` (template) — runs the Python Hermes agent per-id, not a container. **Stays.**
- `installer/systemd/hal0-bench.service`, `hal0-bench-worker.service`, `hal0-honcho-sync.service`, `hal0-honcho-sync.timer`, `hal0-bench.timer`, `hindsight-api.service` — none of these launch containers. **Stay.**
- The slot ComfyUI unit `hal0-slot@img.service` is **not** a hand-rendered unit — it's generated by `ContainerProvider` for the `comfyui` runtime family via `_spec_provider_for → ComfyUIProvider.container_spec` (`container.py:789-792`, `api/routes/comfyui.py:25,61,148,793`, `comfyui/provision.py:85`). Migrates transparently with the rest of the slot units — same `_render_quadlet_text` writes the `.container` for it; `ComfyUIProvider.image_ref` + `Mount.coerce` calls stay unchanged.

### The docker-fallback scar + the misleading `.hal0ai` UX
- `container.py:295-319` (`_container_runtime`) — checks `["/usr/bin/podman", "/usr/bin/docker", "podman", "docker"]` for a runtime. Docker is a documented anti-pattern: podman's `--network=host`, `:z` SELinux, rootless+`Type=notify` sd_notify are the reasons hal0 picked podman. Quadlet is **podman-only**; the docker fallback is unreachable in practice. **Delete the docker candidates.**
- `container.py:34-37, :390-392, :468-473, :491, :1476` — comments + code that hand-hold "docker does/doesn't do X." Every reference dies with the fallback.
- `container.py:744` — `[server].env → docker run --env (e.g. HSA_OVERRIDE_GFX_VERSION) so operators can tune the runtime without forking the image`. The `--env` semantically survives as Quadlet `Environment=`, but the comment's "docker run" naming goes.
- The `.hal0ai` UX scar: `$PWD/.hal0ai/` is the **dev-mode install prefix** (`installer/README.md:46,87,121,133,150,151`; `installer/install.sh:105,151`; `installer/uninstall.sh:13,147,168`; `cli/main.py:253`), not a docker template. It exists because dev-mode lays files under `.hal0ai/etc/systemd/system/` for inspection without enabling them. With Quadlet, dev-mode adds `packaging/quadlet/` writes to `$PWD/.hal0ai/etc/containers/systemd/` (the generator runs in dev mode too, since the .container file is the source). The misleading "we support docker" read comes from the install README's mention of `host.docker.internal` in OpenWebUI's `--add-host` (`packaging/systemd/hal0-openwebui.service:55`); deleting that line kills the misleading read.

### Slot unit naming today (the `@`-template instance)
- `ContainerProvider._unit_name` `container.py:1106-1110` — `hal0-slot@{slot_name}.service`.
- Used at: `api/_settings_apply.py:9`, `api/routes/comfyui.py:25,61,148,793`, `api/routes/journal.py:12`, `api/routes/backends.py:447`, `api/routes/installer.py:556,558,628`, `api/routes/logs.py:66,67`, `api/routes/board_chat.py:140`, `api/routes/slots.py:876,945,1400,1451,1517`, `cli/app_commands.py:31`, `cli/slot_commands.py:325`, `dispatcher/npu_swap_status.py:7`, `dispatcher/npu_trio.py:4`, `install/extensions.py:163`, `slot_view/__init__.py:439`, `slots/__init__.py:3`, `services/registry.py:12`. **All retarget to `hal0-slot-<id>`** under §11.1. This is a wide search-and-replace, but each call site is a literal string concat — `f"hal0-slot@{name}.service"` → `f"hal0-slot-{slot.id}.service"` (or routed through a helper that names the unit by id, see §B.5).

---

## PART A — Current scars (in plain English)

### A.1 The hand-rendered ExecStart chain (the structural scar)

Every slot launch stringifies a `RuntimeLaunchPlan` into a 200+ token `podman run …` argv (`container.py:475-535`), wraps it in a systemd skeleton (`container.py:396-433`), writes the file, runs `systemctl daemon-reload`, `enable`, `restart` (`container.py:1223-1227`). The same argv is also the only place container lifecycle is encoded — there's no declarative statement of "this is a container; here's its image, its mounts, its port, its devices." To change the SELinux relabel you edit a string literal; to add a CDI device you edit the `argv` list builder; to switch the runtime from `podman` to `podman --cgroup-manager=cgroupfs` you patch the renderer. Quadlet makes each of those declarative, readable in the unit file, and overridable via a drop-in.

### A.2 The docker-fallback code path

`container.py:297-319` resolves a runtime from `[podman, docker]` candidates. In practice docker is never selected (the install deploys podman exclusively — `packaging/systemd/hal0-openwebui.service:52`, the box image only ships podman). The fallback exists because: (a) the container.py docstring (`:34-37`) was authored when docker was still an option, (b) the `--replace` flag has a docker-only shim (`:390-392`), (c) the `ExecStartPre=-podman rm -f` cleanup is the "docker-safe" fallback for docker's lack of `--replace` (`:468-473, 491`). Quadlet makes the runtime choice at the unit-generator layer; docker doesn't have a Quadlet equivalent; we delete the fallback and the comment cargo cult. The user's rework plan (`/home/mint/hal0-rework-plan.md:458-459` §7.2) explicitly calls for this: *"Migrate slot units to Quadlet `.container` files, which deletes the hand-rendered `podman run` string assembly … Delete the misleading docker-referencing `.hal0ai` slot template; treat docker as unsupported."*

### A.3 The companion services (OpenWebUI)

`packaging/systemd/hal0-openwebui.service:52` ExecStart is a literal 6-line `podman run --rm --name … --env-file … -v … -p … --add-host=… --security-opt … <image>` string. Updating the image digest means editing the unit file and `systemctl daemon-reload`. Quadlet: edit the `Image=` line (or `ImageDigest=`), `systemctl daemon-reload`, podman restarts the container with the new image. The `host.docker.internal:host-gateway` hack dies with the docker-fallback cleanup (podman users `host.containers.internal` natively).

### A.4 The slot unit naming convention

`hal0-slot@<name>.service` is a systemd template-instance — one template unit, many instances. Quadlet can't generate template instances from a single `.container` file; each instance is its own `.container` file (`hal0-slot-<id>.container`). This is a forced break with the `@`-template pattern and is the alignment point with §11.1: slot `id` (stable, opaque, numeric) becomes the unit filename suffix, and slot `name` (mutable label) is irrelevant to unit identity. Slot TOML rename → no unit rename needed; one `.container` file per slot, owned by `id`.

### A.5 Why the `--replace` + ExecStartPre dance exists

`container.py:481-491` adds `--replace` to every podman argv so a stale same-name container record from an unclean shutdown doesn't fail the next start (`#721`). The `ExecStartPre=-{runtime} rm -f` (`container.py:412`) is the docker fallback for the same problem. **Quadlet solves this for free:** it tracks the container's lifecycle via the generator, on unit start it stops/cleans the previous container by the unit basename, on unit stop it stops the container. The `--replace` and `ExecStartPre` both die. The `_run("systemctl", "reset-failed", unit, check=False)` line in `unload_sync` (`:1371`) also dies — Quadlet's `StopSec=` / `Restart=` semantics don't leave failed sub-state the same way.

### A.6 The hand-rendered `RequiresMountsFor=` ordering

`container.py:401-403` joins mount sources into `RequiresMountsFor=` so a slot on `/mnt/ai-models` orders after the mount. Quadlet's `Volume=` accepts the same source path and podman/Quadlet knows to wait on the mount via `RequiresMountsFor=` (auto-emitted). The hand-built string dies; the ordering survives.

---

## PART B — Target: Quadlet `.container` per slot (and per companion)

### B.1 Quadlet field mapping (the load-bearing translation table)

Every `podman run …` flag becomes a typed `.container` key. The mapping is **the contract** for this spec — if a flag is not in the table, it's not preserved.

| podman run flag (current `container.py:475-535`) | Quadlet `.container` field | Notes |
|---|---|---|
| `--rm` | `AutoRemove=yes` (default in Quadlet; do not set) | Quadlet always auto-removes on stop. |
| `--name=<container>` | `ContainerName=<name>` (default = unit basename) | We don't override; Quadlet uses `hal0-slot-<id>` as the container name, matching the existing name. |
| `--replace` | (none — Quadlet handles) | Quadlet stops/cleans the prior container on start. |
| `--log-driver=none` | `LogDriver=none` (or omit; podman default for Quadlet is `journald` for `Type=notify` units) | Keep `none` for slots so journald isn't double-fed (the `B3` comment, `container.py:486-492`). |
| `--network=<mode>` | `Network=<mode>` | |
| `--device=<path>` | `AddDevice=<path>` | Per-device, same semantics. NVIDIA CDI uses `AddDevice=nvidia.com/gpu=all` (or `=0`, `=1`). |
| `--group-add=<gid>` | `GroupAdd=<gid>` (or `--group-add=keep-groups` for runtime-resolved video GIDs) | Numeric GID keeps the `_gpu.py:resolve_gpu_group_ids()` helper as the single source. |
| `--cap-add=<cap>` | `AddCapability=<cap>` | |
| `--security-opt=<opt>` | `SecurityOpt=<opt>` | One per line; multi-value list. |
| `--volume=<src:dst[:ro][,z]>` | `Volume=<src>:<dst>:<opts>` where `<opts>` is a comma-list (`ro,z` or `ro,Z`) | `Mount.render()` (`base.py:46-63`) already produces the `<src>:<dst>[:opts]` shape; the Quadlet renderer just inverts the commas/colon (`src:dst:z` instead of `src:dst,z`). New helper `Mount.render_quadlet()` returns `src:dst:z`; old `render()` stays for the one caller (`container.py:510`) which dies with the renderer. |
| `--env=<k>=<v>` | `Environment=<k>=<v>` (one per line; `EnvironmentFile=` for the file-style alt) | |
| `--publish=<host>:<port>:<port>` | `PublishPort=<host>:<port>:<port>` | `<host>` is loopback (`127.0.0.1`) by default; widened via `[slots].publish_host` (§A1, unchanged). |
| `--health-*` | `HealthCmd=`, `HealthStartPeriod=`, `HealthInterval=`, `HealthRetries=`, `HealthTimeout=` | `HealthCheck.render_flags()` (`base.py:91-103`) becomes `HealthCheck.render_quadlet()` returning a list of these. |
| `extra_args` (`--ulimit memlock=-1` etc.) | drop-in support via `container_extra_args` in `[Container]` — **NOT a first-class field.** Quadlet doesn't model free-form exec flags; the escape hatch is a `hal0-slot-<id>.container.d/extra.conf` drop-in the operator can hand-edit. **Document this** in the operator guide; `extra_args` on the `RuntimeLaunchPlan` becomes **deprecated** in this spec, kept for one release as a no-op (logging a warning) so an operator's hand-authored extra_args survives the upgrade. |
| `<image>` | `Image=<ref>` | Pull policy: `PullPolicy=missing` (default) or `never` for air-gap. |
| `<command>` (the argv *after* the image) | `Exec=<token> <token> …` (one Exec= line, space-separated; multi-line via backslash continuation or `Exec=` repeated — Quadlet accepts repeated `Exec=` keys appended) | The llama-server argv (last-wins merged per `_llama_argv_segments` in `container.py:599-686`) becomes a single `Exec=` line. |
| `--security-opt apparmor=unconfined seccomp=unconfined` | `SecurityOpt=apparmor=unconfined` + `SecurityOpt=seccomp=unconfined` | One per line. |

**SELinux `:z` / `:Z`:** Quadlet supports `Volume=src:dst:z` (shared) and `src:dst:Z` (private). `Mount.selinux="z"` → `Volume=src:dst:z`. **NFS gotcha (already noted in `/home/mint/hal0-rework-plan.md:1606`):** detect `statfs f_type==0x6969` and OMIT the relabel option entirely. The renderer takes a `Mount` and consults `paths.is_nfs(src)` (new helper, returns True on NFS) — if NFS, `Volume=src:dst:ro` (no `:z`). Mount-time detection keeps the Quadlet file deterministic (no need to re-render on mount-state changes).

**[Install] section:** Quadlet auto-generates `[Install] WantedBy=hal0.target` (or `default.target`). The slot unit gets `WantedBy=hal0.target` so it starts on boot (hal0.target is the unit target existing companion units use; verify `hal0.target` is in the unit dep tree — if not, use `multi-user.target`).

**Restart policy:** the current hand-rendered `Restart=no` (`:407`) is the root cause of `slots/manager._fail_watch_loop` having to manually restart failed slots. Quadlet's `Restart=always` (set in the `[Service]` override drop-in) lets systemd handle the restart; `_fail_watch_loop` keeps its health-probe role but no longer drives restarts. **Verify the watchdog simplification is coordinated with P3-slots** (`/home/mint/hal0-specs/spec-p3-slots.final.md` §1(b′)) before changing the policy.

### B.2 The `.container` template (sample, for a llama-server slot)

```ini
# hal0-slot-<id>.container — generated by ContainerProvider (Podman Quadlet).
# Do not edit; regenerated on every slot load.
[Unit]
Description=hal0 container inference slot (<name>)

[Container]
Image=ghcr.io/hal0ai/hal0-toolbox-rocmfp4@sha256:abc…
ContainerName=hal0-slot-<id>
AutoRemove=yes
LogDriver=none
Network=host
AddDevice=/dev/kfd
AddDevice=/dev/dri/renderD128
GroupAdd=993
GroupAdd=44
SecurityOpt=apparmor=unconfined
SecurityOpt=seccomp=unconfined
Volume=/mnt/ai-models:/mnt/ai-models:ro,z
Environment=HSA_OVERRIDE_GFX_VERSION=11.5.1
Environment=HAL0_RUNTIME=container
PublishPort=127.0.0.1:8081:8081
HealthCmd=curl -fsS http://127.0.0.1:8081/health || exit 1
HealthStartPeriod=180s
HealthInterval=30s
HealthRetries=3
HealthTimeout=5s
Exec=llama-server --host 0.0.0.0 --port 8081 --model /mnt/ai-models/foo.gguf --alias foo --ctx-size 8192 -ngl 999 --parallel 1 --kv-unified

[Service]
Restart=always
RestartSec=3
```

Quadlet auto-generates a systemd unit `hal0-slot-<id>.service` from this; `systemctl daemon-reload` + `systemctl start hal0-slot-<id>` works as before.

### B.3 What deletes from `container.py`

| Symbol | Lines | Reason |
|---|---|---|
| `_unit_skeleton` | `:362-433` | Quadlet generates the skeleton. |
| `_render_unit_from_plan` | `:436-543` | argv assembly evaporates. |
| `_render_unit` | `:546-581` | Back-compat shim; renderer is gone. |
| `_render_unit_from_spec` | `:584-596` | Back-compat alias. |
| `_container_runtime` (docker fallback) | `:297-319` | Strip `/usr/bin/docker` + `docker` PATH candidates; rename to `_podman_runtime()` returning `/usr/bin/podman` or `$HAL0_CONTAINER_RUNTIME`. |
| The `is_podman` branch (`:473`) | `:473` | Always podman; delete. |
| The `ExecStartPre=-{runtime} rm -f` block | (inside `_unit_skeleton`, `:412`) | Quadlet handles. |
| The `ExecStop=podman stop -t 20` + `ExecStopPost=-{runtime} rm -f` lines | (inside `_unit_skeleton`, `:427`) | Quadlet handles. |
| `_render_unit_text` (replaced) | `:1233-1269` | Becomes `_render_quadlet_text`. |
| `_write_and_start_unit` (replaced) | `:1199-1231` | Becomes `_write_and_reload` (writes the .container, calls `hal0-systemctl daemon-reload`). |
| The `Mount.render()` call inside the renderer | `:510` | Renderer is gone. |
| `Mount.coerce` in the renderer | `:536` | Renderer is gone. (Mount coercion stays used elsewhere; keep `Mount.coerce` in `base.py`.) |
| `--replace`, `--log-driver=none` literal appends | `:485, 493` | Quadlet defaults + `LogDriver=none`. |
| The `Mount.render` helper in `base.py:46-63` | (kept, but…) | Add `Mount.render_quadlet()` returning `src:dst:z` Quadlet syntax; keep `render()` for the one back-compat caller if any survives (likely none — delete `render()`). |

**Kept verbatim (the input shape doesn't move):** `RuntimeLaunchPlan` (frozen dataclass), `Mount` (frozen dataclass — adds `render_quadlet`), `HealthCheck` (frozen dataclass — adds `render_quadlet`), `_llama_argv_segments`, `_llama_launch_plan`, `_resolve_profile_*`, `_resolve_image_ref`, `_resolve_model_path`, `_resolve_context_size`, `_resolve_llama_scalars`, `container_spec`, `health`, `wait_ready`, `running_image`, `running_argv`, `is_active`, `image_present`, `pull_image_stream`, `expected_argv`, `resolved_command_for_slot`, `resolved_argv_detail_for_slot`, `_resolve_slot_argv`, `_best_effort_model_info`, `_spec_provider_for`, `_spec_provider_for` dispatch (FLM/kokoro/qwen3tts/comfyui — all flow through the same renderer).

### B.4 The new `_render_quadlet_text` (outline)

```python
def _render_quadlet_text(slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> str:
    """Render a Podman Quadlet .container unit for a slot (replaces _render_unit_text).

    Sole producer of slot container unit text — both `load_sync` and
    `rerender_unit_sync` render through here so fresh-install and update-time
    units are byte-identical (#1103 invariant, preserved).

    The unit lives at `/etc/containers/systemd/hal0-slot-<id>.container`;
    Quadlet generates `hal0-slot-<id>.service` on `daemon-reload`.
    """
    slot_id = str(slot_cfg.get("id") or slot_cfg.get("name") or "")  # §11.1: id is primary
    slot_name = str(slot_cfg.get("name") or "")
    provider = _spec_provider_for(slot_cfg) or _DEFAULT_PROVIDER
    plan = provider.container_spec(slot_cfg, model_info)

    lines: list[str] = [
        f"# hal0-slot-{slot_id}.container — generated by ContainerProvider",
        f"# Do not edit; regenerated on every slot load. Slot name: {slot_name}",
        "",
        "[Unit]",
        f"Description=hal0 container inference slot ({slot_name})",
        "",
        "[Container]",
        f"Image={plan.image}",
        f"ContainerName=hal0-slot-{slot_id}",
        "AutoRemove=yes",
        "LogDriver=none",                       # keep slot logs in the unit's journal
    ]
    if plan.network_mode:
        lines.append(f"Network={plan.network_mode}")
    for dev in plan.devices:
        lines.append(f"AddDevice={dev}")
    for gid in plan.group_add:
        lines.append(f"GroupAdd={gid}")
    for cap in plan.cap_add:
        lines.append(f"AddCapability={cap}")
    for opt in plan.security_opt:
        lines.append(f"SecurityOpt={opt}")
    publish_host = _slot_publish_host()
    if plan.port and plan.network_mode != "host":
        lines.append(f"PublishPort={publish_host}:{plan.port}:{plan.port}")
    for mount in plan.mounts:
        lines.append(f"Volume={Mount.coerce(mount).render_quadlet()}")
    for k, v in plan.env.items():
        lines.append(f"Environment={k}={v}")
    if plan.health is not None:
        lines.extend(plan.health.render_quadlet())
    if plan.command:
        lines.append(f"Exec={' '.join(shlex.quote(t) if ' ' in t else t for t in plan.command)}")
    lines.extend([
        "",
        "[Service]",
        "Restart=always",
        "RestartSec=3",
    ])
    return "\n".join(lines) + "\n"
```

`Mount.render_quadlet`:
```python
def render_quadlet(self) -> str:
    """Quadlet `Volume=` value: `src:dst:ro,z` (colons separate, no comma-join)."""
    parts = [self.source, self.target]
    opts: list[str] = []
    if self.read_only:
        opts.append("ro")
    if self.selinux:
        opts.append(self.selinux)             # "z" or "Z" verbatim
    if opts:
        parts.append(",".join(opts))
    return ":".join(parts)
```

NFS detection: `Mount.render_quadlet` consults `_paths.is_nfs(self.source)` (new helper in `config/paths.py`, returns True iff `statfs(src).f_type == 0x6969`); if NFS, omit the `:z` option (and warn once at render — Quadlet can't change mount options post-write).

`HealthCheck.render_quadlet`:
```python
def render_quadlet(self) -> list[str]:
    return [
        f"HealthCmd={self.cmd}",
        f"HealthStartPeriod={self.start_period}",
        f"HealthInterval={self.interval}",
        f"HealthRetries={self.retries}",
        f"HealthTimeout={self.timeout}",
    ]
```

### B.5 Slot unit naming — `hal0-slot-<id>.service` (no `@` template)

Under §11.1 the slot `id` (stable, opaque, numeric) is the primary key; `name` is a mutable label. Unit filename is `hal0-slot-<id>.container`; Quadlet generates `hal0-slot-<id>.service`. Each slot gets its own `.container` file (no `@`-template instances). The 25+ call sites that today do `f"hal0-slot@{name}.service"` route through a helper:

```python
def slot_unit_name(slot_id: str) -> str:
    """The systemd unit name for a slot, derived from the slot id (§11.1).

    Pre-P3-quadlet: `hal0-slot@<name>.service` (template-instance).
    Post: `hal0-slot-<id>.service` (per-id unit, generated from a per-id
    Quadlet .container file).
    """
    return f"hal0-slot-{slot_id}.service"

def slot_quadlet_name(slot_id: str) -> str:
    """The Quadlet .container filename for a slot."""
    return f"hal0-slot-{slot_id}.container"
```

`ContainerProvider._unit_name` is replaced by `slot_unit_name(slot_id)`; the 25+ call sites in PART 0 use the helper. The slot's `id` field is required (`Slot.id`, populated by §11.1's SlotConfigStore migration); the helper falls back to `name` only during the §11.1 cutover window (logged warning). After §11.1 lands, `id` is the only key.

### B.6 Companion services collapse to `.container` + tiny override

`hal0-openwebui.service` (the only companion currently rendered) becomes:

**`packaging/quadlet/hal0-openwebui.container`:**
```ini
[Unit]
Description=hal0 OpenWebUI companion (ghcr.io/open-webui/open-webui)
Documentation=https://github.com/open-webui/open-webui

[Container]
Image=ghcr.io/open-webui/open-webui@sha256:7f1b0a1a50cfbac23da3b16f96bc968fd757b26dc9e54e93813d61768ea9184e
ContainerName=hal0-openwebui
AutoRemove=yes
EnvironmentFile=/etc/hal0/openwebui.env
Volume=/var/lib/hal0/openwebui:/app/backend/data
PublishPort=0.0.0.0:3001:8080
AddHost=host.containers.internal:host-gateway   # podman-native equivalent of host.docker.internal:host-gateway

[Service]
Restart=always
RestartSec=5
StartLimitIntervalSec=120
StartLimitBurst=10
```

`AddHost=host.containers.internal:host-gateway` is **podman's documented equivalent** of docker's `--add-host=host.docker.internal:host-gateway`. The OpenWebUI env writer (`openwebui/env_writer`) updates `OPENAI_API_BASE_URLS` from `host.docker.internal` to `host.containers.internal` once at install — a 1-line string replace. **No operator action needed.**

The previous `packaging/systemd/hal0-openwebui.service` is deleted (replaced by the Quadlet-generated `hal0-openwebui.service` which is byte-identical for our purposes — podman's generator emits the `[Unit]/[Service]` skeleton).

`install.sh:1717` (the skill drop-in installation), `installer/systemd/hal0-agent@.service`, `hal0-bench*`, `hindsight-api`, `hal0-honcho*`, `hal0-podman-forward` — none of these launch containers; **stay as hand-written systemd units.**

### B.7 What dies in the daemon reload + enable + restart dance

| Step | Current | Quadlet |
|---|---|---|
| Write unit | `unit_path.write_text(unit_text)` (`container.py:1223`) | `hal0-systemctl write-quadlet <id> < <text>` → writes `/etc/containers/systemd/hal0-slot-<id>.container` (`install -o root -g root -m 0644`). |
| Daemon reload | `self._run("systemctl", "daemon-reload")` (`container.py:1224`) | `hal0-systemctl daemon-reload`. The generator runs on `daemon-reload`, creating `hal0-slot-<id>.service` from the `.container`. |
| Enable | `self._run("systemctl", "enable", unit, check=False)` (`:1226`) | Quadlet auto-adds `[Install] WantedBy=hal0.target` → `enable` happens once on first install. Not called per-load. |
| Restart | `self._run("systemctl", "restart", unit)` (`:1227`) | `hal0-systemctl start hal0-slot-<id>` (first launch) or `restart` (re-render). `Restart=always` in the `[Service]` drop-in handles crash recovery. |
| Unload | `systemctl stop; reset-failed; disable; unlink; daemon-reload` (`:1362-1378`) | `rm /etc/containers/systemd/hal0-slot-<id>.container; hal0-systemctl daemon-reload`. The generator removes the corresponding `.service`; `systemctl stop` is implicit (Quadlet stops the container when the source vanishes — verify on first migration). |

**`hal0-systemctl` extension (P3-perms seam):** add `write-quadlet <id>` to the helper (PART D in `spec-p3-perms.md`). Pseudocode:
```bash
write-quadlet)
  id="${1:-}"
  [[ "$id" =~ ^[a-zA-Z0-9_-]{1,64}$ ]] || { echo "bad slot id" >&2; exit 64; }
  install -m 0644 -o root -g root /dev/stdin "/etc/containers/systemd/hal0-slot-${id}.container"
  ;;
```

The sudoers drop-in (`packaging/sudoers/hal0-systemctl`) needs a new permission line: `hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/hal0-systemctl write-quadlet *`. Verified against the existing `hal0-agentenv` template (validate-args, no shell, no wildcards, no arbitrary file writes).

### B.8 What happens to `Restart=no` (`container.py:407`)

The hand-rendered unit has `Restart=no` because every slot restart is manager-driven (state transitions, not crash recovery). Quadlet + `Restart=always` lets systemd do crash recovery and frees `slots/manager._fail_watch_loop` from the restart-on-strike path. **Coordinate with P3-slots** — the watchdog (`/home/mint/hal0-specs/spec-p3-slots.final.md` §1(b′)) keeps the health-probe role; only the restart-on-strike arm is removed. The change is risky if a slot crashes repeatedly (Quadlet's `StartLimitBurst`/`StartLimitIntervalSec` defaults are 10/10s, which is tighter than the existing manager-driven `restart no` + manual reaper — tune to 5/300s via the `[Service]` drop-in to match the current behavior).

---

## PART C — Coordination contracts (cross-lane seams)

### C.1 §11.1 Slot id-keying — slot `id` is the unit name

**Contract:** every slot has a stable opaque `id` (numeric string). The Quadlet `.container` filename is `hal0-slot-<id>.container`. The `name` field is a mutable label, irrelevant to unit identity.

**Sequence:** P3-quadlet code accepts BOTH `slot.id` AND `slot.name` during the §11.1 cutover window. After §11.1 lands, `id` is required (helper raises if absent). **P3-quadlet can land in parallel with §11.1 if the helper is written to accept both** — verified by the P3-slots spec, which keeps `Slot.name` as a delegator until §11.1 deletes it.

**Idempotency under slot rename:** today `f"hal0-slot@{name}.service"` re-keys on every rename (the @-template instance name changes). Under P3-quadlet, rename is a metadata-only operation: `Slot.name` changes, `Slot.id` doesn't, the `.container` filename doesn't, the running container doesn't. This is the §11.1 invariant expressed in the runtime layer.

### C.2 §11.2 PortAuthority — `PublishPort` consumes the port authority

**Contract:** every slot gets its port from PortAuthority (`port_claim` SQLite table per §8 / spec-ml1-sqlite). The Quadlet unit's `PublishPort=` carries the authority-issued port; `RuntimeLaunchPlan.port` carries the same value (the renderer reads it from the plan, which the launcher reads from the authority).

**Sequence:** §11.2's PortAuthority ships in W3-W4 (per `/home/mint/hal0-rework-plan.md:1712-1713`). P3-quadlet reads `plan.port` exactly as today (`container.py:518-519`); if §11.2 lands first, `plan.port` is authority-issued; if P3-quadlet lands first, `plan.port` is whatever the slot TOML says (current behavior). The P3-quadlet code does not need to know which.

**Publish host:** unchanged — `_slot_publish_host()` reads `[slots].publish_host` (`container.py:322-337`), defaults to `127.0.0.1`, widened via the same TOML key. The Quadlet field is `PublishPort=<host>:<port>:<port>`. **Verify:** Quadlet accepts `127.0.0.1:8081:8081` (host:host_port:container_port). Confirmed in the upstream Quadlet docs.

### C.3 P3-perms (hard dependency, already specified in `spec-p3-perms.md`)

**Contract:** `hal0-api` runs as `User=hal0`; `/etc/containers/systemd/` is root:root 0755 (systemd-managed); writes happen via `hal0-systemctl write-quadlet <id>` (the new subcommand). Without P3-perms:

1. `hal0-api` (User=root today) writes the `.container` directly — works but defeats the born-owned model.
2. After P3-perms flips hal0-api to User=hal0, the direct write fails (EACCES on `/etc/containers/systemd/`).

**Sequence (§23.4):** P3-perms (F.3-F.6 cluster) MUST land before P3-quadlet can use `hal0-systemctl write-quadlet`. The reverse — P3-quadlet landing first while hal0-api is still User=root — works but bakes in the wrong ownership pattern (root-writes-container, root-runs-quadlet, container named `hal0-slot-<id>`). **Recommended:** wait for P3-perms. **Acceptable:** land P3-quadlet first with direct root writes, then switch to `hal0-systemctl` as a follow-up PR.

**`hal0-systemctl` extension:** add `write-quadlet <id>` subcommand (PART B.7). No new sudoers file — extend the existing `packaging/sudoers/hal0-systemctl` with the new permission line. `visudo -cf` after the edit; verified by the P3-perms spec (F.5 verification gate).

### C.4 Slot lifecycle under Quadlet (spawn / terminate / restart / swap)

| Operation | Current (hand-rendered) | Quadlet |
|---|---|---|
| **Spawn** (`SlotManager.spawn` → `ContainerProvider.load_sync`) | `_render_unit_text` → `write_text` → `daemon-reload` → `enable` → `restart` | `_render_quadlet_text` → `hal0-systemctl write-quadlet <id>` (stdin→file) → `hal0-systemctl daemon-reload` → `hal0-systemctl start hal0-slot-<id>`. |
| **Terminate** (`unload_sync`) | `stop` → `reset-failed` → `disable` → `unlink unit` → `daemon-reload` | `rm /etc/containers/systemd/hal0-slot-<id>.container` → `hal0-systemctl daemon-reload`. The generator emits `Requires=podman-user-watcher.service` and a stop; verify on first migration that the container is fully torn down (no zombies). |
| **Restart** (`slots/manager.restart`) | `systemctl restart <unit>` | `hal0-systemctl restart hal0-slot-<id>` (same seam). Quadlet's `Restart=always` handles crash-driven restarts separately. |
| **Swap** (`slots/manager.swap` → unload + load with new model/port) | `unload_sync` then `load_sync` with new plan | `unload_sync` (delete .container + daemon-reload) then `load_sync` (write new .container + daemon-reload + start). Same shape; one fewer `daemon-reload` if we batch (unload + load = one daemon-reload at the end). |
| **Re-render** (`rerender_unit_sync`, called by `updater.py`) | Re-renders unit text, writes if changed, batches `daemon_reload` | Same — re-renders .container text, writes if changed, batches `daemon-reload`. The byte-identical invariant (#1103) holds because `_render_quadlet_text` is deterministic. |
| **Status** (`is_active`, `running_image`, `running_argv`) | All unchanged — `systemctl is-active` and `<runtime> inspect <container>`. Container name is `hal0-slot-<id>` (Quadlet default; no `ContainerName=` override needed). |

### C.5 GPU / NPU passthrough in Quadlet

- **AMD path:** `AddDevice=/dev/kfd` + `AddDevice=/dev/dri/renderD128` (enumerated by `resolve_gpu_device_paths`, `_gpu.py:51-83`). `GroupAdd=<gid>` for `render`/`video` (numeric GIDs from `resolve_gpu_group_ids`). **No existence filtering** in the renderer — Quadlet doesn't validate at render-time; the device either exists at start (Quadlet errors loudly with a clear message) or it doesn't (CI / no-GPU dev box falls back to directory paths via the existing helper at `_gpu.py:80-82`). For the live box this is fine; for tests, the dev fallback is sufficient.
- **NVIDIA CDI path:** `AddDevice=nvidia.com/gpu=all` (or `=0` when `gpu_index` is set). `nvidia_cdi_devices` in `_gpu.py:180-?` (already verified). No `GroupAdd` for CDI (the CDI spec injects permissions). Pass-through unchanged.
- **NPU path:** no current GPU/NPU device passthrough for FLM (the FLM container talks to the host's NPU via /dev/accel/* or a host-side daemon; verify in `providers/flm.py` — out of scope for this spec, lands unchanged).
- **SELinux `:z` on volumes:** `Volume=src:dst:z` (Quadlet syntax). **NFS gotcha:** detect `statfs f_type==0x6969`, omit the relabel entirely (per `/home/mint/hal0-rework-plan.md:1606` §23.3).
- **Security:** `SecurityOpt=apparmor=unconfined` + `SecurityOpt=seccomp=unconfined` (carried from `container.py:750`). One per line.

### C.6 P3-slots compatibility

`ContainerProvider.container_spec` is the slot-config → RuntimeLaunchPlan bridge (lives in `provider.container_spec`, called by `SlotManager._spawn_locked` via `provider.load_sync(slot_cfg, model_info)`). The provider returns a `RuntimeLaunchPlan`; `container_spec` doesn't care how the plan is rendered. P3-slots decomposes `slots/manager.py` (`spec-p3-slots.final.md`) and keeps `ContainerProvider.load_sync` as the public load path; the renderer swap is invisible to P3-slots. **Verification:** `tests/providers/test_container.py` constructs plans and asserts `load_sync` writes the expected unit text — update the assertions to expect `.container` text instead of `.service` text.

### C.7 P3-brain + Hermes provisioning compatibility

`Hal0Brain` doesn't launch containers directly; it uses a slot. P3-quadlet is invisible to P3-brain. The slot the brain uses (`hal0/brain` → slot `brain` → unit `hal0-slot-brain.service`) renders through the same `_render_quadlet_text`; the brain's wait_ready / health probing (`ContainerProvider.health`) is unchanged.

Hermes provisioning (`hermes_provision.py`) doesn't launch containers either (the per-agent unit is `hal0-agent@<id>.service`, a Python daemon, not a podman container). P3-quadlet is invisible to hermes provisioning.

---

## PART D — Edit plan (files, order, delegators/shims)

Order is load-bearing — each step assumes the previous step's invariants hold. The cluster F.3→F.4→F.5→F.6 (P3-perms) MUST be complete before P3-quadlet ships to the live box (per `/home/mint/hal0-rework-plan.md:1713` §23.4 W4 dependency).

### PR Q.1 — Renderer swap (data + behavior, no runtime change yet)

**Depends on:** nothing (uses direct root writes; `hal0-api` still User=root at this point).

- Add `Mount.render_quadlet()` and `HealthCheck.render_quadlet()` to `providers/base.py`.
- Add `_paths.is_nfs(path)` helper to `config/paths.py` (returns True iff `statfs.f_type == 0x6969`).
- Add `_render_quadlet_text` to `providers/container.py` (per PART B.4).
- Add `slot_unit_name(slot_id)` / `slot_quadlet_name(slot_id)` helpers to a new `hal0/slots/naming.py` (or `providers/container.py` near the existing `_unit_name`).
- **Keep** `_render_unit_text` and `_render_unit_from_plan` as a parallel path; add a `HAL0_QUADLET=1` env-var toggle (or a `container_spec` return-field annotation) that selects the renderer. Tests run both paths.
- Update `ContainerProvider.load_sync` to honor the toggle: write the `.container` to a scratch path; verify Quadlet generates the same `.service` (parse the generator output for `ExecStart` equivalence).
- Add `tests/providers/test_container.py::test_quadlet_text_matches_rendered` — assertion: `RuntimeLaunchPlan → _render_quadlet_text → .container text → systemd generator → .service text` is **equivalent** to the old `_render_unit_from_plan → .service text` for the same plan. This is the load-bearing byte-equivalence test.
- Tests: `test_quadlet_image_field`, `test_quadlet_volume_field_nfs_omits_z`, `test_quadlet_security_opt_one_per_line`, `test_quadlet_exec_quoted`, `test_quadlet_health_fields`. New `test_container.py` cases, ≤150 lines.

**Behavior change:** none (toggle-off keeps the existing renderer). Build is green; CI green; scar baseline neutral.

### PR Q.2 — Delete hand-rendered renderer

**Depends on:** Q.1 merged + green for one full release cycle (catches any toggle-on bug found in dev).

- Delete `_render_unit_from_plan`, `_render_unit`, `_render_unit_from_spec`, `_unit_skeleton` from `providers/container.py` (PART B.3). Drop the `_render_unit_text` rename (now just the quadlet renderer).
- Delete the `_container_runtime` docker fallback (PART B.3); rename to `_podman_runtime()`.
- Delete the `is_podman` branch (`:473`).
- Delete the `extra_args` field from `RuntimeLaunchPlan`? **No — keep `extra_args` for one release as a no-op warning** (PART B.1 escape-hatch note). Operators with hand-authored `extra_args` (e.g. `--ulimit memlock=-1`) need a migration path: emit a `hal0-slot-<id>.container.d/extra.conf` drop-in from `extra_args` so the value survives. The drop-in reads:
  ```ini
  [Container]
  ULimit=nofile=65535:65535
  ```
  Field name mapping: `--ulimit memlock=-1` → `ULimit=memlock=-1`. Documented in operator guide.
- Update `ContainerProvider._unit_name` to call `slot_unit_name(slot_id)`.
- Update `ContainerProvider._unit_path` to return `/etc/containers/systemd/<quadlet-name>`.
- Update `ContainerProvider._write_and_start_unit` to call `hal0-systemctl write-quadlet` + `daemon-reload` + `start`. (If P3-perms isn't merged yet, write directly as root; the seam migration is Q.5.)
- Update `ContainerProvider.unload_sync` to delete the `.container` + `daemon-reload`.

**Behavior change:** slot units switch from `.service` to `.container`. Live boxes: zero-downtime because the new unit is on first launch (every slot is restarted on every hal0 update via `slots/manager._reaper_unconfigured_slots` or `updater.py`'s restart sweep — verify this is the case; if not, add the restart to the cluster's restart gate).

### PR Q.3 — Migrate companion services (OpenWebUI)

**Depends on:** Q.2 merged.

- Add `packaging/quadlet/hal0-openwebui.container` (per PART B.6).
- Delete `packaging/systemd/hal0-openwebui.service` (replaced by the generator's output).
- Update `openwebui.env_writer` to emit `OPENAI_API_BASE_URLS=http://host.containers.internal:8080/v1` (replace `host.docker.internal`).
- Update `install.sh:1717` (the skill drop-in) — no change; only systemd units shift, skills stay.
- Update `installer/uninstall.sh` — remove the Quadlet dir on uninstall: `rm -rf /etc/containers/systemd/hal0-*` (covers slots + companion).

**Behavior change:** OpenWebUI's `OPENAI_API_BASE_URLS` flips from `host.docker.internal` to `host.containers.internal`. Operator impact: zero (env_writer handles it at install; if env_writer isn't re-run, the existing env file is still functional via the old `host.docker.internal:host-gateway` add-host — keep the `AddHost=host.containers.internal:host-gateway` in the .container regardless so both names work).

### PR Q.4 — Slot unit naming retarget (search-and-replace)

**Depends on:** §11.1 slots-final merged (so `Slot.id` exists). **Can land in parallel with Q.2** if the `slot_unit_name(slot_id)` helper falls back to `name` during the cutover.

- Find every `f"hal0-slot@{...}.service"` literal in `src/`, `tests/`, `installer/`, `packaging/` (PART 0 has 25+ call sites). Replace with `slot_unit_name(slot.id)`.
- Find every `"hal0-slot@"` prefix in log strings, error messages, `SyslogIdentifier=` (the slot name in log lines stays — `SyslogIdentifier=hal0-slot-<name>` for backward-compat log searches).
- Update `tests/` fixtures that hardcode `hal0-slot@<name>.service` strings to `hal0-slot-<id>.service` (use `id="42"` for the test fixture).

**Behavior change:** none visible to operators (the systemd unit name changes shape; the container name stays `hal0-slot-<id>`).

### PR Q.5 — `hal0-systemctl write-quadlet` extension (the P3-perms seam)

**Depends on:** P3-perms F.6 merged (so `hal0-api User=hal0` + `hal0-systemctl` helper exists).

- Extend `installer/wrappers/hal0-systemctl` with `write-quadlet <id>` subcommand (PART B.7).
- Extend `packaging/sudoers/hal0-systemctl` with the `write-quadlet *` permission line.
- Update `ContainerProvider._write_and_reload` to use `hal0-systemctl write-quadlet <id>` + `daemon-reload`.
- Update `api/__init__.py` lifespan (if it owns any direct writes — verify none) to use the seam.

**Behavior change:** `hal0-api` (now User=hal0) writes slot containers via the privileged seam. Unit ownership: `.container` file = root:root 0644; systemd unit = generated by root.

### PR Q.6 — Companion service: `hal0-podman-forward` stays (out of scope)

`packaging/systemd/hal0-podman-forward.service` is a one-shot iptables repair (not a container). **No change.**

### PR Q.7 — Drift-delete decision (joint with P3-slots)

`spec-p3-slots.final.md` §6 hypothesizes deleting `compute_config_drift` (it's a 115-line block comparing `running_argv` vs `expected_argv`). P3-quadlet renders `expected_argv`; P3-slots owns `compute_config_drift`. **Joint decision:** if Quadlet's deterministic render + PortAuthority's deterministic ports + the single-Store ML-store resolver together remove the drift class, delete `compute_config_drift` + `_argv_values` + `_resolve_drift_flags` + `_config_drift_values_equal` + `_CONFIG_DRIFT_KEYS`. If a real drift source survives, keep `compute_config_drift` and retarget its `expected_argv` call to the Quadlet renderer. **Do not delete unilaterally.**

### Sequencing summary

```
P3-perms (F.1-F.8)            [W3]
  ↓
Q.1 renderer swap (toggle)    [W4, parallel with P3-slots + P3-brain + ML-3]
Q.2 delete old renderer       [W4, after one release cycle]
Q.3 companion migration       [W4, after Q.2]
Q.4 slot unit naming retarget [W4, after §11.1 slots-final + Q.2]
Q.5 hal0-systemctl extension  [W4, after P3-perms F.6 + Q.2]
Q.7 drift-delete (joint)      [W4, after Q.1]
```

### Delegators / shims that must survive

| Shim | Reason | Lives at |
|---|---|---|
| `_render_quadlet_text` (renamed from `_render_unit_text`) | The single renderer; Q.1 toggle-on path. | `providers/container.py` (per PART B.4) |
| `slot_unit_name(slot_id)` | Unit name by id (§11.1). 25+ call sites route through this. | `hal0/slots/naming.py` (NEW) |
| `slot_quadlet_name(slot_id)` | Quadlet filename by id. | `hal0/slots/naming.py` |
| `Mount.render_quadlet()` | Quadlet `Volume=` syntax. | `providers/base.py` |
| `HealthCheck.render_quadlet()` | Quadlet `HealthCmd=` etc. | `providers/base.py` |
| `_paths.is_nfs(path)` | Skip `:z` on NFS. | `config/paths.py` |
| `hal0-systemctl write-quadlet <id>` | P3-perms seam extension. | `installer/wrappers/hal0-systemctl` |
| `ContainerSpec = RuntimeLaunchPlan` alias (back-compat) | Test imports + module-level alias (`:74`). | `providers/container.py:74` |
| `Mount.coerce` | Tolerate legacy `(src, dst)` tuples. Stays. | `providers/base.py` |
| `Mount.render` (for `--volume=` podman-run syntax) | The current renderer (`:510`) dies; if no other caller, delete. Verify: `grep -rn 'Mount\.render\|\.render()' src/hal0/providers/` — should be empty after Q.2. | `providers/base.py` (delete after Q.2) |
| `extra_args` no-op warning | One-release bridge for operators with hand-authored extra_args. | `providers/container.py` (deprecated field, Q.2) |

### What does NOT change

- `RuntimeLaunchPlan` (frozen dataclass) — input shape preserved.
- `Mount` (frozen dataclass) — adds `render_quadlet`, drops `render`.
- `HealthCheck` (frozen dataclass) — adds `render_quadlet`.
- `_llama_argv_segments`, `_llama_argv_segments` (the segments), `_resolve_llama_scalars`, `_llama_launch_plan` — pure data, unchanged.
- Slot TOML schema (no new fields).
- Slot state machine (P3-slots, unchanged).
- `slots/manager.py` public API (delegators added for `compute_config_drift` if kept).
- `installer/systemd/hal0-api.service` (Python daemon, not a container).
- `installer/systemd/hal0-agent@.service` template (Python daemon, not a container).
- `installer/systemd/hal0-bench*` (no container).
- `installer/systemd/hal0-honcho*` (Phase 1 deletes Honcho; out of scope here).
- `installer/systemd/hindsight-api.service` (Python daemon).
- `packaging/systemd/hal0-podman-forward.service` (iptables one-shot).
- `installer/wrappers/hal0-agentenv`, `hal0-benchctl` (privileged seams; unchanged).
- `openwebui.env_writer` logic (only the `host.docker.internal` → `host.containers.internal` string swap).

---

## PART E — Migration of running units (the live-box transition)

The live lxc105 has ~3-7 slot units running at any time (chat/agent/brain/img + a few slot-swap experiments). The migration must be **zero-downtime**.

### E.1 The cutover procedure (operator-facing)

1. **Pre-cutover:** install the P3-quadlet code (`hal0 pull`); `hal0-api` restarts as User=hal0 (P3-perms).
2. **Verify Quadlet is available:** `ls /usr/lib/systemd/system-generators/podman-systemd-generator` — must exist (podman ≥ 4.4 ships the generator). **Verify on lxc105 BEFORE merging Q.2** — the failure mode is "slot launches break silently." Document the podman version requirement in the PR.
3. **First slot restart triggers the migration.** The next time any slot is loaded, `_render_quadlet_text` writes the new `.container`; the old `.service` (if any) is replaced on `daemon-reload` + `start`.
4. **Bulk migration:** on first `hal0-api` start after Q.2 lands, `rerender_unit_sync` is called by `updater.py`'s sweep (verify the call site exists). If not, add a one-shot "re-render every slot" hook in the lifespan that runs once per restart and no-ops if the `.container` is byte-identical to what would be rendered.
5. **Operator fallback:** if Quadlet misbehaves on the live box, `HAL0_QUADLET=0` env-var on `hal0-api` reverts to the hand-rendered path (Q.1 toggle). Remove the toggle in a follow-up release after one full release cycle proves Quadlet is solid.

### E.2 The `halo` LXC (the rework deploy target)

Per `/home/mint/hal0-rework-plan.md:719-725` §12: P3-quadlet deploys to the **fresh `halo` LXC**, not lxc105 in-place. `halo` is built from P3-perms onward; it never sees the hand-rendered path. The toggle is unnecessary on `halo` (Q.1 lands with `HAL0_QUADLET=1` default for the new install).

### E.3 Podman version check (the load-bearing prerequisite)

Quadlet is GA in podman 4.4 (Feb 2023); the live box runs podman ≥ 4.5 (verified by `packaging/systemd/hal0-openwebui.service` using podman-native features). **Document the minimum podman version** (`>= 4.4`) in `installer/preflight.sh` and `hal0 doctor` (`HAL0-QUADLET-UNSUPPORTED` diagnosis ID, parallel to §21.2's `HAL0-GFX-TARGET-UNSUPPORTED`).

---

## PART F — Risks + capped verification

### F.1 Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Live box runs an older podman without Quadlet** (`/usr/lib/systemd/system-generators/podman-systemd-generator` missing). | Low (podman is ≥4.5 on lxc105; verify) | Every slot launch breaks. | `preflight.sh` + `hal0 doctor` checks; `HAL0-QUADLET-UNSUPPORTED` diagnosis; refuse install on too-old podman. |
| R2 | **Podman Quadlet auto-removes the container on `daemon-reload`** (the existing `--replace` + `ExecStartPre=-podman rm` dance is for the same reason). | High (Quadlet behavior) | None — Quadlet stops+removes before starting the new container. | Verified in upstream Quadlet docs; matches the existing `--replace` semantics. |
| R3 | **`extra_args` deletion breaks an operator's hand-authored extra_args** (e.g. `--ulimit memlock=-1`). | Medium | The flag silently drops; the slot launches without it; perf or stability degrades. | Q.2 emits a `hal0-slot-<id>.container.d/extra.conf` drop-in from the `extra_args` field; the field is **deprecated**, not deleted; one release window to migrate; warning logged on every render. |
| R4 | **`hal0-systemctl write-quadlet` not yet installed when Q.2 lands** (P3-perms F.6 not merged). | Medium | Direct root writes succeed but bypass the seam; after P3-perms flips hal0-api to User=hal0, every slot launch fails EACCES. | Sequence: P3-perms F.6 → Q.5 → Q.2-with-seam. If Q.2 lands first, ship with direct root writes + a "P3-perms pending" TODO. |
| R5 | **Quadlet doesn't honor `RequiresMountsFor=`** for bind mounts on non-FUSE sources. | Low | Slot starts before the mount is up; bind fails. | Verify on `halo` first (where mounts are static). On lxc105 (`/mnt/ai-models` is a FUSE mount via the NFS pathway), the existing `RequiresMountsFor=` survives via Quadlet's auto-emission. |
| R6 | **Drift delete is a behavior change** (`compute_config_drift` removed). | Low | The updater's drift panel signal disappears. | Joint decision with P3-slots (§C.7). Do not delete unilaterally. |
| R7 | **`host.containers.internal` doesn't resolve in OpenWebUI's container** (the podman-native equivalent of `host.docker.internal`). | Medium | OpenWebUI's `OPENAI_API_BASE_URLS` health check on first boot can't reach hal0-api; OpenWebUI retries. | Verified in upstream podman docs (≥4.0). Document the version requirement. If broken on lxc105 podman, keep `host.docker.internal:host-gateway` in the `AddHost=` line as a fallback (OpenWebUI's env_writer emits both forms during the migration). |
| R8 | **NFS mount with `:z` relabel fails** (the user's `ai-models` is NFS). | High (the user's specific box) | Bind fails with EACCES. | `Mount.render_quadlet` calls `_paths.is_nfs(src)`; if True, omit `:z` entirely. Per `/home/mint/hal0-rework-plan.md:1606` §23.3 + the user's `pve-gtt-hidden-memory` + `ai-models access model` memory. |
| R9 | **Slot `id` not yet present** (Q.4 lands before §11.1). | Medium | `slot_unit_name(slot.id)` returns `hal0-slot-None.service`; the unit won't start. | Q.4 helper falls back to `name` with a warning during the §11.1 cutover; after §11.1, `id` is required. |
| R10 | **Container name collision** between slots (if two slots share a name post-§11.1 migration bug). | Low | Second slot fails to start (name-in-use). | Quadlet's per-basename container names are unique by construction (slot `id` is unique); the `compute_config_drift` ALIAS-based collision is gone. |

### F.2 Capped verification (per PR)

| PR | Verification gate |
|---|---|
| Q.1 | `pytest tests/providers/test_container.py` — new `test_quadlet_text_matches_rendered` passes (byte-equivalent to old `.service` text). Generator parse: `systemd-analyze verify /run/systemd/generator/hal0-slot-test.service` returns 0. Toggle off → existing `.service` text unchanged. |
| Q.2 | On `halo` LXC: spawn a slot (`hal0 slots create chat test`), `systemctl status hal0-slot-test` shows running. `cat /etc/containers/systemd/hal0-slot-test.container` is the new format. `cat /run/systemd/generator/hal0-slot-test.service` is the generator output. `--device` / `--group-add` / `--env` / `--publish` all present. Health probe (`curl /health`) passes within 180s. Swap (`hal0 slots swap test <other-model>`) reloads the container. Unload removes the `.container` + the generator's `.service` + stops the container. |
| Q.3 | OpenWebUI: install on `halo`, `curl http://localhost:3001/` returns the OpenWebUI HTML; `OPENAI_API_BASE_URLS` reaches hal0-api (the health check passes). |
| Q.4 | `grep -rn 'hal0-slot@' src/ tests/ installer/ packaging/` returns nothing. `slot_unit_name("42")` returns `"hal0-slot-42.service"`. Slot restart test (Q.2) passes. |
| Q.5 | `hal0-systemctl write-quadlet test < /etc/containers/systemd/hal0-slot-test.container` writes root:root 0644. `hal0-systemctl write-quadlet bad-id` returns 64 with "bad slot id" (validation works). `hal0-api` (User=hal0) writes a new slot `.container`; `systemctl status hal0-slot-<new>` is active. |
| F (post-cluster) | Adversarial: `chown hal0 /etc/containers/systemd/hal0-slot-test.container` (drift), observe `hal0 doctor` reports it (or it works because root owns the dir; verify `OwnershipStore` covers `/etc/containers/systemd/`); `hal0 doctor perms --fix` restores. |

### F.3 Adversarial verification (post-Q.5 cluster, on `halo` LXC)

1. Fresh `sudo bash install.sh` on `halo`. `/etc/containers/systemd/` exists root:root 0755. `OwnershipStore` has a row for it (`spec-p3-perms.md` Appendix A adds it; verify P3-perms includes `/etc/containers/systemd/`).
2. `hal0 doctor` reports `Quadlet: ok` (verifies the podman version + generator presence).
3. Slot lifecycle: `hal0 slots create chat test` → `hal0-slot-test.service` running, `cat /etc/containers/systemd/hal0-slot-test.container` is well-formed, generator output verified by `systemd-analyze verify`.
4. GPU passthrough: a GPU-backed slot (e.g. `rocm` profile) — `journalctl -u hal0-slot-test` shows the slot loaded the model; `/dev/dri/renderD128` is visible in the container (`podman exec hal0-slot-test ls -la /dev/dri/renderD128`).
5. Companion: `hal0-openwebui.service` running, `curl http://localhost:3001/` returns OpenWebUI HTML, `OPENAI_API_BASE_URLS` health check passes against hal0-api.
6. NFS volume (the user's `ai-models`): `:z` omitted from `Volume=` line; bind succeeds; model file accessible inside the container.
7. Failure injection: `rm /etc/containers/systemd/hal0-slot-test.container; systemctl daemon-reload` — generator removes the `.service`; the running container is stopped (Quadlet behavior, verify); restart requires re-spawning the slot.
8. Failure injection: `HAL0_QUADLET=0` on `hal0-api` env — falls back to hand-rendered `.service` (Q.1 toggle); slot launches; remove the toggle env to confirm Quadlet path works.
9. Self-update simulation: install a fake newer package, observe slot units re-render via `rerender_unit_sync`; verify `daemon-reload` once at the end of the sweep.

---

## PART G — Spec-level DoD (cluster acceptance)

P3-quadlet lands when:

- [ ] `ContainerProvider._render_quadlet_text` is the sole renderer of slot unit text; `_render_unit_from_plan`, `_unit_skeleton`, `_render_unit`, `_render_unit_from_spec` are deleted from `providers/container.py`.
- [ ] `_render_quadlet_text` output for a slot matches the byte-equivalent generated `.service` text for the same `RuntimeLaunchPlan` (preserving the #1103 invariant).
- [ ] Slot units are `hal0-slot-<id>.container` files at `/etc/containers/systemd/`, owned root:root 0644, written via `hal0-systemctl write-quadlet <id>`.
- [ ] Slot unit systemd names are `hal0-slot-<id>.service` (no `@`-template instance); the 25+ call sites that build the name literal route through `slot_unit_name(slot_id)`.
- [ ] No `podman run` strings appear in `providers/container.py` or `providers/base.py` (renderer is dead).
- [ ] `_container_runtime` returns podman only; docker is unsupported; the misleading `.hal0ai` docker references (`packaging/systemd/hal0-openwebui.service:55` `host.docker.internal:host-gateway`) are gone (replaced with `host.containers.internal:host-gateway`).
- [ ] `Mount.render_quadlet` handles `:z` / `:Z` / `:ro` / NFS correctly (omits `:z` on NFS, verified via `_paths.is_nfs`).
- [ ] `HealthCheck.render_quadlet` emits all 5 `Health*` fields.
- [ ] `extra_args` on `RuntimeLaunchPlan` is deprecated (logged warning) + emits a `.container.d/extra.conf` drop-in for one release cycle; deleted in a follow-up PR after the cycle.
- [ ] `Restart=always` + `RestartSec=3` is set on the `[Service]` section; `StartLimitIntervalSec=300 StartLimitBurst=5` is in the drop-in (matches the existing manager-driven restart behavior).
- [ ] Companion service `hal0-openwebui.service` is a Quadlet `.container`; the old `packaging/systemd/hal0-openwebui.service` is deleted; `openwebui.env_writer` emits `host.containers.internal` for `OPENAI_API_BASE_URLS`.
- [ ] `hal0-systemctl` extends with `write-quadlet <id>`; sudoers drop-in extended; `visudo -cf` passes.
- [ ] All 7 PRs (Q.1-Q.5 + Q.7 + Q.6 trivial) green: unit tests, integration tests, linter, type checker, scar-baseline ratchet, sunset-shim check, CI.
- [ ] `/etc/containers/systemd/` is in `OwnershipStore` (P3-perms Appendix A).
- [ ] `hal0 doctor` has `Quadlet: ok` check (verifies podman ≥ 4.4).
- [ ] Tracker row `P3-quadlet:Q.1` … `P3-quadlet:Q.5` flipped + changelog line per `/home/mint/hal0-rework-plan.md:644-658`.
- [ ] Joint decision with P3-slots on `compute_config_drift` (delete or keep); both specs reference the decision.

---

## Appendix A — File inventory (exact paths)

**Modified (PR Q.1):**
- `src/hal0/providers/base.py` — `Mount.render_quadlet()`, `HealthCheck.render_quadlet()`, optionally delete `Mount.render()`.
- `src/hal0/config/paths.py` — `is_nfs(path)` helper.
- `src/hal0/providers/container.py` — add `_render_quadlet_text`, `slot_unit_name`, `slot_quadlet_name`; Q.1 toggle (`HAL0_QUADLET=1` or annotation).
- `tests/providers/test_container.py` — new `test_quadlet_*` cases.

**Deleted in PR Q.2:**
- `src/hal0/providers/container.py:362-433` (`_unit_skeleton`).
- `src/hal0/providers/container.py:436-543` (`_render_unit_from_plan`).
- `src/hal0/providers/container.py:546-581` (`_render_unit`).
- `src/hal0/providers/container.py:584-596` (`_render_unit_from_spec`).
- The docker fallback in `src/hal0/providers/container.py:297-319`.
- `Mount.render()` in `src/hal0/providers/base.py` (if no callers after Q.2).
- `RuntimeLaunchPlan.extra_args` (deprecated Q.2, deleted in a follow-up PR after one release cycle).

**Replaced in PR Q.3:**
- `packaging/systemd/hal0-openwebui.service` → `packaging/quadlet/hal0-openwebui.container`.
- `openwebui.env_writer` emits `host.containers.internal`.

**Modified in PR Q.4 (search-and-replace):**
- 25+ call sites in `src/hal0/{api,cli,dispatcher,install,slot_view,slots,services}/` and `installer/`, `packaging/` — see PART 0.
- `tests/` fixtures that hardcode `hal0-slot@<name>.service` strings.

**Modified in PR Q.5:**
- `installer/wrappers/hal0-systemctl` — add `write-quadlet <id>` subcommand.
- `packaging/sudoers/hal0-systemctl` — add `write-quadlet *` permission line.
- `src/hal0/providers/container.py:_write_and_reload` — use the seam.

**Joint with P3-slots (PR Q.7):**
- `src/hal0/slots/drift.py` (if drift survives) or `src/hal0/slots/manager.py` `compute_config_drift` + 4 helpers (if drift is deleted).

## Appendix B — Glossary

| Term | Definition |
|---|---|
| Quadlet | Podman's declarative unit generator. `.container` files in `/etc/containers/systemd/` are translated to `.service` files by `/usr/lib/systemd/system-generators/podman-systemd-generator` on `daemon-reload`. |
| Podman Quadlet generator | The systemd generator (`/usr/lib/systemd/system-generators/podman-systemd-generator`) that emits systemd units from Quadlet `.container`/`.volume`/`.network` files. Requires podman ≥ 4.4 (GA Feb 2023). |
| Slot id | The stable, opaque, numeric primary key for a slot (per `/home/mint/hal0-rework-plan.md:683-690` §11.1). Replaces the slot `name` as the unit filename suffix. |
| Slot name | The mutable label for a slot (§11.1). Display only; not used in unit filenames. |
| `Mount.render_quadlet` | The new `src/hal0/providers/base.py` helper producing `src:dst:z` (Quadlet `Volume=` syntax). Replaces `Mount.render` which produced `--volume=src:dst,z` (podman-run argv syntax). |
| `slot_unit_name(slot_id)` | The new helper returning `hal0-slot-<id>.service`. Replaces the literal `f"hal0-slot@{name}.service"` pattern at 25+ call sites. |
| `slot_quadlet_name(slot_id)` | The new helper returning `hal0-slot-<id>.container`. |
| `HAL0_QUADLET` (env) | Q.1-only toggle; `=0` falls back to hand-rendered `.service`; default `=1` once Q.5 lands. Removed in a follow-up PR. |
| `host.containers.internal` | Podman's documented equivalent of docker's `host.docker.internal` (podman ≥ 4.0). Resolves to the host gateway IP from inside the container; set via `AddHost=host.containers.internal:host-gateway`. |

## Appendix C — Cross-spec references

- **P3-perms** (`/home/mint/hal0-specs/spec-p3-perms.md`) — hard prereq; provides `hal0-systemctl` seam + `User=hal0` daemon + `/etc/containers/systemd/` ownership row.
- **§11.1 slot id-keying** (`/home/mint/hal0-rework-plan.md:675-690`) — slot `id` becomes the unit filename suffix; lands in `spec-p3-slot-identity-ports.md` or is folded into P3-slots.
- **§11.2 PortAuthority** (`/home/mint/hal0-rework-plan.md:691-700`) — slot ports authority-issued; `PublishPort=` reads `plan.port` (already populated by PortAuthority).
- **P3-slots** (`/home/mint/hal0-specs/spec-p3-slots.final.md`) — `ContainerProvider.container_spec` is the input; `compute_config_drift` is the joint decision (§C.7).
- **§21.2 gfx-arch guard + cold-start timeouts** (`/home/mint/hal0-rework-plan.md:1396-1402`) — separate lane; Quadlet doesn't change the `system_info` probe; uses the same backend registry.
- **§23.4 sequencing** (`/home/mint/hal0-rework-plan.md:1713`) — W4 dependency: `§7.1d + P3-quadlet specs NEED AUTHORING` (this spec closes P3-quadlet).
- **§7.2 Container runtime & permissions** (`/home/mint/hal0-rework-plan.md:453-474`) — the parent section this spec implements the second half of.
- **§17 Installer / setup overhaul** (`/home/mint/hal0-rework-plan.md:1060-1099`) — Lane E; P3-quadlet is part of Lane E; cross-references `hal0-systemctl` (P3-perms) for the install-time writes.
- **`Mount.render` ↔ `Mount.render_quadlet` migration** — `Mount.render()` produced `--volume=src:dst,z` (podman-run argv syntax, comma-joined opts); `Mount.render_quadlet()` produces `src:dst:z` (Quadlet `Volume=` syntax, colon-separated opts). The shape difference is load-bearing.

---

**End of spec.**