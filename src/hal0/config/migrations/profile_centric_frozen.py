"""What a slot launches before the profile-centric conversion (ADR-0029).

A frozen, side-effect-free copy of the launch resolution that the conversion
replaces: ``providers.container._resolve_llama_scalars`` +
``_llama_argv_segments`` + ``slots.argv.resolve_argv`` +
``_resolve_context_size``, the profile catalog lookup
(``ProfileCatalog.resolve`` with its seed overlay and demoted-seed adoption),
the profile fit predicate (``slots.profile_adopt.resolved_profile_fits_slot``)
and the provider dispatch (``container._provider_for_family``), as they stood
at ADR-0029.

The converter (:mod:`hal0.config.migrations.profile_centric`) asks it one
question per slot: *what does this slot launch today?* It has to keep giving
the same answer after the release that ships ADR-0029 rewrites the real
resolver, so it imports nothing that release changes. Rules and data it
depends on are copied here (the seed profiles are in
``config/data/profile_centric_seeds_before.toml``); only primitives that keep
their meaning are imported: the argv tokenizer and last-wins dedup
(``slots.argv``), the runner registry, ``model_meta.device_to_backend``, the
curated catalogue's native context and the slot type heal.

Pure: it reads nothing from disk except the frozen seed file. Callers pass the
raw slot dict, the model row and the raw ``profiles.toml`` dict. It is deleted
together with the converter.
"""

from __future__ import annotations

import json
import shlex
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Any

from hal0.errors import BadRequest

# ── frozen constants ─────────────────────────────────────────────────────────

#: ``slots.argv.MANAGED_ARGS_DENYLIST`` at ADR-0029.
MANAGED_FLAGS: frozenset[str] = frozenset(
    {"--model", "--ctx-size", "--host", "--port", "--n-gpu-layers", "--alias"}
)
#: ``slots.argv.SLOT_HARDWARE_FLAGS`` at ADR-0029 (both spellings).
HARDWARE_FLAGS: frozenset[str] = frozenset(
    {"--n-gpu-layers", "-ngl", "--device", "-dev", "--threads", "-t"}
)
#: Segments whose free text is screened against :data:`MANAGED_FLAGS`.
UNTRUSTED_SEGMENTS: frozenset[str] = frozenset(
    {"extra_args", "model_extra_args", "slot_profile", "slot_profile_template"}
)
#: Profile-sourced segments: hardware flags are silently stripped first.
PROFILE_SEGMENTS: frozenset[str] = frozenset({"slot_profile", "slot_profile_template"})

CTX_SAFE_FALLBACK = 8192
CTX_DENSE_CAP = 32768
CTX_MIN_PLAUSIBLE = 128
CTX_MAX_PLAUSIBLE = 2**24

#: ``config.schema._MTP_DRAFT_DEVICE`` and the bundle ``build_mtp_flag_bundle``
#: rendered at ADR-0029. The draft device came from the profile's ``backend``
#: hint, which no seed set, so it was ``ROCm0`` on every lane.
_MTP_DRAFT_DEVICE: dict[str, str] = {"rocm": "ROCm0", "vulkan": "Vulkan0", "cuda": "CUDA0"}
_MTP_BUNDLE_TAIL = (
    " --spec-draft-ngl all"
    " --spec-draft-n-max 4"
    " --spec-draft-n-min 0"
    " --spec-draft-p-min 0.0"
    " --spec-draft-p-split 0.10"
    " --spec-draft-type-k q8_0"
    " --spec-draft-type-v q8_0"
    " --spec-draft-threads 16"
    " --spec-draft-threads-batch 32"
    " --spec-draft-poll 1"
    " --spec-draft-poll-batch 1"
)

#: ``config/data/family_defaults.toml`` shipped empty at ADR-0029.
FAMILY_FLAGS: dict[str, str] = {}

#: The engines a slot can dispatch to (``container._provider_for_family``).
LLAMA = "llama-server"


def mtp_bundle(backend: str | None) -> str:
    """The MTP draft bundle exactly as ``build_mtp_flag_bundle`` rendered it."""
    device = _MTP_DRAFT_DEVICE.get((backend or "").lower(), "ROCm0")
    return f"--spec-type draft-mtp --spec-draft-device {device}{_MTP_BUNDLE_TAIL}"


# ── frozen seed data ─────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def frozen_seeds() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """``(seed profiles, demoted seed profiles)`` as shipped at ADR-0029."""
    text = (
        files("hal0.config.data")
        .joinpath("profile_centric_seeds_before.toml")
        .read_text(encoding="utf-8")
    )
    raw = tomllib.loads(text)
    return dict(raw.get("seed", {})), dict(raw.get("demoted", {}))


# ── raw-row normalisation ────────────────────────────────────────────────────


def hoist_slot(raw: Mapping[str, Any], stem: str) -> dict[str, Any]:
    """A slot TOML in the shape ``SlotManager._load_slot_config`` hands the
    launch path: a ``[slot]`` table hoisted to the top, sibling tables left in
    place, ``name`` defaulted from the file stem, a type-less llm slot healed."""
    from hal0.slots._cfg_helpers import heal_missing_llm_type

    data = json.loads(json.dumps(dict(raw)))  # deep copy of plain TOML data
    slot_tbl = data.pop("slot", None)
    if isinstance(slot_tbl, dict):
        data.update(slot_tbl)
    data.setdefault("name", stem)
    heal_missing_llm_type(data)
    return data


def model_info_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """The ``model_info`` dict the launch path built from one ``model`` row
    (``db.repository.row_to_model(...).model_dump()``), reduced to the keys
    the launch resolution reads."""
    extra: dict[str, Any] = {}
    raw_extra = row.get("extra")
    if raw_extra:
        try:
            loaded = json.loads(raw_extra)
            if isinstance(loaded, dict):
                extra = loaded
        except ValueError:
            extra = {}
    metadata = {k: v for k, v in extra.items() if not (isinstance(k, str) and k.startswith("_"))}
    if row.get("context_length") is not None:
        metadata["context_length"] = row["context_length"]

    def _tri(v: Any) -> bool | None:
        return None if v is None else bool(v)

    def _text(v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    defaults = {
        "profile": _text(row.get("profile")),
        "extra_args": _text(row.get("extra_args")),
        "chat_template": _text(row.get("chat_template")),
        "context_size": row.get("context_size"),
        "rope_freq_base": row.get("rope_freq_base"),
        "mtp": _tri(row.get("mtp")),
        "jinja": _tri(row.get("jinja")),
        "enable_thinking": extra.get("_enable_thinking")
        if isinstance(extra.get("_enable_thinking"), bool)
        else None,
        "vision": extra.get("_vision") if isinstance(extra.get("_vision"), bool) else None,
    }
    has_defaults = any(v is not None for v in defaults.values())
    tags_raw = row.get("tags")
    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except ValueError:
        tags = []
    return {
        "_model_key": row.get("id"),
        "id": row.get("id"),
        "path": row.get("path"),
        "mmproj": row.get("mmproj"),
        "architecture": row.get("architecture"),
        "tags": tags if isinstance(tags, list) else [],
        "metadata": metadata,
        "defaults": defaults if has_defaults else None,
    }


# ── the catalog as the launch path saw it ────────────────────────────────────


@dataclass(frozen=True)
class Profile:
    """One resolved profile definition (the fields the launch path read)."""

    name: str
    flags: str = ""
    runner: str | None = None
    device_class: str | None = None
    backend: str | None = None
    seed: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)


class Catalog:
    """``ProfileCatalog.resolve`` over a raw ``profiles.toml`` dict.

    ``raw`` is the parsed file, or ``None`` when there is no file (a fresh
    install). Seeds always win over a same-named disk entry; managed flags are
    stripped from custom profiles (``loader._sanitize_custom_profile_flags``);
    a demoted seed resolves when the file's adoption ledger has not settled it.
    """

    def __init__(self, raw: Mapping[str, Any] | None) -> None:
        seeds, demoted = frozen_seeds()
        self.seeds = seeds
        self.demoted = demoted
        self.file_present = raw is not None
        data: Mapping[str, Any] = raw or {}
        entries = data.get("profile")
        self.custom: dict[str, dict[str, Any]] = {
            str(k): dict(v)
            for k, v in (entries.items() if isinstance(entries, Mapping) else ())
            if isinstance(v, Mapping) and str(k) not in seeds
        }
        # ``ProfilesConfig.adopted_legacy_names``: the ledger when present, else
        # every demoted name once the bulk demotion marker is set. No file at
        # all is a fresh install: nothing settled yet.
        ledger = data.get("legacy_seeds_adopted")
        self.settled: frozenset[str]
        if not self.file_present:
            self.settled = frozenset()
        elif isinstance(ledger, list):
            self.settled = frozenset(str(x) for x in ledger)
        elif data.get("legacy_seeds_migrated"):
            self.settled = frozenset(demoted)
        else:
            self.settled = frozenset()

    def names(self) -> set[str]:
        return set(self.seeds) | set(self.custom)

    def resolve(self, name: str) -> Profile | None:
        """The profile ``name`` resolves to, or ``None`` (``profiles.not_found``)."""
        if not name:
            return None
        if name in self.seeds:
            return _profile(name, self.seeds[name], seed=True)
        if name in self.custom:
            entry = dict(self.custom[name])
            entry["flags"] = _strip_managed_text(str(entry.get("flags") or ""))
            return _profile(name, entry, seed=False)
        if name in self.demoted and name not in self.settled:
            return _profile(name, self.demoted[name], seed=False)
        return None

    def resolve_or_base(self, name: str, slot: Mapping[str, Any]) -> tuple[Profile, bool]:
        """``container._resolve_profile_or_base``: ``(profile, fell_back)``.

        ``fell_back`` is True only for a NON-empty name the catalog does not
        know (an empty name falling back is the ordinary "no profile" case).
        """
        found = self.resolve(name)
        if found is not None:
            return found, False
        backend = str(slot.get("backend") or "")
        names = self.names()
        base = "chat" if "chat" in names else (backend if backend in names else "rocm")
        resolved = self.resolve(base)
        if resolved is None:
            raise BadRequest(
                f"profile {name!r} not found and no base profile to fall back to",
                code="profiles.not_found",
                details={"profile": name},
            )
        return resolved, bool(name)


def _profile(name: str, entry: Mapping[str, Any], *, seed: bool) -> Profile:
    runner = entry.get("runner")
    return Profile(
        name=name,
        flags=str(entry.get("flags") or ""),
        runner=str(runner) if isinstance(runner, str) and runner.strip() else None,
        device_class=entry.get("device_class") or None,
        backend=entry.get("backend") or None,
        seed=seed,
        raw=dict(entry),
    )


def _strip_managed_text(flags: str) -> str:
    if not flags.strip():
        return flags
    try:
        tokens = shlex.split(flags)
    except ValueError:
        return flags
    clean, removed = _strip(tokens, MANAGED_FLAGS)
    return " ".join(shlex.quote(t) for t in clean) if removed else flags


def runtime_family(profile: Profile) -> str:
    """``profiles._runtime_family`` at ADR-0029."""
    if profile.runner:
        from hal0.runners import RUNNER_IMAGES

        runner = RUNNER_IMAGES.get(profile.runner)
        if runner is not None:
            return str(runner.runtime_family)
    name = profile.name
    if name == "flm" or profile.device_class == "npu":
        return "flm"
    if name == "qwen3-tts":
        return "qwen3tts"
    if name == "kokoro":
        return "kokoro"
    if name == "moonshine":
        return "moonshine"
    if name == "comfyui" or profile.device_class == "img":
        return "comfyui"
    return LLAMA


def supported_slot_types(family: str) -> tuple[str, ...]:
    if family == "flm":
        return ("llm", "embedding", "transcription")
    if family in ("kokoro", "qwen3tts"):
        return ("tts",)
    if family == "moonshine":
        return ("transcription",)
    if family == "comfyui":
        return ("image",)
    return ("llm", "embedding", "reranking")


def profile_fits_slot(profile: Profile, slot: Mapping[str, Any]) -> bool:
    """``slots.profile_adopt.resolved_profile_fits_slot`` at ADR-0029."""
    slot_type = slot.get("type")
    if slot_type and slot_type not in supported_slot_types(runtime_family(profile)):
        return False
    device = str(slot.get("device") or "")
    if device:
        slot_class = (
            "gpu"
            if device.startswith("gpu")
            else device
            if device in ("npu", "cpu", "img")
            else "cpu"
        )
        if profile.device_class is not None and profile.device_class != slot_class:
            return False
        if profile.runner:
            from hal0.runners import RUNNER_IMAGES

            runner = RUNNER_IMAGES.get(profile.runner)
            if runner is None or runner.device_class != slot_class:
                return False
        if profile.backend:
            from hal0.model_meta import device_to_backend

            slot_backend = device_to_backend(device)[1]
            if slot_backend and slot_backend != profile.backend and not profile.runner:
                return False
    return True


def engine_for(slot: Mapping[str, Any], catalog: Catalog) -> str:
    """The engine a slot dispatches to (``container._provider_for_family``)."""
    name = str(slot.get("profile") or "")
    resolved = catalog.resolve(name) if name else None
    family = runtime_family(resolved) if resolved is not None else None
    device = str(slot.get("device", ""))
    slot_type = str(slot.get("type", ""))
    provider = str(slot.get("provider", ""))
    if family == "flm" or device == "npu":
        return "flm"
    if family == "qwen3tts":
        return "qwen3tts"
    if family == "kokoro" or slot_type == "tts":
        return "kokoro"
    if family == "moonshine" or slot_type == "transcription":
        return "moonshine"
    if family == "comfyui" or provider == "comfyui" or slot_type == "image":
        return "comfyui"
    return LLAMA


# ── the non-llama engines ────────────────────────────────────────────────────

#: Profile a provider resolved when the slot named none (``_DEFAULT_PROFILE``
#: in providers/kokoro.py, moonshine.py, qwen3tts.py). Kokoro's ``tts`` is not
#: a seed, so a profile-less Kokoro slot failed to launch.
ENGINE_DEFAULT_PROFILE: dict[str, str] = {
    "kokoro": "tts",
    "moonshine": "moonshine",
    "qwen3tts": "qwen3-tts",
}
#: ``providers/comfyui.py`` ``_DEFAULT_PROFILE_FLAGS``.
COMFYUI_DEFAULT_FLAGS = "--disable-mmap --bf16-vae --cache-none"
#: ``providers/flm.py`` ``build_env`` default window.
FLM_DEFAULT_CTX = 8192


def engine_flags(slot: Mapping[str, Any], catalog: Catalog, engine: str) -> str | None:
    """The profile flag text a non-llama provider launched with.

    ``None`` when the provider could not resolve a profile (the launch
    failed). FLM reads no profile flags (``""``).
    """
    if engine == "flm":
        return ""
    name = str(slot.get("profile") or "")
    if engine == "comfyui":
        resolved = catalog.resolve(name) if name else None
        flags = resolved.flags.strip() if resolved is not None else ""
        return flags or COMFYUI_DEFAULT_FLAGS
    resolved = catalog.resolve(name or ENGINE_DEFAULT_PROFILE.get(engine, ""))
    return resolved.flags if resolved is not None else None


def flm_ctx(slot: Mapping[str, Any]) -> str:
    """``providers.flm.FLMProvider.build_env``'s ``HAL0_FLM_CTX``."""
    model = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
    old = slot.get("defaults") if isinstance(slot.get("defaults"), Mapping) else {}
    ctx = (
        model.get("context_size")
        or slot.get("ctx_size")
        or old.get("context_size", FLM_DEFAULT_CTX)
    )
    return str(ctx)


# ── runner, MTP, context ─────────────────────────────────────────────────────


def _backend_and_class(slot: Mapping[str, Any], profile: Profile) -> tuple[str | None, str | None]:
    device = slot.get("device")
    if isinstance(device, str) and device:
        from hal0.model_meta import device_to_backend

        recipe, backend = device_to_backend(device)
        device_class = "npu" if recipe == "flm" else ("cpu" if device == "cpu" else "gpu")
        return backend, device_class
    sb = slot.get("backend")
    if isinstance(sb, str) and sb:
        return sb, profile.device_class
    return profile.backend, profile.device_class


def effective_runner(slot: Mapping[str, Any], profile: Profile) -> Any:
    """``container._effective_runner``: the slot's ``binary``, else the lane default."""
    from hal0.runners import RUNNER_IMAGES, runner_for_backend

    binary = slot.get("binary")
    if isinstance(binary, str) and binary and binary in RUNNER_IMAGES:
        return RUNNER_IMAGES[binary]
    backend, device_class = _backend_and_class(slot, profile)
    return runner_for_backend(backend, device_class)


def model_mtp(model: Mapping[str, Any], runner: Any) -> bool:
    """``container._effective_mtp`` (explicit ``defaults.mtp`` wins; else tag AND runner)."""
    defaults = model.get("defaults")
    explicit = defaults.get("mtp") if isinstance(defaults, Mapping) else None
    if explicit is not None:
        return bool(explicit)
    tags = model.get("tags") or ()
    eligible = isinstance(tags, (list, tuple, set)) and any(
        str(t).strip().lower() == "mtp" for t in tags
    )
    return eligible and bool(getattr(getattr(runner, "supports", None), "mtp", False))


def native_ctx(model: Mapping[str, Any]) -> int | None:
    """``container._native_ctx``: the row's context_length, else the curated one."""
    md = model.get("metadata")
    if isinstance(md, Mapping) and md.get("context_length"):
        try:
            return int(md["context_length"])
        except (TypeError, ValueError):
            pass
    model_id = model.get("_model_key") or model.get("id")
    if model_id:
        try:
            from hal0.registry.curated import get_curated

            curated = get_curated(str(model_id))
        except Exception:
            curated = None
        if curated is not None and curated.context_length:
            return int(curated.context_length)
    return None


def _plausible(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if CTX_MIN_PLAUSIBLE <= value <= CTX_MAX_PLAUSIBLE else None


def declared_ctx(model: Mapping[str, Any]) -> int | None:
    defaults = model.get("defaults")
    if not isinstance(defaults, Mapping) or not defaults.get("context_size"):
        return None
    return _plausible(defaults["context_size"])


def resolve_ctx(ceiling_raw: Any, model: Mapping[str, Any]) -> tuple[int, str]:
    """``container._resolve_context_size``: ``(window, source)``.

    ``source`` is ``model`` (explicit ``defaults.context_size``), ``native``
    (dense-capped GGUF window), ``fallback`` (8192), or ``ceiling`` when the
    slot's ``[model].context_size`` decided the answer.
    """
    ceiling = _plausible(ceiling_raw)
    declared = declared_ctx(model)
    if declared is not None:
        resolved, source = declared, "model"
    else:
        native = native_ctx(model)
        if native:
            cap = CTX_DENSE_CAP if ceiling is None else max(CTX_DENSE_CAP, ceiling)
            resolved, source = min(native, cap), "native"
            if ceiling is not None and ceiling > CTX_DENSE_CAP and resolved > CTX_DENSE_CAP:
                source = "ceiling"
        else:
            resolved, source = CTX_SAFE_FALLBACK, "fallback"
    if ceiling is not None and ceiling < resolved:
        return ceiling, "ceiling"
    return resolved, source


# ── argv merge (slots.argv.resolve_argv at ADR-0029) ─────────────────────────


def _strip(tokens: list[str], denylist: frozenset[str]) -> tuple[list[str], list[str]]:
    from hal0.slots.argv import _split_pairs

    clean: list[str] = []
    removed: list[str] = []
    for pair in _split_pairs(tokens):
        if pair.canon is not None and pair.canon in denylist:
            removed.append(str(pair.flag))
            continue
        if pair.flag is not None:
            clean.append(pair.flag)
        clean.extend(pair.values)
    return clean, removed


def _strip_reasoning_format_none(tokens: list[str]) -> list[str]:
    from hal0.slots.argv import _split_pairs

    clean: list[str] = []
    for pair in _split_pairs(tokens):
        if pair.canon == "--reasoning-format" and pair.flag is not None:
            joined = pair.flag.startswith("--reasoning-format=")
            value = pair.flag.split("=", 1)[1] if joined else (pair.values or ("",))[0]
            if value.strip().lower() == "none":
                if joined:
                    clean.extend(pair.values)
                continue
        if pair.flag is not None:
            clean.append(pair.flag)
        clean.extend(pair.values)
    return clean


def merge_segments(segments: list[tuple[str, list[str]]]) -> list[str]:
    """Screen, strip and last-wins-dedup labelled segments into one argv."""
    return merge_segments_with_sources(segments)[0]


def merge_segments_with_sources(
    segments: list[tuple[str, list[str]]],
) -> tuple[list[str], dict[str, str]]:
    """:func:`merge_segments` plus, per surviving flag, the segment that set it."""
    from hal0.slots.argv import _dedup, _split_pairs

    tokens: list[str] = []
    sources: list[str] = []
    for label, seg in segments:
        if label in PROFILE_SEGMENTS:
            seg, _ = _strip(seg, HARDWARE_FLAGS)
        if label in UNTRUSTED_SEGMENTS:
            _, offenders = _strip(seg, MANAGED_FLAGS)
            if offenders:
                raise BadRequest(
                    f"{label} may not set managed flag(s) {offenders}",
                    code="slot.managed_arg_denied",
                    details={"segment": label, "flags": offenders},
                )
        clean = _strip_reasoning_format_none(seg)
        tokens.extend(clean)
        sources.extend([label] * len(clean))
    out, _removed, winners = _dedup(_split_pairs(tokens, sources))
    return out, {canon: pair.source for canon, pair in winners.items()}


# ── the evaluation ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LaunchToday:
    """What one llama-server slot launches before the conversion."""

    argv: tuple[str, ...]
    segments: tuple[tuple[str, tuple[str, ...]], ...]
    #: Surviving flag (canonical spelling) -> the segment that set it.
    sources: Mapping[str, str]
    profile_named: str
    profile_launched: str
    profile_missing: bool
    ctx: int
    ctx_source: str
    mtp: bool
    mtp_backend: str | None
    jinja: bool
    chat_template: str | None
    mmproj: str | None
    enable_thinking: bool | None
    overlay_applied: bool
    template_applied: bool

    @property
    def vision_loaded(self) -> bool:
        """Whether llama.cpp actually loaded the projector.

        ``common_params_handle_models`` clears ``mmproj`` whenever
        ``--no-mmproj`` (alias ``--no-mmproj-auto``) is set, whatever the
        order, so an emitted ``--mmproj`` is not enough.
        """
        return bool(self.mmproj) and not ({"--no-mmproj", "--no-mmproj-auto"} & set(self.argv))


def _flags_tokens(flags: str) -> list[str]:
    """``container._profile_flag_tokens``: shlex-split, ``--jinja`` dropped."""
    if not flags or not flags.strip():
        return []
    return [t for t in shlex.split(flags) if t != "--jinja"]


def evaluate(
    slot: Mapping[str, Any],
    model: Mapping[str, Any],
    catalog: Catalog,
    *,
    chat_template_root: str = "",
) -> LaunchToday:
    """What ``slot`` (hoisted raw TOML) launches with ``model`` (see
    :func:`model_info_from_row`) on the llama-server engine.

    Raises :class:`~hal0.errors.BadRequest` when today's launch would refuse
    too (a managed flag in free text) and :class:`ValueError` for flag text
    that does not shell-split.
    """
    named = str(slot.get("profile") or "")
    profile, missing = catalog.resolve_or_base(named, slot)
    runner = effective_runner(slot, profile)
    mtp_on = model_mtp(model, runner)

    md = model.get("defaults") if isinstance(model.get("defaults"), Mapping) else None
    provenance = md.get("profile") if md else None
    md_extra = md.get("extra_args") if md else None
    has_tune = bool(md_extra and str(md_extra).strip())
    same_profile = profile.name == named

    overlay = ""
    if (
        named
        and isinstance(provenance, str)
        and provenance
        and named != provenance
        and same_profile
        and profile_fits_slot(profile, slot)
    ):
        overlay = profile.flags.strip()
    template = ""
    if (
        named
        and not has_tune
        and not (isinstance(provenance, str) and provenance)
        and same_profile
        and profile_fits_slot(profile, slot)
    ):
        template = profile.flags.strip()

    model_table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
    ctx, ctx_source = resolve_ctx(model_table.get("context_size"), model)
    model_alias = model.get("_model_key") or model_table.get("default") or None

    tmpl = md.get("chat_template") if md else None
    tmpl_id = str(tmpl) if tmpl and tmpl != "auto" else None
    chat_template_path = f"{chat_template_root}/chat-templates/{tmpl_id}.jinja" if tmpl_id else None

    mmproj = model.get("mmproj")
    if md and md.get("vision") is False:
        mmproj = None

    # Family flags (``config.schema.family_flags``) were prepended here; the
    # shipped table was empty at ADR-0029 (:data:`FAMILY_FLAGS`), so they
    # contributed nothing.
    model_extra = str(md_extra).strip() if has_tune else ""
    mode_tokens = set(shlex.split(model_extra)) if model_extra else set()
    embed_mode = bool(mode_tokens & {"--embedding", "--reranking"})
    jinja_on = (
        bool(getattr(getattr(runner, "supports", None), "jinja", False))
        and (md.get("jinja") if md else None) is not False
        and not embed_mode
    )
    if jinja_on:
        model_extra = f"{model_extra} --jinja".strip()
    if mtp_on and not embed_mode:
        model_extra = f"{mtp_bundle(profile.backend)} {model_extra}".strip()

    base: list[str] = ["--host", "0.0.0.0", "--port", str(int(slot.get("port") or 0))]
    model_path = str(model.get("path") or "")
    if model_path:
        base += ["--model", model_path]
    if model_alias:
        base += ["--alias", str(model_alias)]
    base += ["--ctx-size", str(ctx)]

    hw: list[str] = []
    if slot.get("n_gpu_layers") is not None:
        hw += ["-ngl", str(int(slot["n_gpu_layers"]))]
    threads = slot.get("threads")
    if threads is not None and int(threads) > 0:
        hw += ["--threads", str(int(threads))]
    metrics = slot.get("metrics")
    if metrics is None or bool(metrics):
        hw += ["--metrics"]

    segments: list[tuple[str, list[str]]] = [("base", base)]
    template_tokens = _flags_tokens(template)
    if template_tokens:
        segments.append(("slot_profile_template", template_tokens))
    segments.append(("model_extra_args", shlex.split(model_extra) if model_extra else []))
    overlay_tokens = _flags_tokens(overlay)
    if overlay_tokens:
        segments.append(("slot_profile", overlay_tokens))
    segments += [
        ("slot_hardware", hw),
        (
            "chat_template",
            ["--chat-template-file", chat_template_path] if chat_template_path else [],
        ),
        ("mmproj", ["--mmproj", str(mmproj)] if mmproj else []),
    ]
    argv, sources = merge_segments_with_sources(segments)
    thinking = md.get("enable_thinking") if md else None
    return LaunchToday(
        argv=tuple(argv),
        sources=dict(sources),
        segments=tuple((label, tuple(toks)) for label, toks in segments),
        profile_named=named,
        profile_launched=profile.name,
        profile_missing=missing,
        ctx=ctx,
        ctx_source=ctx_source,
        mtp=bool(mtp_on and not embed_mode),
        mtp_backend=profile.backend,
        jinja=jinja_on,
        chat_template=tmpl_id,
        mmproj=str(mmproj) if mmproj else None,
        enable_thinking=thinking if isinstance(thinking, bool) else None,
        overlay_applied=bool(overlay_tokens),
        template_applied=bool(template_tokens),
    )


__all__ = [
    "CTX_DENSE_CAP",
    "CTX_SAFE_FALLBACK",
    "LLAMA",
    "Catalog",
    "LaunchToday",
    "Profile",
    "effective_runner",
    "engine_flags",
    "engine_for",
    "evaluate",
    "flm_ctx",
    "frozen_seeds",
    "hoist_slot",
    "merge_segments",
    "merge_segments_with_sources",
    "model_info_from_row",
    "mtp_bundle",
    "native_ctx",
    "profile_fits_slot",
    "resolve_ctx",
    "runtime_family",
]
