"""FLMProvider — AMD NPU (XDNA2) inference backend.

FLM (Flexible Language Model) targets the AMD Strix Halo NPU. Optional —
only loaded on hardware where the NPU driver is present and the FLM
binary tree + toolbox image are available.

Capabilities: chat, embed, ASR multiplexed on one NPU. A single
``flm serve <chat-tag> --embed 1 --asr 1`` process serves
``/v1/chat/completions`` + ``/v1/embeddings`` + ``/v1/audio/transcriptions``
against three different models simultaneously (embed-gemma + whisper-v3
+ the chat tag). The NPU serializes execution, so multiprocess FLM
provides no parallelism gain — port the single-multiplex design from
haloai's lib/providers/flm.py.

# Hybrid container packaging:
#   - Image: hal0-toolbox-flm carries Ubuntu 24.04 + ffmpeg + libxrt-npu2
#     + libboost-program-options. Freely redistributable, ~250 MB.
#   - Host: FLM binary tree (bin/, lib/, share/, deps/) bind-mounted in
#     at the SAME absolute path inside the container so that
#     ``bin/xclbins -> /mnt/ai-models/flm-ubuntu/share/flm/xclbins``
#     (an absolute symlink) still resolves. Discovered while
#     containerising on haloai 2026-05-15.
#   - Models: FLM's own cache dir (``~/.config/flm/models/``) bind-mounted
#     to a hal0-managed dir so model downloads persist across container
#     restarts.
# See docs/handoff-2026-05-15-autonomous.md for the test that proved
# this layout (Validate / serve / embed all succeeded; chat is blocked
# only by whoever currently holds the NPU's hardware context, not by
# anything container-side).
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from hal0.errors import Hal0Error
from hal0.providers.base import ContainerSpec, Provider

# ── Toolbox image ─────────────────────────────────────────────────────────────
# Default tag. Override via HAL0_TOOLBOX_IMAGE_FLM in api.env when running
# on hal0-test before the GHCR org is provisioned (PLAN §17).
_DEFAULT_FLM_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.43"

# ── On-disk layout ────────────────────────────────────────────────────────────
# The toolbox image is self-contained: it bundles FLM at /opt/fastflowlm/
# (binary, libs, xclbins, share assets) and symlinks /usr/local/bin/flm
# at the binary. ENTRYPOINT runs the in-image flm via tini, so the
# container_spec only supplies the subcommand + args.
#
# _DEFAULT_FLM_ROOT below is retained ONLY for the non-container fallback
# path used by start_cmd() (native systemd runs, tests, debug shells).
# Container runs no longer reference it.
_IMAGE_FLM_ROOT = "/opt/fastflowlm"
_DEFAULT_FLM_ROOT = "/opt/hal0/flm-ubuntu"
# FLM's per-user model cache. Bind-mounted writable so `flm pull` downloads
# survive container restarts. (Still used by the serving container_spec below.)
_DEFAULT_FLM_MODELS_DIR = "/var/lib/hal0/.config/flm/models"
# FLM hardcodes ~/.config/flm/models as its model cache. The container runs
# as the hal0 user with HOME=/var/lib/hal0, so the cache resolves to this
# path. The host source must be the SAME directory the host flm binary uses,
# otherwise flm reports every model as not-installed and hides them from
# the dashboard.

# ── Host FLM (catalog probe + pull) ─────────────────────────────────────────────
# The catalog probe and model pulls run the HOST flm binary directly — NOT the
# docker toolbox — so they use the exact binary + on-disk cache the NPU slot
# *serves* with. The toolbox had drifted to FLM v0.9.42 while the host serves
# v0.9.43: it (a) bind-mounted the empty `_DEFAULT_FLM_MODELS_DIR`, so every
# model reported installed=False and vanished from the dashboard (the UI hides
# not-installed models), and (b) emitted "model may not be compatible" warnings
# that broke JSON parsing once pointed at the real cache. Serving was already
# host-native; probe/pull now match it.
# See docs/superpowers/plans/2026-06-07-flm-host-probe-and-unified-model-storage.md.
#
# Run as the `hal0` user with HOME=/var/lib/hal0 so flm resolves its real cache
# at ~/.config/flm/models. All three are env-overridable for dev/test.
_HOST_FLM_BIN = os.environ.get("HAL0_FLM_BIN", "/usr/bin/flm")
_HOST_FLM_HOME = os.environ.get("HAL0_FLM_HOME", "/var/lib/hal0")
_HOST_FLM_USER = os.environ.get("HAL0_FLM_USER", "hal0")
# Real FLM cache (HOME/.config/flm/models). Replaces the toolbox bind-mount
# source for probe/pull; HAL0_FLM_MODELS_DIR still overrides if set.
_HOST_FLM_MODELS_DIR = (
    os.environ.get("HAL0_FLM_MODELS_DIR") or f"{_HOST_FLM_HOME}/.config/flm/models"
)

# ── Timeouts ───────────────────────────────────────────────────────────────────
# TIER1: separate health budget from infer budget.
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
# The sentinel completion can take a moment on cold NPU; give it a
# more generous read window than llama-server.
_HEALTH_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


class FLMHealthError(Hal0Error):
    """FLM health probe failed (typed for the error envelope)."""

    code = "slot.not_ready"
    status = 503


class FLMInferError(Hal0Error):
    """FLM inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


def _resolve_render_gid() -> int | None:
    """Look up the ``render`` group's numeric gid on the host.

    Slot containers need this group to read /dev/accel/accel0 and
    /dev/dri/renderD128. The gid varies between hosts (993 on Strix Halo
    LXCs, 109 on some bare-metal Debian, etc.) so we resolve once at
    container-spec build time rather than baking it into the image.

    Returns ``None`` if the group can't be resolved — the slot will still
    launch but device reads may fail; the slot's health probe will catch
    it.
    """
    try:
        import grp

        return grp.getgrnam("render").gr_gid
    except (KeyError, ImportError, OSError):
        return None


class FLMProvider(Provider):
    """Provider for the AMD NPU FLM backend.

    Health probe REQUIRES a real /v1/chat/completions round-trip with
    max_tokens=1, not just a populated /v1/models list. This is the
    Tier 1 fix for the haloai lib/slots.py:899-920 bug.
    """

    name = "flm"

    # ── Env / argv ─────────────────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Build HAL0_* env vars for an FLM slot.

        Returned vars are stamped into the slot's EnvironmentFile so the
        rendered docker command can refer to them via ``${HAL0_*}``. Kept
        separate from container_spec so non-container callers (tests,
        debug shells, native systemd fallback) can use them too.
        """
        port = slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port", 8086)
        # [model].context_size is the SlotConfig shape (container slots);
        # ctx_size / defaults.context_size are legacy (pre-container) shapes.
        ctx = (
            (slot_cfg.get("model") or {}).get("context_size")
            or slot_cfg.get("ctx_size")
            or slot_cfg.get("defaults", {}).get("context_size", 8192)
        )
        flm_tag = model_info.get("flm_tag") or model_info.get("_model_key") or "qwen3:0.6b"

        npu_table = slot_cfg.get("npu") or {}
        defaults = slot_cfg.get("defaults") or {}
        if slot_cfg.get("npu") is not None:
            # [npu] table is PRIMARY when present — explicit asr=false must
            # win even if stale legacy defaults say otherwise.
            load_asr = "1" if npu_table.get("asr") else "0"
            load_embed = "1" if npu_table.get("embed") else "0"
        else:
            # Legacy (pre-container) fallback — still reachable from legacy
            # TOMLs without an [npu] table (api/__init__.py reads the same
            # ``defaults.load_*`` shape): truthy check, same semantics as
            # before the [npu] table existed.
            load_asr = "1" if defaults.get("load_asr") else "0"
            load_embed = "1" if defaults.get("load_embed") else "0"

        return {
            "HAL0_FLM_TAG": str(flm_tag),
            "HAL0_PORT": str(port),
            "HAL0_FLM_CTX": str(ctx),
            "HAL0_FLM_LOAD_ASR": load_asr,
            "HAL0_FLM_LOAD_EMBED": load_embed,
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Return argv for the native ``flm serve`` invocation.

        Used by tests and the fallback systemd-without-Docker path. The
        primary deployment path is ``container_spec``.
        """
        binary = os.environ.get("HAL0_FLM_BINARY", f"{_DEFAULT_FLM_ROOT}/bin/flm")
        argv = [
            binary,
            "serve",
            env["HAL0_FLM_TAG"],
            "--host",
            "0.0.0.0",
            "--port",
            env["HAL0_PORT"],
            "--ctx-len",
            env["HAL0_FLM_CTX"],
        ]
        if env.get("HAL0_FLM_LOAD_EMBED") == "1":
            argv += ["--embed", "1"]
        if env.get("HAL0_FLM_LOAD_ASR") == "1":
            argv += ["--asr", "1"]
        return argv

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, _slot_cfg: dict[str, Any]) -> str:
        """Return the FLM toolbox image reference.

        Allows pre-GHCR-org deploys to override via env var (matches the
        Vulkan/ROCm toolbox pattern).
        """
        return os.environ.get("HAL0_TOOLBOX_IMAGE_FLM", _DEFAULT_FLM_IMAGE)

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec for FLM in the toolbox image.

        The toolbox image is self-contained: FLM is built in and lives at
        /opt/fastflowlm/ (binary, libs, xclbins, share assets). The image
        ENTRYPOINT is ``tini -- /usr/local/bin/flm``, so the command we
        pass becomes the flm subcommand + args — no host bind-mount of
        the binary tree is needed.

        Only the model cache is bind-mounted, so ``flm pull`` downloads
        survive container restarts.

        FLM needs ``/dev/accel/accel0`` for the AMD XDNA2 NPU. ``/dev/dri``
        is included because some FLM model loaders hit iGPU helpers
        during init.
        """
        env = self.build_env(slot_cfg, model_info)
        port = int(env["HAL0_PORT"])

        paths = slot_cfg.get("_paths", {}) or {}
        flm_models = (
            paths.get("flm_models")
            or os.environ.get("HAL0_FLM_MODELS_DIR")
            or _DEFAULT_FLM_MODELS_DIR
        )

        # Only the model cache is bind-mounted. FLM hardcodes
        # ~/.config/flm/models internally; map our hal0-managed cache
        # to that path. The toolbox image runs as the non-root ``hal0``
        # user (uid 1000, HOME=/var/lib/hal0), so ~/.config resolves to
        # /var/lib/hal0/.config — NOT /root/.config. Mounting at the
        # wrong HOME would silently drop every pulled model into the
        # container's writable overlay and lose it on exit.
        mounts: list[tuple[str, str]] = [
            (flm_models, "/var/lib/hal0/.config/flm/models"),
        ]

        # Build the argv passed to the image's ENTRYPOINT. The image runs
        # /usr/local/bin/flm via tini, so what we provide here is the
        # subcommand + flags — NOT a binary path. Passing an absolute
        # binary path here would be treated by flm as a stray positional
        # argument and rejected with "too many positional options".
        command: list[str] = [
            "serve",
            env["HAL0_FLM_TAG"],
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--ctx-len",
            env["HAL0_FLM_CTX"],
        ]
        if env["HAL0_FLM_LOAD_EMBED"] == "1":
            command += ["--embed", "1"]
        if env["HAL0_FLM_LOAD_ASR"] == "1":
            command += ["--asr", "1"]

        # render group resolves dynamically at run-time; use the numeric gid
        # so the spec doesn't depend on /etc/group in the container.
        render_gid = _resolve_render_gid()

        return ContainerSpec(
            image=self.image_ref(slot_cfg),
            command=command,
            env={
                # Image-internal paths (the Dockerfile installs FLM at
                # /opt/fastflowlm and XRT at /opt/xilinx/xrt).
                "FLM_CONFIG_PATH": f"{_IMAGE_FLM_ROOT}/share/flm/model_list.json",
                # Docker `--env LD_LIBRARY_PATH=...` REPLACES the image ENV
                # set by the Dockerfile rather than augmenting it, so we
                # must spell out every path here even though the image's
                # own ENV would be correct on its own. libxrt_coreutil.so.2
                # (XRT runtime) and the FLM libs (libllama_npu.so &c, dlopen'd
                # when models load) both need to be findable; missing either
                # crashes /usr/local/bin/flm at startup before main().
                "LD_LIBRARY_PATH": f"{_IMAGE_FLM_ROOT}/lib:/opt/xilinx/xrt/lib:/usr/lib/x86_64-linux-gnu",
            },
            mounts=mounts,
            # /dev/accel/accel0: XDNA2 NPU. /dev/dri/renderD128: iGPU companion.
            devices=["/dev/accel/accel0", "/dev/dri/renderD128"],
            cap_add=[],
            # apparmor=unconfined is required in LXC; on bare metal a
            # tailored profile would be tighter but Strix Halo deployments
            # under Proxmox LXC are the primary target.
            # seccomp=unconfined matches the GPU slot rendering so
            # _render_unit_from_spec doesn't need special-casing here.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[str(render_gid)] if render_gid is not None else [],
            port=port,
            # FLM's /v1/* server needs to be reachable from the dispatcher
            # at 127.0.0.1:<port>. Use port-mapping rather than network=host
            # so multiple slots can coexist with overlapping internal ports.
            # The renderer derives --publish=127.0.0.1:<port>:<port> from
            # spec.port declaratively — no hand-rolled "-p" here.
            network_mode="",
            extra_args=[
                # NPU model weights are pinned in DMA-locked memory; this
                # is the same flag haloai's systemd unit sets.
                "--ulimit memlock=-1",
            ],
        )

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Health probe with a real inference round-trip.

        TIER1: PLAN.md §5 — the haloai version (lib/providers/flm.py)
        reported "ok" as soon as `/v1/models` returned data, even when
        the model failed to actually load on the NPU. We now require
        BOTH:
          1. non-empty /v1/models
          2. a /v1/chat/completions with max_tokens=1 returning a
             well-formed response with at least one choice

        Returns {"ok": bool, "status": str, "model": str|None, ...}.
        """
        models_url = f"http://127.0.0.1:{port}/v1/models"
        chat_url = f"http://127.0.0.1:{port}/v1/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                # Step 1: /v1/models must be non-empty.
                models_resp = await client.get(models_url)
                models_resp.raise_for_status()
                data = models_resp.json()
                models = data.get("data", [])
                if not models:
                    return {
                        "ok": False,
                        "status": "models_endpoint_empty",
                        "detail": "/v1/models returned no entries",
                    }
                model_id = models[0].get("id")

            # Step 2: real inference round-trip.  # TIER1
            async with httpx.AsyncClient(timeout=_HEALTH_INFER_TIMEOUT) as client:
                probe_body = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                    "stream": False,
                }
                chat_resp = await client.post(chat_url, json=probe_body)
                if chat_resp.status_code != 200:
                    return {
                        "ok": False,
                        "status": f"sentinel_completion_http_{chat_resp.status_code}",
                        "detail": chat_resp.text[:200],
                        "model": model_id,
                    }
                try:
                    body = chat_resp.json()
                except Exception:
                    return {
                        "ok": False,
                        "status": "sentinel_completion_unparseable",
                        "model": model_id,
                    }
                if not body.get("choices"):
                    return {
                        "ok": False,
                        "status": "sentinel_completion_no_choices",
                        "model": model_id,
                    }
            return {"ok": True, "status": "ready", "model": model_id}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            # TIER1: do not silently swallow.
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough /v1/chat/completions to FLM."""
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise FLMInferError(
                f"FLM returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise FLMInferError(
                f"FLM transport error: {exc}",
                details={"port": port},
            ) from exc


# ── FLM catalog probe ─────────────────────────────────────────────────────────
#
# What FLM can actually serve. Slot picking lives in
# hal0.capabilities.catalog, which calls flm_served_models() to learn
# which model tags the NPU advertises.
#
# Design call (2026-05-20): cache the probe result until process restart.
# Toolbox image upgrades require an hal0-api restart anyway, and the
# catalog refresh path is rare enough that a TTL would add complexity
# without buying us much. Tests + a future manual-refresh CLI can call
# reset_flm_catalog_cache() to invalidate.


_FLM_CATALOG_CACHE: list[dict[str, Any]] | None = None
_FLM_PROBE_OK: bool = False


def _classify_flm_model(entry: dict[str, Any]) -> list[str]:
    """Map an ``flm list -j`` entry to hal0 capability strings.

    Rules, applied in order:

      1. ``label`` contains ``"embeddings"``                → ``["embed"]``
      2. chat-y signal (``"reasoning"`` or ``"tool-calling"`` label) →
         ``["chat"]`` and, if ``asr: true`` is also set, append
         ``"stt"`` (e.g. ``gemma4-it:e2b`` is a multimodal chat + asr
         model).
      3. ``asr: true`` alone, no chat-y labels (e.g. ``whisper-v3:turbo``)
         → ``["stt"]``.
      4. Anything else falls back to ``["chat"]``.

    Returned strings line up with the hal0 capability vocabulary used by
    :mod:`hal0.capabilities.catalog` (``embed``, ``chat``, ``stt``).
    """
    labels = set(entry.get("label", []) or [])
    if "embeddings" in labels:
        return ["embed"]
    has_asr = bool(entry.get("asr"))
    if labels & {"reasoning", "tool-calling"}:
        return ["chat", "stt"] if has_asr else ["chat"]
    if has_asr:
        return ["stt"]
    return ["chat"]


def flm_host_spawn_kwargs() -> dict[str, Any]:
    """subprocess/asyncio kwargs to run host ``flm`` as the hal0 identity.

    Sets ``HOME`` so flm resolves ``~/.config/flm/models`` to the real on-disk
    cache, and drops to the ``hal0`` user/group when we have the privilege
    (hal0-api runs as ``root`` in prod). When already running as a non-root
    user (dev/test), ``user=`` would raise ``PermissionError``, so we skip it
    and rely on the caller already being the model owner.

    Accepted by both :func:`subprocess.run` and
    :func:`asyncio.create_subprocess_exec` (the ``user``/``group`` kwargs are
    available on Python ≥ 3.9).
    """
    import pwd

    kwargs: dict[str, Any] = {"env": {**os.environ, "HOME": _HOST_FLM_HOME}}
    try:
        if os.geteuid() == 0:
            pwd.getpwnam(_HOST_FLM_USER)  # raises KeyError if the user is absent
            kwargs["user"] = _HOST_FLM_USER
            kwargs["group"] = _HOST_FLM_USER
    except (KeyError, OSError, AttributeError):
        # Unknown user, or geteuid unavailable (non-POSIX) — run as-is.
        pass
    return kwargs


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Decode the first top-level JSON object in ``text``.

    ``flm list -j`` normally prints clean JSON, but can prepend ``[WARNING]``
    lines (e.g. a model written by a newer flm). Scan to the first ``{`` and
    decode just that object so a stray preamble (or trailing) line can't null
    the whole catalog.
    """
    import json

    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _probe_flm_catalog() -> list[dict[str, Any]] | None:
    """Run host ``flm list -j`` (as the hal0 user) and parse the JSON output.

    Uses the host ``/usr/bin/flm`` — the SAME binary the NPU slot serves
    with, NOT a docker toolbox — so the reported ``installed`` flag reflects the weights
    actually on disk at ``~/.config/flm/models`` and there is no
    toolbox-vs-host version skew. Returns the raw model list (FLM's own shape)
    or ``None`` on any failure (missing binary, perms, crash, parse error,
    timeout); the caller treats ``None`` as "advertise NPU with no served
    models" so the dashboard renders cleanly.

    No ``--device`` is needed: ``flm list`` reads the bundled
    ``model_list.json`` and checks on-disk files; it never touches NPU
    hardware, so the probe still works on dev hosts without XDNA.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [_HOST_FLM_BIN, "list", "-j"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30.0,
            **flm_host_spawn_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    payload = _extract_json_object(proc.stdout.decode("utf-8", errors="replace"))
    if payload is None:
        return None
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    return models


def flm_served_models() -> list[dict[str, Any]]:
    """Return what the FLM toolbox can serve, classified into hal0 capabilities.

    Each entry is a dict in hal0's shape (NOT FLM's raw JSON)::

        {
            "tag":          "embed-gemma:300m",  # id `flm pull / flm serve` consume
            "capabilities": ["embed"],           # hal0 capability strings
            "installed":    False,               # FLM reports weights on disk
            "size_bytes":   300_000_000,         # FLM's reported model size
            "footprint_gb": 0.62,                # FLM's runtime memory estimate
            "family":       "embed-gemma",       # details.family — useful for grouping
        }

    Cached at module scope on first successful probe; subsequent calls
    are O(1). On probe failure the result is an empty list (also
    cached) so the catalog still renders — call
    :func:`reset_flm_catalog_cache` to force a re-probe.
    """
    global _FLM_CATALOG_CACHE, _FLM_PROBE_OK
    if _FLM_CATALOG_CACHE is not None:
        return _FLM_CATALOG_CACHE

    raw = _probe_flm_catalog()
    if raw is None:
        _FLM_CATALOG_CACHE = []
        _FLM_PROBE_OK = False
        return _FLM_CATALOG_CACHE

    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("model") or entry.get("name")
        if not isinstance(tag, str) or not tag:
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        out.append(
            {
                "tag": tag,
                "capabilities": _classify_flm_model(entry),
                "installed": bool(entry.get("installed")),
                "size_bytes": int(entry.get("size") or 0),
                "footprint_gb": float(entry.get("footprint") or 0.0),
                "family": str(details.get("family") or ""),
            }
        )

    _FLM_CATALOG_CACHE = out
    _FLM_PROBE_OK = True
    return _FLM_CATALOG_CACHE


def reset_flm_catalog_cache() -> None:
    """Drop the cached FLM catalog so the next call re-probes.

    Exposed for tests and for a future "refresh catalog" CLI/UI hook.
    Production code relies on process restart to invalidate.
    """
    global _FLM_CATALOG_CACHE, _FLM_PROBE_OK
    _FLM_CATALOG_CACHE = None
    _FLM_PROBE_OK = False


def is_flm_tag(model_id: str) -> bool:
    """True iff ``model_id`` matches an FLM-served tag.

    Routing helper for the pull endpoint: FLM tags are Ollama-style
    ``family:size`` ids (``qwen3:0.6b``, ``deepseek-r1:8b``, …) and
    don't carry HF coords, so the generic HF pull path can't pull them.
    Looks them up against the cached :func:`flm_served_models` so we
    only treat ids the toolbox actually knows about — a stray ``foo:bar``
    falls through to the HF resolver and gets a proper 422.
    """
    if ":" not in model_id:
        return False
    return any(m["tag"] == model_id for m in flm_served_models())


def is_installed_flm_id(model_id: str) -> bool:
    """True iff ``model_id`` is the ``<tag>-FLM`` id of an INSTALLED FLM model.

    FLM models are host-flm-owned tags and are **never** in hal0's ModelRegistry
    (see docs/internal/brain-redesign/{model,slot}-shapes-audit-2026-06-07.md):
    the registry is the source of truth for GGUF/local models only. So the
    slot-apply registry gate (``routes/slots.py``) wrongly rejects a perfectly
    loadable FLM model — yet the npu.toml ``[model].default`` config path loads
    the same id fine. This lets slot-apply accept an on-disk
    FLM model by *provider-resolvability* instead of registry membership.

    ``model_id`` is the served ``-FLM`` form (``gemma4-it-e4b-FLM``); we
    match it against the forward transform of each installed probe tag
    (``gemma4-it:e4b`` → ``gemma4-it-e4b-FLM``), the same map used to synthesise
    the picker rows in ``routes/models.py``.
    """
    if not model_id.endswith("-FLM"):
        return False
    return any(
        m.get("installed") and m["tag"].replace(":", "-") + "-FLM" == model_id
        for m in flm_served_models()
    )


def flm_id_to_tag(model_id: str) -> str | None:
    """Resolve a hal0 ``<tag>-FLM`` id back to FLM's native ``family:size`` tag.

    Inverse of the forward map in :func:`is_installed_flm_id`
    (``gemma4-it:e2b`` → ``gemma4-it-e2b-FLM``). FLM's ``serve``/``pull``
    subcommands only accept the colon tag, so any code that hands a hal0
    catalog id straight to FLM (the slot manager's ``flm_tag`` stamp) must
    translate first — otherwise FLM answers ``Model not found``.

    Matches on the served-catalog tag regardless of ``installed`` (the
    transform is a pure naming map). Returns the colon tag, or ``None`` when
    ``model_id`` isn't a recognised FLM id or the catalog probe is empty —
    the caller then falls back to the raw id.
    """
    if not model_id.endswith("-FLM"):
        return None
    for m in flm_served_models():
        tag = m.get("tag")
        if isinstance(tag, str) and tag.replace(":", "-") + "-FLM" == model_id:
            return tag
    return None


def flm_pull_command(tag: str) -> tuple[list[str], str]:
    """Return ``(argv, host_models_dir)`` for a host ``flm pull <tag>`` run.

    Uses the host ``/usr/bin/flm`` (same binary the NPU slot serves with),
    NOT a docker toolbox. The caller spawns ``argv`` with
    :func:`flm_host_spawn_kwargs` so it runs as the ``hal0`` user with ``HOME``
    set; downloads land in ``~/.config/flm/models`` (the real cache) owned by
    ``hal0``, matching serving. ``host_models_dir`` is returned so callers can
    locate the weights for progress polling + registry bookkeeping.

    No ``--device``: ``flm pull`` downloads files; it doesn't touch the NPU,
    so it still runs on dev hosts without XDNA passthrough.
    """
    return [_HOST_FLM_BIN, "pull", tag], _HOST_FLM_MODELS_DIR


_FLM_PROGRESS_RE = re.compile(
    r"Downloading:\s*([0-9.]+)%\s*\(([0-9.]+)\s*([KMG]?)B\s*/\s*([0-9.]+)\s*([KMG]?)B\)"
)


def parse_flm_progress(line: str) -> tuple[int, int] | None:
    """Extract ``(bytes_downloaded, bytes_total)`` from a ``flm pull`` line.

    FLM emits progress like::

        [FLM]  Downloading: 38.8% (253.0MB / 652.1MB)

    Returns ``None`` on lines that don't match (status, hash check,
    blank, etc.) so the caller can skip them without branching.
    """
    m = _FLM_PROGRESS_RE.search(line)
    if not m:
        return None
    _pct, cur, cur_unit, tot, tot_unit = m.groups()
    units = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
    try:
        bytes_downloaded = int(float(cur) * units[cur_unit])
        bytes_total = int(float(tot) * units[tot_unit])
    except (KeyError, ValueError):
        return None
    return bytes_downloaded, bytes_total
