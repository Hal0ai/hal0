"""ContainerProvider — podman-container-per-slot runtime (P1 tracer bullet).

Every GPU-LLM slot with ``profile`` set (or ``runtime="container"``) dispatches
through this provider instead of LemonadeProvider.

Architecture (design doc §2):
  - Profile supplies:    image + bench-tuned flags (+ MTP bundle if mtp=true).
  - Slot supplies:       model path, context_size, port.
  - Container provides:  the running llama-server process.

Container lifecycle → systemd template unit ``hal0-slot@<name>.service``:
  ExecStart = podman run --rm ... <image> --model <path> --port <n> <flags>
  ExecStop  = podman stop -t 20 hal0-slot-<name>

The slot's port is loopback-published (``-p 127.0.0.1:<port>:<port>``) so
the dispatcher can proxy it via a ``kind="remote"`` upstream entry without
exposing it on the LAN.

Mount design (IDENTICAL path, design doc §2 gotcha):
  /mnt/ai-models → /mnt/ai-models:ro
  GGUFs in the registry are symlinks whose targets are absolute
  /mnt/ai-models/... paths.  Mounting anywhere else dangles them.

GID resolution (reuses providers/_gpu.py):
  ubuntu:24.04 toolbox images lack ``render``/``video`` group entries.
  Pass numeric GIDs so the kernel gate on /dev/dri/renderD128 passes.

ABC compliance:
  Provider ABC has docker/systemd-shaped methods (build_env, start_cmd,
  container_spec, render_systemd_override).  ContainerProvider implements
  container_spec() and reuses the inherited render_systemd_override().
  build_env / start_cmd / health / infer are implemented as informational
  stubs or thin implementations — the real work is load/unload/status/health.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any

import httpx

from hal0.config.loader import load_profiles_config
from hal0.config.schema import ProfileConfig, resolve_profile_flags
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids
from hal0.providers.base import ContainerSpec, Provider

log = logging.getLogger(__name__)

# Path to the hal0-slot@ base template unit (installed by the package).
# ContainerProvider writes a complete self-contained unit here, *not*
# a drop-in, because the Lemonade migration (PR-9) retired the base
# template.  Writing a complete file means the manager never has to
# know whether the base exists.
_SYSTEMD_SYSTEM_DIR = Path("/etc/systemd/system")
_MODEL_STORE_MOUNT = "/mnt/ai-models"


# Container runtime binary.  Prefer podman (rootless, no daemon);
# fall back to docker when podman is not installed.  The HAL0_CONTAINER_RUNTIME
# env var overrides both so CI + alternate installs can pin a specific path.
def _container_runtime() -> str:
    """Resolve the container runtime binary path.

    Priority: $HAL0_CONTAINER_RUNTIME > /usr/bin/podman > /usr/bin/docker.
    Raises RuntimeError if neither is found.
    """
    import os
    import shutil

    override = os.environ.get("HAL0_CONTAINER_RUNTIME")
    if override:
        return override
    for candidate in ("/usr/bin/podman", "/usr/bin/docker"):
        if shutil.which(candidate):
            return candidate
    raise RuntimeError(
        "no container runtime found; install podman or docker or set HAL0_CONTAINER_RUNTIME"
    )


# Health-check tuning: poll GET /health on the slot port.
_HEALTH_POLL_INTERVAL_S = 2.0
_HEALTH_TIMEOUT_S = 180.0
_HEALTH_REQUEST_TIMEOUT_S = 3.0


def _resolve_profile(profile_name: str) -> ProfileConfig:
    """Load profiles.toml and return the named profile.

    Raises:
        KeyError: If the profile name is not in the catalog.
    """
    catalog = load_profiles_config()
    if profile_name not in catalog.profile:
        available = sorted(catalog.profile.keys())
        raise KeyError(f"profile {profile_name!r} not found in catalog; available: {available}")
    return catalog.profile[profile_name]


def _resolve_model_path(model_info: dict[str, Any]) -> str:
    """Return the absolute GGUF path for this model.

    Prefers ``model_info["path"]`` (populated by ModelRegistry.get);
    falls back to ``model_info["_model_key"]`` (the model-id string)
    so the container can attempt to locate the file at runtime.
    """
    path = model_info.get("path") or model_info.get("_model_key", "")
    if not path:
        raise ValueError(
            "model_info has no 'path' — registry lookup failed; "
            "ensure the model is registered before loading a container slot."
        )
    return str(path)


def _render_unit(
    slot_name: str,
    image: str,
    port: int,
    model_path: str,
    flags_str: str,
    runtime_bin: str | None = None,
    device_paths: list[str] | None = None,
    context_size: int | None = None,
    extra_args: str | None = None,
    model_alias: str | None = None,
) -> str:
    """Render a complete (non-drop-in) systemd unit for a container slot.

    Produces a self-contained unit that does NOT require a parent
    ``hal0-slot@.service`` template (retired in Lemonade migration PR-9).
    Written to ``/etc/systemd/system/hal0-slot@<name>.service``.

    ``runtime_bin``: override the container runtime binary (default: auto-detect
    via :func:`_container_runtime`).  Pass explicitly in tests to avoid
    depending on podman/docker being installed in the test environment.

    ``device_paths``: explicit GPU device nodes to pass via ``--device=``
    (default: auto-detect via :func:`resolve_gpu_device_paths`). Podman cannot
    recurse a ``--device=/dev/dri`` directory, so we pass each node explicitly.

    ``context_size``: slot context window → ``--ctx-size``.  Without it the
    container boots at llama-server's 4096 default regardless of the slot TOML.

    ``extra_args``: ``[server].extra_args`` passthrough, appended after the
    profile flags so slot-level overrides win.
    """
    runtime = runtime_bin or _container_runtime()
    devices = device_paths if device_paths is not None else resolve_gpu_device_paths()
    container_name = f"hal0-slot-{slot_name}"
    # Split the profile flags string into tokens for ExecStart quoting.
    flag_tokens = shlex.split(flags_str) if flags_str.strip() else []
    extra_tokens = shlex.split(extra_args) if extra_args and extra_args.strip() else []

    # Build the container run argv list.
    argv: list[str] = [
        runtime,
        "run",
        "--rm",
        f"--name={container_name}",
    ]
    # Explicit GPU device nodes (podman won't recurse the /dev/dri directory).
    for dev in devices:
        argv.append(f"--device={dev}")
    # Numeric GIDs for video+render groups (ubuntu:24.04 has no group names).
    for gid in resolve_gpu_group_ids():
        argv.append(f"--group-add={gid}")
    argv.extend(
        [
            "--security-opt=apparmor=unconfined",
            "--security-opt=seccomp=unconfined",
            f"--volume={_MODEL_STORE_MOUNT}:{_MODEL_STORE_MOUNT}:ro",
            # Loopback publish: expose slot port on 127.0.0.1 only.
            f"--publish=127.0.0.1:{port}:{port}",
            # Container image.
            image,
            # llama-server args follow the image (space-separated, not --key=val).
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--model",
            model_path,
        ]
    )
    # Advertise the hal0 registry model id (else llama-server reports the raw
    # GGUF basename, which the dispatcher can't match to hal0/* virtual names).
    if model_alias:
        argv.extend(["--alias", model_alias])
    # Slot context window (else llama-server defaults to 4096).
    if context_size is not None:
        argv.extend(["--ctx-size", str(context_size)])
    # Append bench-tuned profile flags, then [server].extra_args (slot wins).
    argv.extend(flag_tokens)
    argv.extend(extra_tokens)

    # ExecStart is a single long line; systemd accepts bare argv tokens.
    exec_start = " ".join(shlex.quote(a) if " " in a else a for a in argv)
    exec_stop = f"{runtime} stop -t 20 {container_name}"

    return "\n".join(
        [
            "# hal0 container slot — generated by ContainerProvider",
            "# Do not edit manually; regenerated on every slot load.",
            "",
            "[Unit]",
            f"Description=hal0 container inference slot ({slot_name})",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "Restart=no",
            f"SyslogIdentifier=hal0-slot-{slot_name}",
            "StandardOutput=journal",
            "StandardError=journal",
            "",
            f"ExecStart={exec_start}",
            f"ExecStop={exec_stop}",
            f"ExecStopPost=-{runtime} rm -f {container_name}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


class ContainerProvider(Provider):
    """Podman-container-per-slot inference backend.

    One instance is shared across all container slots (stateless —
    all config is passed per-call via slot_cfg / model_info, same
    contract as other Providers).

    Public API used by SlotManager:
      load(slot_cfg, model_info)  → writes + starts systemd unit.
      unload(slot_cfg)            → stops systemd unit.
      status(slot_cfg)            → systemctl is-active + /health.
      health(port)                → GET 127.0.0.1:<port>/health.
    """

    name = "container"

    # ── Provider ABC stubs (not used in the container path) ──────────────────

    def build_env(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> dict[str, str]:
        """Informational env block (not written to disk — container is self-contained)."""
        return {
            "HAL0_SLOT": str(slot_cfg.get("name", "")),
            "HAL0_RUNTIME": "container",
            "HAL0_PROFILE": str(slot_cfg.get("profile", "")),
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Not applicable — systemd starts the container."""
        raise NotImplementedError("ContainerProvider uses systemd; start_cmd() is unused")

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Direct inference passthrough (used by tests; dispatcher is primary path)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    def container_spec(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> ContainerSpec:
        """Build the ContainerSpec (satisfies ABC; used by render_systemd_override).

        NOTE: ContainerProvider uses _render_unit() for its own unit rendering
        rather than the inherited render_systemd_override(), because the base
        template (hal0-slot@.service) was retired in the Lemonade migration.
        This method is kept for ABC compliance and test helpers.
        """
        profile_name = slot_cfg.get("profile") or ""
        profile = _resolve_profile(profile_name)
        flags_str = resolve_profile_flags(profile)
        flag_tokens = shlex.split(flags_str) if flags_str.strip() else []

        model_path = _resolve_model_path(model_info)
        port = int(slot_cfg.get("port", 0))

        return ContainerSpec(
            image=profile.image,
            command=[
                # llama-server uses space-separated args (--host HOST, not --host=HOST).
                "--host",
                "0.0.0.0",
                "--port",
                str(port),
                "--model",
                model_path,
                *flag_tokens,
            ],
            mounts=[(_MODEL_STORE_MOUNT, _MODEL_STORE_MOUNT)],
            devices=resolve_gpu_device_paths(),
            group_add=[str(g) for g in resolve_gpu_group_ids()],
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            port=port,
            network_mode="",
            extra_args=[f"--publish=127.0.0.1:{port}:{port}"],
        )

    # ── ContainerProvider-specific control plane ──────────────────────────────

    def _unit_name(self, slot_name: str) -> str:
        return f"hal0-slot@{slot_name}.service"

    def _unit_path(self, slot_name: str) -> Path:
        return _SYSTEMD_SYSTEM_DIR / self._unit_name(slot_name)

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a subprocess synchronously (load/unload are blocking ops anyway)."""
        return subprocess.run(list(args), capture_output=True, text=True, check=check)

    async def health(self, port: int) -> dict[str, Any]:
        """Probe GET /health on the container port.

        Returns {"ok": bool, "status": str}.
        """
        url = f"http://127.0.0.1:{port}/health"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_REQUEST_TIMEOUT_S) as client:
                resp = await client.get(url)
                ok = resp.status_code == 200
                return {"ok": ok, "status": "healthy" if ok else f"http_{resp.status_code}"}
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            return {"ok": False, "status": str(exc)}

    async def wait_ready(self, port: int) -> None:
        """Poll /health until 200 or HEALTH_TIMEOUT_S exceeded.

        Raises:
            TimeoutError: If the container does not become healthy in time.
        """
        deadline = asyncio.get_event_loop().time() + _HEALTH_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            h = await self.health(port)
            if h.get("ok"):
                return
            await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
        raise TimeoutError(
            f"container slot port {port} did not become healthy within {_HEALTH_TIMEOUT_S}s"
        )

    def load_sync(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> None:
        """Write systemd unit, daemon-reload, enable, start (synchronous).

        Called from ``_spawn_locked`` (which is already inside an
        asyncio.to_thread-friendly path — SlotManager awaits the slot spawn
        via ``await self._spawn_locked(...)``).
        """
        slot_name: str = str(slot_cfg.get("name", ""))
        port: int = int(slot_cfg.get("port", 0))
        profile_name: str = str(slot_cfg.get("profile") or "")

        profile = _resolve_profile(profile_name)
        flags_str = resolve_profile_flags(profile)
        model_path = _resolve_model_path(model_info)

        model_table = slot_cfg.get("model") or {}
        context_size = model_table.get("context_size") if isinstance(model_table, dict) else None
        server_table = slot_cfg.get("server") or {}
        extra_args = server_table.get("extra_args") if isinstance(server_table, dict) else None
        # Registry model id → llama-server --alias so the container advertises
        # the hal0 id (not the raw GGUF basename) for dispatcher matching.
        model_alias = model_info.get("_model_key") or (
            model_table.get("default") if isinstance(model_table, dict) else None
        )

        unit_path = self._unit_path(slot_name)
        unit_text = _render_unit(
            slot_name,
            profile.image,
            port,
            model_path,
            flags_str,
            context_size=context_size,
            extra_args=extra_args,
            model_alias=model_alias,
        )

        log.info(
            "container.unit_write",
            extra={
                "slot": slot_name,
                "unit_path": str(unit_path),
                "image": profile.image,
                "port": port,
                "model_path": model_path,
            },
        )
        unit_path.write_text(unit_text)

        self._run("systemctl", "daemon-reload")
        # Enable so it survives reboots (best-effort — don't fail if already enabled).
        self._run("systemctl", "enable", self._unit_name(slot_name), check=False)
        self._run("systemctl", "restart", self._unit_name(slot_name))
        log.info(
            "container.unit_started",
            extra={"slot": slot_name, "unit": self._unit_name(slot_name)},
        )

    def unload_sync(self, slot_cfg: dict[str, Any]) -> None:
        """Stop and clean up the container unit (synchronous)."""
        slot_name: str = str(slot_cfg.get("name", ""))
        unit = self._unit_name(slot_name)
        log.info("container.unit_stop", extra={"slot": slot_name, "unit": unit})
        self._run("systemctl", "stop", unit, check=False)
        # Disable so it doesn't re-start on reboot.
        self._run("systemctl", "disable", unit, check=False)
        # Remove unit file so daemon-reload leaves no stale entry.
        unit_path = self._unit_path(slot_name)
        if unit_path.exists():
            unit_path.unlink()
            self._run("systemctl", "daemon-reload")

    def is_active(self, slot_name: str) -> bool:
        """Return True if the systemd unit is in an active state."""
        result = self._run("systemctl", "is-active", self._unit_name(slot_name), check=False)
        return result.returncode == 0

    def image_present(self, image: str) -> bool:
        """Return True if ``image`` is in the local container image store.

        Uses ``<runtime> image inspect`` (exit 0 = present, non-zero = missing).
        Runs synchronously — callers must dispatch to a thread executor when
        called from an async context.
        """
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return False
        result = subprocess.run(
            [runtime, "image", "inspect", image],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    async def pull_image_stream(self, image: str):
        """Async generator that runs ``<runtime> pull <image>`` and yields
        layer-progress dicts.

        Yields dicts::

            {"state": "pulling",  "layer": N, "total_layers": M, "line": "<raw line>"}
            {"state": "completed"}
            {"state": "failed",   "error": "<message>"}

        Layer counting heuristic (docker non-TTY output):
          - Each ``Pulling fs layer`` / ``Waiting`` / ``Verifying Checksum`` /
            ``Already exists`` lines indicate a discovered layer (M increments).
          - Each ``Pull complete`` / ``Download complete`` line indicates a
            finished layer (N increments, capped at M).
        """
        import asyncio as _asyncio

        try:
            runtime = _container_runtime()
        except RuntimeError as exc:
            yield {"state": "failed", "error": str(exc)}
            return

        proc = await _asyncio.create_subprocess_exec(
            runtime,
            "pull",
            image,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.STDOUT,
        )

        total_layers = 0
        done_layers = 0

        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                # Discover new layers.
                if any(
                    kw in line
                    for kw in (
                        "Pulling fs layer",
                        "Waiting",
                        "Verifying Checksum",
                        "Already exists",
                    )
                ):
                    total_layers += 1
                # Count finished layers.
                if (
                    "Pull complete" in line
                    or "Download complete" in line
                    or "Already exists" in line
                ):
                    done_layers = min(done_layers + 1, max(total_layers, 1))
                yield {
                    "state": "pulling",
                    "layer": done_layers,
                    "total_layers": total_layers,
                    "line": line,
                }
        except Exception as exc:
            yield {"state": "failed", "error": str(exc)}
            return
        finally:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()

        exit_code = await proc.wait()
        if exit_code == 0:
            yield {"state": "completed", "layer": done_layers, "total_layers": total_layers}
        else:
            yield {"state": "failed", "error": f"pull exited with code {exit_code}"}


# ── Module-level singleton (matches lemonade_provider() pattern) ─────────────

_container_provider: ContainerProvider | None = None


def container_provider() -> ContainerProvider:
    """Return the process-wide ContainerProvider singleton."""
    global _container_provider
    if _container_provider is None:
        _container_provider = ContainerProvider()
    return _container_provider


def resolved_command_for_slot(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> list[str] | None:
    """Return the canonical llama-server argv for a container slot.

    Used by the API layer (GET /api/slots + /config) to surface a
    ``resolved_command`` field without fabricating flags client-side.

    Returns the podman run argv *starting from the image tag* — the
    boilerplate podman preamble (--device, --group-add, --security-opt,
    --volume, --publish) is omitted because:
      a) it requires root to read GIDs (``resolve_gpu_group_ids``), and
      b) it is not useful for debugging inference behaviour.

    Returns ``None`` when the slot has no profile (not a container slot)
    or the profile lookup fails.
    """
    profile_name = str(slot_cfg.get("profile") or "")
    if not profile_name:
        return None
    try:
        profile = _resolve_profile(profile_name)
    except (KeyError, Exception):
        return None

    flags_str = resolve_profile_flags(profile)
    flag_tokens = shlex.split(flags_str) if flags_str.strip() else []

    # port: may be at top-level or nested under [slot]
    port = int(slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port") or 0)
    # model lives under [model] default (nested TOML table), not as a top-level string
    model_table = slot_cfg.get("model") or {}
    default_model = (
        model_table.get("default", "") if isinstance(model_table, dict) else str(model_table)
    )
    effective_model = model_path or str(default_model or "")
    context_size = model_table.get("context_size") if isinstance(model_table, dict) else None
    server_table = slot_cfg.get("server") or {}
    extra_args = server_table.get("extra_args") if isinstance(server_table, dict) else None
    extra_tokens = shlex.split(extra_args) if extra_args and extra_args.strip() else []

    argv: list[str] = [
        profile.image,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    if effective_model:
        argv += ["--model", effective_model]
    if default_model:
        argv += ["--alias", str(default_model)]
    if context_size is not None:
        argv += ["--ctx-size", str(context_size)]
    argv.extend(flag_tokens)
    argv.extend(extra_tokens)
    return argv


__all__ = ["ContainerProvider", "container_provider", "resolved_command_for_slot"]
