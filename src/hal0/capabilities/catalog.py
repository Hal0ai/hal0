"""Catalog: available models per capability + available backends.

Two surfaces:

  - :func:`models_for_capability` reads :mod:`hal0.registry.curated`
    (and any registered :class:`~hal0.registry.store.ModelRegistry`
    entries) to produce a per-(backend, model) row for the dashboard
    picker. The same model id may appear once per backend it can run on
    — the picker uses ``backend`` as the secondary key.

  - :func:`available_backends` derives ``[npu (if XDNA), gpu-vulkan,
    gpu-rocm, cpu]`` from the cached hardware probe at
    ``/etc/hal0/hardware.json``.

Both helpers stay deliberately pure / synchronous — they're called from
``GET /api/capabilities`` on every load, so they must not hit the network
or invoke subprocesses. The registry + hardware probe layers already
cache the heavy work upstream.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hal0.config.loader import load_hardware_info
from hal0.config.schema import DEVICE_DEFAULT_PROFILES
from hal0.errors import Hal0Error
from hal0.model_fit import evaluate_model_fit
from hal0.profiles import ProfileCatalog, ResolvedProfile
from hal0.registry.curated import CURATED, CuratedModel, HaloaiModel
from hal0.registry.store import ModelRegistry

log = logging.getLogger(__name__)

# ── Capability → child mappings ───────────────────────────────────────────────

# Capability strings that may appear on a Model / CuratedModel mapped to
# the child slot they should populate. Multiple capability tags can map
# to the same child (e.g. "stt" and "asr" both go to voice.stt).
_CAPABILITY_TO_CHILD: dict[str, tuple[str, str]] = {
    "embed": ("embed", "embed"),
    "rerank": ("embed", "rerank"),
    "stt": ("voice", "stt"),
    "asr": ("voice", "stt"),
    "tts": ("voice", "tts"),
    "image": ("img", "img"),
}

# Provider hints derived from a model's ``backend`` tag — used so the
# catalog rows can pre-fill the provider dropdown alongside the backend
# dropdown in the picker UI.
_BACKEND_TO_PROVIDER: dict[str, str] = {
    "flm": "flm",
    "llamacpp": "llama-server",
    "llama-server": "llama-server",
    "kokoro": "kokoro",
    "moonshine": "moonshine",
    "whispercpp": "whispercpp",  # the whisper.cpp STT recipe
    "vibevoice": "kokoro",  # closest existing provider; the UI lets the user override
    "comfyui": "comfyui",
}

# Per-runtime allow-list of host backends the toolbox image can actually
# bind to. Picker fan-out (`_canonicalize_backends_for_picker`) uses this
# instead of a one-size-fits-all `(gpu-vulkan, cpu)` because Moonshine's
# upstream wheel only ships the ONNX CPU EP — letting an operator pick
# gpu-vulkan would write backend=vulkan into the slot TOML while the
# container still runs every op on CPU. Order matters: first entry that
# matches a host backend wins display order in the dropdown.
_RUNTIME_TO_HOST_BACKENDS: dict[str, tuple[str, ...]] = {
    "moonshine": ("cpu",),
    "whispercpp": ("gpu-vulkan", "cpu"),  # whisper.cpp supports Vulkan + CPU
    "kokoro": ("gpu-vulkan", "cpu"),
    "vibevoice": ("gpu-vulkan", "cpu"),
    "comfyui": ("gpu-vulkan",),
}

_CAPABILITY_TO_SLOT_TYPE: dict[str, str] = {
    "chat": "llm",
    "embed": "embedding",
    "rerank": "reranking",
    "stt": "transcription",
    "asr": "transcription",
    "tts": "tts",
    "image": "image",
    "vision": "llm",
}


# ── Backends ──────────────────────────────────────────────────────────────────


_FLM_TOOLBOX_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.43"


# FLM tags hidden from the dashboard catalog because of upstream FLM
# bugs we've reproduced end-to-end on the bundled toolbox image. The
# slot would still ``state=ready`` for these tags (the health probe
# passes), but a real ``/v1/chat/completions`` 500s with the recorded
# error, so surfacing them in the picker is a trap. Revisit after each
# toolbox-image bump: re-run ``tests/harness`` flm-chat-utf8 and shrink
# this set if upstream has fixed.
#
# Each entry: {tag: short reason}. The reason becomes the next reader's
# search term when checking whether to remove the entry.
_FLM_BROKEN_TAGS: dict[str, str] = {
    # 2026-05-21 (toolbox FLM v0.9.42, model_list.json pin
    # v0.9.22-faster-q4-1): any prompt that elicits non-ASCII output
    # returns ``[json.exception.type_error.316] invalid UTF-8 byte at
    # index 0: 0x..``. nlohmann/json is rejecting a content string that
    # starts mid-multibyte-UTF-8, so FLM's qwen3 0.6B decoder is emitting
    # partial token bytes. qwen3:1.7b and qwen3.5:* on the SAME tag
    # don't repro — bug is model-weight-specific, not tag-wide.
    "qwen3:0.6b": "FLM v0.9.42 emits invalid UTF-8 on non-ASCII output (see hal0_flm_chat_utf8_error)",
}


def _flm_image_present() -> bool:
    """True iff the FLM toolbox image is already pulled locally.

    Picking ``backend=npu`` rewrites the slot TOML and asks docker to
    spawn the FLM container. The image is gated on ghcr.io credentials
    that aren't part of the public install, so an unauthenticated host
    spirals into a ``docker pull → unauthorized → systemd restart``
    loop with no way for the user to recover from the dashboard.
    Advertising NPU as a backend only after we know docker can spawn
    the container avoids that whole class of failure.

    Checked via ``docker image inspect`` which returns 0 iff the image
    id resolves locally. We cache nothing — the toolchain install
    pulls the image once and the function is called only on the
    /api/capabilities GET which is already cheap.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", _FLM_TOOLBOX_IMAGE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def available_backends() -> list[dict[str, Any]]:
    """Return the list of backends this host can run, ordered.

    Order: NPU first (when XDNA is present AND the FLM toolbox image
    is locally available), then GPU/Vulkan (always when a GPU is
    detected), then GPU/ROCm (only when the GPU is ``compute_capable``),
    then CPU as a guaranteed-available fallback.

    Each entry carries the fields the dashboard footer renders:
    ``id`` (stable key the selection writer expects), ``label`` (long
    name), ``short`` (badge text), ``provider`` (default provider for
    this backend), and ``multiplex`` (true for NPU/FLM where one
    process serves many models).
    """
    out: list[dict[str, Any]] = []
    try:
        hw = load_hardware_info()
    except Exception:
        # Probe missing or unreadable — fall back to CPU-only so the UI
        # still renders a usable picker.
        hw = None

    npu_present = bool(hw and hw.npu and hw.npu.present)
    primary_gpu = hw.gpus[0] if hw and hw.gpus else None

    if npu_present and _flm_image_present():
        out.append(
            {
                "id": "npu",
                "label": "NPU",
                "short": "NPU",
                "provider": "flm",
                "multiplex": True,
            }
        )

    if primary_gpu is not None:
        # Vulkan path — every detected GPU we surface is assumed Vulkan
        # capable. (Probe defaults vulkan_capable=False for non-AMD,
        # but every modern Linux GPU has Mesa Vulkan.)
        if primary_gpu.vulkan_capable or primary_gpu.vendor in {"amd", "nvidia", "intel"}:
            out.append(
                {
                    "id": "gpu-vulkan",
                    "label": "GPU (Vulkan)",
                    "short": "GPU/Vk",
                    "provider": "llama-server",
                    "multiplex": False,
                }
            )
        # ROCm path — only AMD GPUs with compute support light this up.
        if primary_gpu.vendor == "amd" and primary_gpu.compute_capable:
            out.append(
                {
                    "id": "gpu-rocm",
                    "label": "GPU (ROCm)",
                    "short": "GPU/ROCm",
                    "provider": "llama-server",
                    "multiplex": False,
                }
            )

    # CPU is always reachable; provider stays llama-server (the same
    # binary, run with --n-gpu-layers 0).
    out.append(
        {
            "id": "cpu",
            "label": "CPU",
            "short": "CPU",
            "provider": "llama-server",
            "multiplex": False,
        }
    )
    return out


def get_backend(backend_id: str) -> dict[str, Any] | None:
    """Return one backend descriptor by id, or ``None`` if not present."""
    for b in available_backends():
        if b["id"] == backend_id:
            return b
    return None


# ── Models per capability ─────────────────────────────────────────────────────


def _model_capabilities(entry: CuratedModel | HaloaiModel | Any) -> list[str]:
    """Best-effort capability extraction across the catalog shapes.

    Curated entries expose a single ``capability`` string; haloai-seeded
    entries do the same. Registry-loaded :class:`~hal0.registry.model.Model`
    rows carry a ``capabilities`` list. Either shape works.

    Vision (#515) is a secondary capability: the curated multimodal MoE
    primaries keep ``capability="chat"`` (they are still chat models) but
    advertise ``vision`` via their ``tags`` and ship an mmproj sidecar.
    Surfacing ``vision`` from the ``tags`` lets the same entry appear in
    both the chat slot and the dedicated vision capability dropdown
    without a brand-new model download.
    """
    caps: list[str] = []
    cap = getattr(entry, "capability", None)
    if isinstance(cap, str) and cap:
        caps.append(cap)
    cap_list = getattr(entry, "capabilities", None)
    if isinstance(cap_list, list):
        for c in cap_list:
            if isinstance(c, str) and c and c not in caps:
                caps.append(c)
    tags = getattr(entry, "tags", None)
    if isinstance(tags, list) and "vision" in tags and "vision" not in caps:
        caps.append("vision")
    return caps


# Capabilities the AMD NPU (XDNA + FLM stack) can serve. Mirrors the
# ``flm`` capability set in src/hal0/registry/detect.py. Used to decide
# whether a llama.cpp-compatible entry should also fan out to ``npu``
# when the host has an NPU. ``stt`` is included as of NPU Phase 2: the
# FLM trio (one ``flm serve`` process) coresides ASR with chat/embed, so
# ``voice.stt`` can run on the NPU. TTS is NOT here — it routes through a
# dedicated provider (kokoro), not through FLM.
_NPU_FANOUT_CAPS: frozenset[str] = frozenset({"chat", "embed", "stt"})


def _backend_variants(entry: Any) -> list[str]:
    """Return the canonical backend ids this entry can run under.

    Curated and haloai entries carry a single ``backend`` string tag
    (e.g. ``"flm"`` or ``"llamacpp"``). Registry entries may carry a
    ``backends`` list. We map those to the stable backend ids used by
    :func:`available_backends`. Llama.cpp-compatible entries fan out
    across every GPU backend the host advertises (gpu-vulkan / gpu-rocm
    / cpu) — that's the picker's "this GGUF runs everywhere" UX. When
    the host has an NPU AND the entry serves a capability the NPU can
    handle (chat/embed per :mod:`hal0.registry.detect`), we also fan out
    to ``npu`` so the picker shows the NPU as an alternative — load-time
    will surface a clear error if the specific model doesn't have an
    FLM-packaged variant.
    """
    raw: list[str] = []
    backend = getattr(entry, "backend", None)
    if isinstance(backend, str) and backend:
        raw.append(backend)
    backend_list = getattr(entry, "backends", None)
    if isinstance(backend_list, list):
        for b in backend_list:
            if isinstance(b, str) and b and b not in raw:
                raw.append(b)

    # ── Defaults by entry shape ───────────────────────────────────────────
    # Curated image entries (the FirstRun image picks) carry no ``backend``
    # field — :class:`CuratedModel` has no such attribute. Treat the
    # presence of ``comfyui_subdir`` or ``capability == "image"`` as a
    # signal to route through ComfyUI, which fan-outs to gpu-vulkan + cpu
    # via the provider-runtime branch below.
    if not raw:
        comfy_subdir = getattr(entry, "comfyui_subdir", "") or ""
        cap_str = getattr(entry, "capability", "") or ""
        if comfy_subdir or cap_str == "image":
            raw.append("comfyui")

    out: list[str] = []
    for b in raw:
        low = b.lower()
        if low in {"flm", "npu"}:
            if "npu" not in out:
                out.append("npu")
        elif low in {"vulkan", "gpu-vulkan", "llamacpp", "llama-server"}:
            # Llama.cpp-compatible — fan out to every GPU/CPU backend
            # the host actually advertises so the picker shows what's
            # really runnable here.
            host_backends = {b["id"] for b in available_backends()}
            for candidate in ("gpu-vulkan", "gpu-rocm", "cpu"):
                if candidate in host_backends and candidate not in out:
                    out.append(candidate)
        elif low in {"rocm", "gpu-rocm"}:
            if "gpu-rocm" not in out:
                out.append("gpu-rocm")
        elif low in {"cpu"}:
            if "cpu" not in out:
                out.append("cpu")
        elif low in _RUNTIME_TO_HOST_BACKENDS:
            # Provider-specific runtimes only fan out to the host
            # backends their toolbox image can actually serve. Moonshine
            # ships with onnxruntime CPU EP only (no Vulkan/ROCm EP in
            # the upstream wheel), so advertising it on gpu-vulkan would
            # let the operator pick a backend the slot can't honour —
            # the slot TOML would say backend=vulkan but the container
            # would still pin every op to CPU.
            host_backends = {b["id"] for b in available_backends()}
            for candidate in _RUNTIME_TO_HOST_BACKENDS[low]:
                if candidate in host_backends and candidate not in out:
                    out.append(candidate)
        # Unknown backend strings fall through silently — they're
        # surfaced for debugging via the registry view, not here.
    return out


def _provider_for_backend(entry_backend: str, backend_id: str, *, entry: Any = None) -> str:
    """Pick the provider that pairs with this backend / entry combo.

    Resolution order:
      1. NPU backend → always FLM.
      2. The singular ``entry.backend`` tag (CuratedModel uses this).
      3. The ``entry.backends`` list (registry Model uses this; .backend
         is absent). Match the first tag that names a provider-specific
         runtime (moonshine, kokoro, comfyui, …) — skip llama-server,
         since that's also the generic default below.
      4. Fall through to llama-server for llama.cpp-compatible models.

    Step 3 used to be missing, which made every registry-derived row
    (moonshine, kokoro, vibevoice, …) advertise provider="llama-server"
    in the picker. The dashboard's onChange handler then sent that
    provider on every dropdown pick, overwriting the user's prior
    moonshine selection in capabilities.toml and the underlying slot
    TOML the next time they touched the card.
    """
    if backend_id == "npu":
        return "flm"
    if entry_backend in _BACKEND_TO_PROVIDER:
        return _BACKEND_TO_PROVIDER[entry_backend]
    if entry is not None:
        for b in getattr(entry, "backends", None) or []:
            if not isinstance(b, str):
                continue
            mapped = _BACKEND_TO_PROVIDER.get(b)
            if mapped and mapped != "llama-server":
                return mapped
    # Default for llama.cpp-compatible models.
    return "llama-server"


def _size_gb(entry: Any) -> float:
    """Return the model's on-disk size in GB, best effort."""
    size_gb = getattr(entry, "size_gb", None)
    if isinstance(size_gb, (int, float)) and size_gb > 0:
        return float(size_gb)
    size_bytes = getattr(entry, "size_bytes", None)
    if isinstance(size_bytes, (int, float)) and size_bytes > 0:
        return round(float(size_bytes) / (1024**3), 2)
    return 0.0


def _entry_to_row(
    entry: Any,
    backend_id: str,
    capabilities: list[str],
    *,
    registry: ModelRegistry | None = None,
) -> dict[str, Any]:
    """Project one (entry, backend) pair into a picker row.

    ``downloaded`` reflects whether the on-disk weights are actually
    present. For registry-derived entries we trust the stored path. For
    curated entries (CURATED + haloai seed) we look the id up in the
    registry — a curated row is "downloaded" iff a registry entry with
    that id has a path that exists on disk.

    ``pullable`` reflects whether ``POST /api/models/{id}/pull`` can
    actually fetch the file. CuratedModel entries (hand-curated Python
    list) carry hf_repo + hf_file and are pullable. HaloaiModel seed
    entries are routes into an existing upstream service — there's
    nothing to download, so the dashboard should not render a ⬇ chip
    against them and the handler should short-circuit before issuing
    the pull. Registry entries with hf_repo + hf_filename are pullable
    too (the user can re-pull a previously-pulled model).
    """
    raw_backend = getattr(entry, "backend", "") or ""
    return {
        "id": entry.id,
        "backend": backend_id,
        "provider": _provider_for_backend(raw_backend, backend_id, entry=entry),
        "size_gb": _size_gb(entry),
        "capabilities": capabilities,
        "downloaded": _is_downloaded(entry, registry=registry),
        "pullable": _is_pullable(entry, registry=registry),
    }


def _is_pullable(entry: Any, *, registry: ModelRegistry | None) -> bool:
    """True iff this entry has HF coordinates a pull job can use.

    Mirrors :func:`_resolve_pull_source` in routes/models.py — checks
    the entry itself first (curated CuratedModels carry hf_repo +
    hf_file) and falls back to the registry (user-added entries with
    HF coords). HaloaiModel seed rows never have hf_repo and are
    intentionally not pullable.
    """
    repo = (getattr(entry, "hf_repo", "") or "").strip()
    filename = (getattr(entry, "hf_file", "") or "").strip() or (
        getattr(entry, "hf_filename", "") or ""
    ).strip()
    if repo and filename:
        return True
    if registry is None:
        return False
    entry_id = getattr(entry, "id", "")
    if not entry_id:
        return False
    try:
        if not registry.has(entry_id):
            return False
        reg_entry = registry.get(entry_id)
    except Exception:
        return False
    reg_repo = (getattr(reg_entry, "hf_repo", "") or "").strip()
    reg_filename = (getattr(reg_entry, "hf_filename", "") or "").strip()
    return bool(reg_repo and reg_filename)


def _is_downloaded(entry: Any, *, registry: ModelRegistry | None) -> bool:
    """True iff this entry's weights exist on disk.

    Registry entries: ``entry.path`` is authoritative; check it exists.
    Curated entries: fall back to a registry lookup by id; treat the
    curated row as downloaded iff the registry has it AND the recorded
    path resolves on the host filesystem. We don't probe ``hf_repo`` /
    HuggingFace cache layouts directly — that's the registry's job
    (discover.py walks HF caches and registers them).
    """
    entry_path = getattr(entry, "path", None)
    if isinstance(entry_path, str) and entry_path:
        try:
            return Path(entry_path).exists()
        except OSError:
            return False
    # No path on the entry → it's a curated stub. Look it up in the
    # registry. If the registry isn't wired (older test paths), assume
    # not downloaded — better to show ⬇ on a real model than to claim
    # a missing model is ready.
    if registry is None:
        return False
    entry_id = getattr(entry, "id", "")
    if not entry_id:
        return False
    try:
        if not registry.has(entry_id):
            return False
        reg_entry = registry.get(entry_id)
    except Exception:
        return False
    reg_path = getattr(reg_entry, "path", None)
    if not isinstance(reg_path, str) or not reg_path:
        return False
    try:
        return Path(reg_path).exists()
    except OSError:
        return False


def _iter_registry_models(registry: ModelRegistry | None) -> list[Any]:
    """Return the registry's entries, or ``[]`` if no registry available."""
    if registry is None:
        return []
    try:
        return list(registry.list())
    except Exception:
        return []


def _flat_rows_for_capability(
    capability: str,
    *,
    registry: ModelRegistry | None = None,
) -> list[dict[str, Any]]:
    """Build per-(model, backend) flat rows for one capability child.

    Internal helper for :func:`models_for_capability`. Each compatible
    model emits one row per backend it can run on; FLM/NPU rows are
    appended after the curated / registry / llama-server fan-out.

    HaloaiModel entries (the upstream-routed seed in
    ``seeds/haloai_models.json``) are intentionally skipped here even
    though they're still part of :data:`CURATED`. They surface no
    download path and no working route on a standalone hal0 install, so
    listing them in the capability dropdowns just produced rows the
    user couldn't actually pick. They remain visible through
    ``/api/models/catalogue`` so the Models view's "upstream" tab and
    any future "wire up an upstream" UX still has them in reach.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # ``bundle_only`` entries are normally hidden from capability
    # dropdowns (they exist only to give bundle manifests a loadable id).
    # The ``vision`` capability (#515) is the exception: it deliberately
    # REUSES the curated multimodal MoE primaries (which are bundle_only
    # because they double as the chat/primary tier) rather than shipping a
    # new model. Let bundle_only entries through for vision so the dropdown
    # surfaces a real, already-curated multimodal pick.
    allow_bundle_only = capability == "vision"
    curated_only = [
        e
        for e in CURATED
        if not isinstance(e, HaloaiModel)
        and (allow_bundle_only or not getattr(e, "bundle_only", False))
    ]
    candidates: list[Any] = curated_only + _iter_registry_models(registry)

    for entry in candidates:
        caps = _model_capabilities(entry)
        if capability not in caps:
            continue
        for backend_id in _backend_variants(entry):
            key = (str(entry.id), backend_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(_entry_to_row(entry, backend_id, caps, registry=registry))

    for npu_row in _flm_rows_for_capability(capability):
        key = (npu_row["id"], npu_row["backend"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(npu_row)

    return rows


def _profile_for_fit(capability: str, device: str) -> ResolvedProfile | None:
    """Infer the profile implied by a picker backend.

    Mirrors CapabilityOrchestrator's conservative inference so picker and
    apply use the same model/profile/slot compatibility rules until the
    selection schema carries an explicit profile.
    """
    profile_name: str | None = None
    if device == "npu":
        profile_name = DEVICE_DEFAULT_PROFILES.get("npu")
    elif device in {"gpu-rocm", "gpu-vulkan"}:
        profile_name = DEVICE_DEFAULT_PROFILES.get(device)
    elif capability == "tts":
        profile_name = "tts"
    elif capability == "image":
        profile_name = "comfyui"
    if not profile_name:
        return None
    try:
        return ProfileCatalog().resolve(profile_name)
    except Hal0Error:
        log.warning(
            "capability.catalog.profile_fit_skipped profile=%s capability=%s device=%s",
            profile_name,
            capability,
            device,
        )
        return None


def _row_with_model_fit(
    capability: str,
    row: dict[str, Any],
    *,
    registry: ModelRegistry | None = None,
) -> dict[str, Any] | None:
    slot_type = _CAPABILITY_TO_SLOT_TYPE.get(capability)
    if slot_type is None:
        return row
    backend_id = str(row.get("backend") or "")
    profile = _profile_for_fit(capability, backend_id)
    registry_for_fit = None
    if registry is not None:
        try:
            if registry.has(str(row["id"])):
                registry_for_fit = registry
        except Exception:
            registry_for_fit = None
    fit = evaluate_model_fit(
        model_id=str(row["id"]),
        slot_type=slot_type,
        device=backend_id,
        profile=profile,
        registry=registry_for_fit,
        capabilities=row.get("capabilities"),
    )
    if not fit.allowed:
        return None
    fitted = dict(row)
    fitted["fit_status"] = fit.status
    fitted["fit_reasons"] = list(fit.reasons)
    if profile is not None:
        fitted["profile"] = profile.name
        fitted["runtime_family"] = profile.runtime_family
    return fitted


def models_for_capability(
    capability: str,
    *,
    registry: ModelRegistry | None = None,
) -> list[dict[str, Any]]:
    """Return picker entries for one capability child, grouped by model id.

    Each entry carries the model-level fields plus a ``backends`` list,
    one element per backend the model can actually run on::

        {
            "id":           "nomic-embed-text-v1.5-q8_0",
            "capabilities": ["embed"],
            "size_gb":      0.14,
            "backends": [
                {"id": "gpu-vulkan", "provider": "llama-server",
                 "downloaded": True, "pullable": True},
                {"id": "cpu",        "provider": "llama-server",
                 "downloaded": True, "pullable": True},
                {"id": "gpu-rocm",   "provider": "llama-server",
                 "downloaded": True, "pullable": True},
            ],
        }

    The model-first shape lets the dashboard offer the user a single
    model dropdown and narrow the backend dropdown to the picked model's
    legal options — replacing the old flat per-(model, backend) row
    layout that allowed the operator to mix incompatible pairs (e.g.
    ``backend=npu`` + an llama.cpp GGUF) which then crashed the slot at
    start-up.

    Backends preserve the order produced by :func:`_flat_rows_for_capability`
    (llama.cpp fan-out first, FLM/NPU appended).
    """
    flat = [
        fitted
        for row in _flat_rows_for_capability(capability, registry=registry)
        for fitted in [_row_with_model_fit(capability, row, registry=registry)]
        if fitted is not None
    ]
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in flat:
        rid = row["id"]
        entry = grouped.get(rid)
        if entry is None:
            entry = {
                "id": rid,
                "capabilities": list(row["capabilities"]),
                "size_gb": row["size_gb"],
                "backends": [],
            }
            grouped[rid] = entry
            order.append(rid)
        else:
            # Defensive: a provider could in theory tag the same model
            # differently per backend. Union rather than silently drop.
            for cap in row["capabilities"]:
                if cap not in entry["capabilities"]:
                    entry["capabilities"].append(cap)
        entry["backends"].append(
            {
                "id": row["backend"],
                "provider": row["provider"],
                "downloaded": row["downloaded"],
                "pullable": row["pullable"],
                "fit_status": row.get("fit_status", "allowed"),
                "fit_reasons": list(row.get("fit_reasons", [])),
                "profile": row.get("profile"),
                "runtime_family": row.get("runtime_family"),
            }
        )
    return [grouped[rid] for rid in order]


def _flm_rows_for_capability(capability: str) -> list[dict[str, Any]]:
    """Return picker rows the NPU/FLM toolbox can serve for one capability.

    Probes :func:`hal0.providers.flm.flm_served_models` (cached at module
    scope after first call) and projects each FLM tag into a picker row
    with ``backend="npu"`` / ``provider="flm"``. Scope is ``chat``,
    ``embed`` and ``stt`` (NPU Phase 2): the FLM trio coresides ASR with
    chat/embed in one ``flm serve`` process, so ``voice.stt`` runs on the
    NPU. ``tts`` is still out — it routes through kokoro, not FLM.

    ``pullable`` is True for FLM tags — the pull route (routes/models.py)
    detects FLM ids via :func:`hal0.providers.flm.is_flm_tag` and dispatches
    to :func:`hal0.registry.pull.run_flm_pull`, which shells ``flm pull
    <tag>`` inside the toolbox image with the same bind mount the slot
    uses. After a successful pull we reset the FLM probe cache so the
    next ``/api/capabilities`` GET flips ``downloaded`` to True without a
    process restart.
    """
    if capability not in _NPU_FANOUT_CAPS:
        return []
    # Local import so catalog.py doesn't drag the provider module (and
    # its httpx dependency) onto every import path.
    from hal0.providers.flm import flm_served_models

    out: list[dict[str, Any]] = []
    for entry in flm_served_models():
        if capability not in entry["capabilities"]:
            continue
        if entry["tag"] in _FLM_BROKEN_TAGS:
            continue
        # Filter capabilities reported to the dashboard down to the
        # in-scope subset; otherwise an "stt" tag would leak through on a
        # chat row and confuse the picker.
        reported_caps = [c for c in entry["capabilities"] if c in _NPU_FANOUT_CAPS]
        # FLM's reported `size` is the raw weights footprint; for
        # quantized models it under-reports actual disk usage, so prefer
        # the larger of size and runtime footprint as the displayed value.
        size_gb_from_bytes = (
            round(entry["size_bytes"] / (1024**3), 2) if entry["size_bytes"] else 0.0
        )
        size_gb = max(size_gb_from_bytes, entry["footprint_gb"])
        out.append(
            {
                "id": entry["tag"],
                "backend": "npu",
                "provider": "flm",
                "size_gb": size_gb,
                "capabilities": reported_caps,
                "downloaded": entry["installed"],
                "pullable": True,
            }
        )
    return out


def catalogs_by_slot(
    *, registry: ModelRegistry | None = None
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return ``{ slot: { child: [picker_rows] } }`` for the dashboard.

    Mirrors the capability layout the orchestrator hard-codes — embed has
    two children (embed, rerank), voice has two (stt, tts), img has one.

    The ``chat`` bucket is included so backend-card UIs (notably
    :file:`ui/src/components/capabilities/NPUBackendCard.vue`) can walk
    every ``(slot, capability)`` pair when listing NPU-capable models —
    without this entry chat-on-NPU models would be invisible to the
    "+ load NPU model" picker (chat lives in the dedicated ``primary``
    slot, not in a capability slot). Operator selection for chat still
    flows through the primary slot config, NOT through capability apply.
    """
    return {
        "embed": {
            "embed": models_for_capability("embed", registry=registry),
            "rerank": models_for_capability("rerank", registry=registry),
        },
        "voice": {
            "stt": models_for_capability("stt", registry=registry),
            "tts": models_for_capability("tts", registry=registry),
        },
        "img": {
            "img": models_for_capability("image", registry=registry),
        },
        "vision": {
            "vision": models_for_capability("vision", registry=registry),
        },
        "chat": {
            "chat": models_for_capability("chat", registry=registry),
        },
    }


__all__ = [
    "available_backends",
    "catalogs_by_slot",
    "get_backend",
    "models_for_capability",
]
