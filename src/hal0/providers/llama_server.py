"""LlamaServerProvider — llama.cpp inference backend.

Supports Vulkan (default) and ROCm (opt-in via slot_cfg["backend"]).
Handles: chat completions, embeddings, reranking, vision (mmproj).

Port target: haloai lib/providers/llama_server.py (382 lines).
See PLAN.md §1 (v1 ships — llama.cpp provider), §3 (module port plan),
PLAN.md §5 Tier 1 (atomic env writes, health probe hardening).

# NOTE: The haloai source reads slot configs in a flat-or-nested
# fallback shape (`_g(cfg, "port", "slot", "port", 8081)`). hal0's
# SlotConfig pydantic model normalises this upstream — see
# `hal0.config.schema`. For Phase 1 we keep the flat-or-nested reader
# so legacy slot TOMLs continue to load without a migration; Phase 5
# tightens this once the migration framework lands.

# NOTE: Backend-profile lookups (`_load_backend_flags`,
# `_load_backend_meta`) reach into `lib.config.load_backend` in
# haloai. hal0 does not yet ship a backend-profile registry; the
# lookups here fall back to {} on any failure, which preserves
# the haloai contract that slot config wins over backend defaults.
"""

from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path
from typing import Any

import httpx

from hal0.errors import Hal0Error
from hal0.providers._gpu import resolve_gpu_group_ids as _resolve_gpu_group_ids
from hal0.providers.base import ContainerSpec, Provider
from hal0.slots.flag_merge import merge_flags

log = logging.getLogger(__name__)

# ── Toolbox image refs ─────────────────────────────────────────────────────────
# Per PLAN.md §12 the hal0 toolbox images are published under
# ghcr.io/hal0ai/. The actual GHCR org/images are not published yet
# (see PLAN §17 Risks: "Toolbox images on ghcr.io/hal0ai/ blocked by
# org provisioning"). Names are fixed now so the rest of the system can
# wire against them; Phase 5 publishes digests into manifest.json.
_HAL0_TOOLBOX_IMAGES = {
    "vulkan": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1",
    "rocm": "ghcr.io/hal0ai/hal0-toolbox-rocm:v1",
}

# ── Timeouts ───────────────────────────────────────────────────────────────────
# TIER1: Health probe gets its own short timeout, infer gets a long
# read budget so big prompts don't trip on a 5s read.
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


class ProviderHealthError(Hal0Error):
    """Provider health probe failed (typed for the error envelope)."""

    code = "slot.not_ready"
    status = 503


class ProviderInferError(Hal0Error):
    """Provider inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


def _g(
    cfg: dict[str, Any], flat_key: str, section: str, legacy_key: str, default: Any = None
) -> Any:
    """Read flat key, fall back to nested legacy location.

    Mirrors haloai's flat-or-nested slot TOML reader.  Hal0 slots are
    flat per SlotConfig, but legacy migration data may still be nested.
    """
    if flat_key in cfg:
        return cfg[flat_key]
    return cfg.get(section, {}).get(legacy_key, default)


def _load_backend_flags(_backend_id: str) -> dict[str, Any]:
    """Return backend profile [flags] dict, or empty dict if profile missing.

    # NOTE: hal0 does not yet ship a backend-profile registry; this is
    # a future hook (Phase 5). Returning {} preserves the haloai
    # contract that slot config wins over backend defaults.
    """
    return {}


def _load_backend_meta(_backend_id: str) -> dict[str, Any]:
    """Return backend profile [backend] dict, or empty dict if profile missing.

    # NOTE: see _load_backend_flags. Same Phase 5 hook.
    """
    return {}


class LlamaServerProvider(Provider):
    """Provider for llama.cpp (llama-server) backends.

    Toolbox images (PLAN.md §12):
      Vulkan: ghcr.io/hal0ai/hal0-toolbox-vulkan:v1
      ROCm:   ghcr.io/hal0ai/hal0-toolbox-rocm:v1

    Backend is selected by slot_cfg["backend"]: "vulkan" | "rocm" | "cpu".
    "cpu" maps to the Vulkan image (it runs CPU when no GPU is exposed).
    Default: "vulkan".
    """

    name = "llama-server"

    # ── Env / argv construction ────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Build HAL0_* env vars for a llama-server slot.

        Ported from haloai lib/providers/llama_server.py.  Renames
        HALOAI_* → HAL0_* per the rebrand.

        Args:
            slot_cfg:   slot TOML dict (flat or legacy-nested).
            model_info: model registry entry.

        Returns:
            dict suitable for hal0.config.env.write_env_atomic.
        """
        paths = slot_cfg.get("_paths", {})

        port = _g(slot_cfg, "port", "slot", "port", 8081)
        model_path = model_info.get("path", "")
        is_embedding = (
            model_info.get("embedding", False)
            or _g(slot_cfg, "embedding", "defaults", "embedding", False)
            or "embed" in (model_info.get("capabilities") or [])
        )

        # Slot backend wins; model preferred_backend is legacy fallback.
        backend = (
            slot_cfg.get("backend")
            or slot_cfg.get("slot", {}).get("backend")
            or model_info.get("preferred_backend")
            or "vulkan"
        )

        backend_flags = _load_backend_flags(backend)
        backend_meta = _load_backend_meta(backend)

        # Binary/lib_path: backend profile wins over paths registry defaults.
        if backend == "rocm":
            default_binary = paths.get("llama_rocm", "/opt/llama-rocm/llama-server")
            rocm_lib = paths.get("llama_rocm_lib", "/opt/llama-rocm/lib")
            default_ld_path = rocm_lib + ":" + rocm_lib + "/rocm"
        else:  # vulkan / cpu
            default_binary = paths.get("llama_vulkan", "/opt/llama-vulkan/llama-server")
            default_ld_path = paths.get("llama_vulkan_lib", "/opt/llama-vulkan/lib")

        binary = backend_meta.get("binary", default_binary)
        ld_path = backend_meta.get("lib_path", default_ld_path)

        def _slot_or_backend(
            flat_key: str, section: str, legacy_key: str, default: Any = None
        ) -> Any:
            """Slot value (flat or nested) > backend profile flag > default."""
            slot_val = _g(slot_cfg, flat_key, section, legacy_key)
            if slot_val is not None:
                return slot_val
            bf_val = backend_flags.get(flat_key)
            if bf_val is not None:
                return bf_val
            return default

        # Slot ctx_size wins; clamp to per-model max_context cap.
        ctx = (
            _slot_or_backend("ctx_size", "defaults", "context_size")
            or model_info.get("max_context")
            or 8192
        )
        model_max = model_info.get("max_context")
        if model_max:
            ctx = min(ctx, model_max)

        threads = _slot_or_backend("threads", "defaults", "threads", 12)
        parallel = _slot_or_backend("parallel", "defaults", "parallel", 2)
        gpu_layers = model_info.get("gpu_layers") or _slot_or_backend(
            "gpu_layers", "defaults", "gpu_layers", 999
        )
        batch_size = _slot_or_backend("batch_size", "defaults", "batch_size", 4096)
        ubatch_size = _slot_or_backend("ubatch_size", "defaults", "ubatch_size")
        threads_batch = _slot_or_backend("threads_batch", "defaults", "threads_batch")
        cache_k = _slot_or_backend("cache_k", "defaults", "cache_type_k")
        cache_v = _slot_or_backend("cache_v", "defaults", "cache_type_v")
        cache_reuse = _slot_or_backend("cache_reuse", "defaults", "cache_reuse")
        defrag_thold = _slot_or_backend("defrag_thold", "defaults", "defrag_thold")
        slot_ps = _slot_or_backend("slot_prompt_similarity", "defaults", "slot_prompt_similarity")
        chat_template = _slot_or_backend(
            "chat_template", "defaults", "chat_template_file"
        ) or model_info.get("chat_template_file")

        use_mlock = (
            model_info["mlock"]
            if "mlock" in model_info
            else _slot_or_backend("mlock", "defaults", "mlock", False)
        )
        use_no_mmap = (
            model_info["no_mmap"]
            if "no_mmap" in model_info
            else _slot_or_backend("no_mmap", "defaults", "no_mmap", False)
        )

        extra: list[str] = []
        if _slot_or_backend("flash_attn", "defaults", "flash_attention", False):
            extra.append("--flash-attn on")
        if use_no_mmap:
            extra.append("--no-mmap")
        if use_mlock:
            extra.append("--mlock")
        if _slot_or_backend("jinja", "defaults", "jinja", False):
            extra.append("--jinja")
        if is_embedding:
            extra.append("--embedding")
        if _slot_or_backend("metrics", "defaults", "metrics", True):
            extra.append("--metrics")
        if _slot_or_backend("verbose", "defaults", "verbose", True):
            extra.append("--verbose")
        if batch_size:
            extra.extend(["-b", str(batch_size)])
        if ubatch_size:
            extra.extend(["-ub", str(ubatch_size)])
        if threads_batch:
            extra.extend(["--threads-batch", str(threads_batch)])
        if cache_k:
            extra.extend(["--cache-type-k", str(cache_k)])
        if cache_v:
            extra.extend(["--cache-type-v", str(cache_v)])
        if cache_reuse is not None:
            extra.extend(["--cache-reuse", str(cache_reuse)])
        if defrag_thold is not None:
            extra.extend(["--defrag-thold", str(defrag_thold)])
        if slot_ps is not None:
            extra.extend(["--slot-prompt-similarity", str(slot_ps)])
        if chat_template:
            extra.extend(["--chat-template-file", str(chat_template)])
        # Vision projector: --mmproj loads the multimodal projector so
        # the model can accept images. Per-model toggle from the registry.
        mmproj = model_info.get("mmproj")
        if mmproj:
            extra.extend(["--mmproj", str(mmproj)])
        if model_info.get("extra_args"):
            extra.append(model_info["extra_args"])

        # ── Phase 1 A3: model.defaults.extra_args ⊕ slot.server.extra_args ──
        # Merge the model registry's freeform CLI defaults with the slot's
        # [server].extra_args override.  Slot flags win on collisions (except
        # for the append-list flags --lora/--draft-model/--override-kv which
        # llama-server accepts repeated occurrences of).  The merged string
        # is shlex-split so quoted values survive the trip through
        # HAL0_EXTRA_ARGS into argv.
        defaults = model_info.get("defaults") or {}
        model_extra_args = defaults.get("extra_args") if isinstance(defaults, dict) else None
        slot_server = slot_cfg.get("server") if isinstance(slot_cfg.get("server"), dict) else {}
        slot_extra_args = slot_server.get("extra_args") if slot_server else None
        merged = merge_flags(model_extra_args, slot_extra_args)
        if merged:
            extra.extend(shlex.split(merged))

        return {
            "HAL0_MODEL": str(model_path),
            "HAL0_PORT": str(port),
            "HAL0_CTX": str(ctx),
            "HAL0_THREADS": str(threads),
            "HAL0_PARALLEL": str(parallel),
            "HAL0_GPU_LAYERS": str(gpu_layers),
            "HAL0_BACKEND": str(backend),
            "HAL0_EXTRA_ARGS": " ".join(extra),
            "HAL0_BINARY": str(binary),
            "HAL0_LD_PATH": str(ld_path),
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Return argv for llama-server invocation outside systemd.

        Used by unit-template rendering and integration tests.
        """
        cmd = [
            env["HAL0_BINARY"],
            "--model",
            env["HAL0_MODEL"],
            "--port",
            env["HAL0_PORT"],
            "--host",
            "0.0.0.0",
            "--ctx-size",
            env["HAL0_CTX"],
            "--threads",
            env["HAL0_THREADS"],
            "--parallel",
            env["HAL0_PARALLEL"],
            "-ngl",
            env["HAL0_GPU_LAYERS"],
        ]
        extra = env.get("HAL0_EXTRA_ARGS", "").strip()
        if extra:
            cmd.extend(extra.split())
        return cmd

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Resolve the toolbox image for this slot.

        Resolution order:
          1. ``slot_cfg["image"]`` — explicit override from slot TOML
             (e.g. ``image = "hal0-toolbox-vulkan:dev"`` for local builds).
          2. ``HAL0_TOOLBOX_IMAGE_{BACKEND}`` env var — installer / operator
             override without editing slot TOML.  Example:
             ``HAL0_TOOLBOX_IMAGE_VULKAN=ghcr.io/hal0ai/...@sha256:abc``
             materialised by the installer at first-run.
          3. ``_HAL0_TOOLBOX_IMAGES[backend]`` — the schema default
             (``ghcr.io/hal0ai/hal0-toolbox-<backend>:v1``).

        Backends: "vulkan" | "rocm" | "cpu" (cpu falls through to vulkan
        since the vulkan image runs on cpu when no GPU is exposed).
        """
        # (1) Per-slot override always wins — but only a STRING is an
        # override. In raw slot dicts the [image] TOML section (#599
        # image-gen settings) shares this key; a dict here is config,
        # not an image ref (live CT105 'invalid reference format', Phase D).
        override = slot_cfg.get("image") or slot_cfg.get("slot", {}).get("image")
        if isinstance(override, str) and override:
            return override

        backend = (
            slot_cfg.get("backend")
            or slot_cfg.get("slot", {}).get("backend")
            or slot_cfg.get("defaults", {}).get("backend")
            or "vulkan"
        )
        if backend == "cpu":
            backend = "vulkan"

        # (2) Env-var override per backend (installer materialisation hook).
        env_key = f"HAL0_TOOLBOX_IMAGE_{backend.upper()}"
        env_override = os.environ.get(env_key, "").strip()
        if env_override:
            return env_override

        # (3) Default image map.
        image = _HAL0_TOOLBOX_IMAGES.get(backend)
        if image is None:
            raise ValueError(
                f"Unknown llama-server backend '{backend}'; "
                f"expected one of {list(_HAL0_TOOLBOX_IMAGES)}"
            )
        return image

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec for running llama-server in the toolbox image.

        The toolbox image sets ENTRYPOINT=llama-server, so command[] is
        ARGS only — never include "llama-server" as command[0] or the
        binary will see its own name as the first flag and exit.

        Strix Halo's iGPU surfaces through both /dev/kfd and /dev/dri
        for both Vulkan and ROCm; passing both is harmless on Vulkan-only
        and required for ROCm.
        """
        env = self.build_env(slot_cfg, model_info)

        port = int(_g(slot_cfg, "port", "slot", "port", 8081))
        model_path = env["HAL0_MODEL"]

        command: list[str] = [
            "--model",
            model_path,
            "--port",
            str(port),
            "--host",
            "0.0.0.0",
            "--ctx-size",
            env["HAL0_CTX"],
            "--threads",
            env["HAL0_THREADS"],
            "--parallel",
            env["HAL0_PARALLEL"],
            "-ngl",
            env["HAL0_GPU_LAYERS"],
        ]
        extra = env.get("HAL0_EXTRA_ARGS", "").strip()
        if extra:
            command.extend(extra.split())

        # Bind-mount the model directory so in-container path matches host.
        # _paths overrides win when the SlotManager injects them; otherwise
        # fall back to the HAL0_HOME-aware paths.models_dir() rather than
        # a hardcoded /var/lib/hal0/models so dev installs (HAL0_HOME set)
        # mount the right directory.
        slot_paths = slot_cfg.get("_paths", {}) or {}
        from hal0.config import paths as _cfg_paths

        models_base = slot_paths.get("models_base") or str(_cfg_paths.models_dir())
        mounts: list[tuple[str, str]] = [(models_base, models_base)]

        def _mount_dir(d: str) -> None:
            """Add (d, d) to mounts unless an ancestor is already mounted.

            Skips empty strings and relative paths — without the relative
            guard, an unset HAL0_MODEL_PATH passed through Path("").parent
            collapses to ``.``, which docker rejects with ``invalid mount
            path: '.' mount path must be absolute`` and the slot crashes
            into a restart loop before llama-server ever runs.
            """
            if not d or not d.startswith("/"):
                return
            for host, _ in mounts:
                # `d` already covered by an existing mount; nothing to do.
                if d == host or d.startswith(host.rstrip("/") + "/"):
                    return
            mounts.append((d, d))

        # The registry path may point inside the models_base, into a
        # shared store like /mnt/ai-models, or into a HuggingFace cache
        # whose snapshots/*.gguf entries are symlinks into ../../blobs/.
        # Without resolving the symlink we mount the snapshots dir but
        # not the blobs dir, and llama-server inside the container ENOENTs
        # on the symlink target. Always cover both the path-as-given AND
        # the realpath's parent so the symlink resolves inside.
        model_dir = str(Path(model_path).parent)
        _mount_dir(model_dir)
        try:
            real_model_path = os.path.realpath(model_path)
        except OSError:
            real_model_path = model_path
        if real_model_path != model_path:
            _mount_dir(str(Path(real_model_path).parent))
        # Bind-mount /etc/hal0 so flags referencing absolute config paths
        # (--chat-template-file etc.) resolve at the same path inside.
        config_root = "/etc/hal0"
        if Path(config_root).is_dir():
            mounts.append((config_root, config_root))

        # Pass HF_HOME through if set on the host.
        container_env: dict[str, str] = {}
        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            container_env["HF_HOME"] = hf_home

        # ``group_add`` must be **numeric GIDs**, not names: the toolbox
        # image inherits ``ubuntu:24.04``'s /etc/group, which doesn't
        # define ``render``/``video``.  Passing names there fails fast
        # with "unable to find group render".  Resolve from the host's
        # /etc/group at render time so distros with non-standard GIDs
        # still work.
        group_add: list[str] = [str(gid) for gid in _resolve_gpu_group_ids()]

        # Only request devices that actually exist on the host. Strix
        # Halo exposes both /dev/kfd and /dev/dri; a CPU-only dev VM
        # (or one with virtio-gpu and no AMD compute) has neither — and
        # docker hard-fails on a missing --device. Filter at render time
        # so the dev VM falls through to a CPU-only llama-server.
        candidate_devices = ["/dev/kfd", "/dev/dri"]
        devices = [d for d in candidate_devices if Path(d).exists()]

        return ContainerSpec(
            image=self.image_ref(slot_cfg),
            command=command,
            env=container_env,
            mounts=mounts,
            devices=devices,
            cap_add=[],
            security_opt=["seccomp=unconfined", "apparmor=unconfined"],
            group_add=group_add,
            port=port,
            network_mode="host",
            extra_args=[],
        )

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Health probe: /v1/models (non-empty) + sentinel /v1/chat/completions.

        TIER1: PLAN.md §5 Tier 1 — health probe must require non-empty
        /v1/models PLUS a /v1/chat/completions with max_tokens=1 before
        reporting ready. Bare /health and "models endpoint returns 200"
        both lie when the model failed to load.
        """
        models_url = f"http://127.0.0.1:{port}/v1/models"
        chat_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                # 1. /v1/models must return non-empty list.
                models_resp = await client.get(models_url)
                models_resp.raise_for_status()
                models_data = models_resp.json()
                models = models_data.get("data", [])
                if not models:
                    return {
                        "ok": False,
                        "status": "models_endpoint_empty",
                        "detail": "/v1/models returned no entries",
                    }
                model_id = models[0].get("id")

                # 2. Sentinel chat completion with max_tokens=1.  # TIER1
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
                    }
                # Best-effort: if the body parses and has at least one choice, good.
                try:
                    body = chat_resp.json()
                    if not body.get("choices"):
                        return {
                            "ok": False,
                            "status": "sentinel_completion_no_choices",
                        }
                except Exception:
                    return {"ok": False, "status": "sentinel_completion_unparseable"}
                return {"ok": True, "status": "ready", "model": model_id}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            # TIER1: do not silently swallow — return typed status
            # but keep the call non-raising so SlotManager can decide.
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough /v1/chat/completions to llama-server."""
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderInferError(
                f"llama-server returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderInferError(
                f"llama-server transport error: {exc}",
                details={"port": port},
            ) from exc

    # ── Metrics (optional helper, kept from haloai) ────────────────────────────

    async def parse_metrics(self, raw_text: str) -> dict[str, Any]:
        """Parse llama.cpp /metrics Prometheus text into a flat dict.

        Whitelisted counters/gauges only.  Lines starting with '#' are
        HELP/TYPE comments and are skipped.
        """
        wanted: dict[str, tuple[str, Any]] = {
            "llamacpp:n_decode_total": ("decode_total", int),
            "llamacpp:n_prompt_tokens_total": ("prompt_tokens_total", int),
            "llamacpp:kv_cache_usage_ratio": ("kv_cache_usage", float),
            "llamacpp:requests_processing": ("requests_processing", int),
            "llamacpp:requests_deferred": ("requests_deferred", int),
        }
        out: dict[str, Any] = {}
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            metric, raw_val = parts[0], parts[1]
            entry = wanted.get(metric)
            if entry is None:
                continue
            key, caster = entry
            try:
                out[key] = caster(float(raw_val)) if caster is int else caster(raw_val)
            except (ValueError, TypeError):
                continue
        return out
