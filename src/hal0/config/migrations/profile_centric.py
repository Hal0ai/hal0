"""Plan the move of an install onto profile-centric config (ADR-0029).

Model rows keep file facts, profiles hold every tune, slots hold placement.
This module plans that move for one install. For EVERY slot it computes what
the slot launches today (:mod:`hal0.config.migrations.profile_centric_frozen`),
picks or generates the profile that holds that tune, computes what the slot
will launch under the new rules (:func:`new_launch`) and diffs the two. A
difference is either one of the few reported launch changes (a slot with no
or a missing profile getting the profile its type needs, the MTP draft
device following the slot's device, ``--jinja`` no longer added in
embedding/reranking mode, a forced MTP on a runner without MTP support) or a
reason to block that slot loudly.

Inert values (slot ``[server].extra_args``, ``parallel``, ``chat_template``, a
context ceiling that never took effect, stack-row tuning, non-llama model
``extra_args``) are never applied: the conversion keeps what launches and
reports every one of them (operator decision, ADR-0029).

This module only PLANS; it writes nothing. ``hal0 profile migrate`` prints
the plan.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import shlex
import sqlite3
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from hal0.config.migrations import profile_centric_frozen as frozen
from hal0.errors import Hal0Error

# ── constants of the conversion ──────────────────────────────────────────────

#: Top-level slot keys that leave the slot.
SLOT_DROP_KEYS: tuple[str, ...] = (
    "parallel",
    "chat_template",
    "workers",
    "runtime",
    "enabled",
    "backend",
    "ctx_size",
    "mtp",
    "enable_thinking",
    "vision",
    "image",
)
#: ``[model]`` keys that leave the slot (only ``default`` stays).
MODEL_TABLE_DROP: tuple[str, ...] = (
    "context_size",
    "ctx_size",
    "n_gpu_layers",
    "rope_freq_base",
    "extra",
)
#: Model row columns that hold tuning, and tuning keys in the row's ``extra``.
MODEL_TUNING_COLUMNS: tuple[str, ...] = (
    "profile",
    "extra_args",
    "context_size",
    "rope_freq_base",
    "n_gpu_layers",
    "preferred_runner",
)
MODEL_TUNING_EXTRA_KEYS: tuple[str, ...] = ("_enable_thinking", "_vision")
#: The table SQL migration 012 moves the model tuning columns into.
HOLDING_TABLE = "legacy_model_tuning"
#: Audit record under ``/var/lib/hal0`` of what the last conversion run did
#: (written by the apply step). It is a report and is never consulted: what
#: still needs converting is read from the content itself (:func:`profile_shape`,
#: :meth:`_Planner.slot_is_old`).
STATE_FILE = "profile-centric.json"
#: Profile keys that leave every profile.
PROFILE_DROP_KEYS: tuple[str, ...] = ("mtp", "device_class", "backend", "quant", "image")
#: Keys only an old-shape profile carries (as does a boolean ``mtp``).
OLD_PROFILE_KEYS: tuple[str, ...] = ("device_class", "backend", "quant", "image")
#: Keys only a new-shape profile carries (as does a string ``mtp``).
NEW_PROFILE_KEYS: tuple[str, ...] = ("ctx_size", "enable_thinking", "vision")
#: Stack-row keys that leave every row (inert today: stack apply never used them).
STACK_ROW_TUNING_KEYS: tuple[str, ...] = ("vision", "mtp", "enable_thinking", "server_extra_args")
#: Runner key a converted profile names for a non-llama engine.
ENGINE_RUNNER: dict[str, str] = {
    "flm": "flm",
    "kokoro": "kokoro",
    "moonshine": "moonshine",
    "qwen3tts": "qwen3tts",
    "comfyui": "comfyui",
}
#: Seed profile per non-llama engine (for a slot whose profile resolves to nothing).
ENGINE_SEED: dict[str, str] = {
    "flm": "flm",
    "kokoro": "kokoro",
    "moonshine": "moonshine",
    "qwen3tts": "qwen3-tts",
    "comfyui": "comfyui",
}
#: Slot type -> the seed that carries its mode flag.
TYPE_SEED: dict[str, str] = {"embedding": "embedding", "reranking": "reranking"}

#: The MTP draft defaults the new launch path adds in the profile layer; the
#: draft device is placement and comes from the slot.
MTP_DRAFT_BUNDLE = "--spec-type draft-mtp" + frozen._MTP_BUNDLE_TAIL
MTP_DRAFT_DEVICE: dict[str, str] = {"rocm": "ROCm0", "vulkan": "Vulkan0", "cuda": "CUDA0"}

_BASE = frozenset({"--host", "--port", "--model", "--alias", "--ctx-size"})
_VISION_OFF = frozenset({"--no-mmproj", "--no-mmproj-auto"})
#: Flags whose combined effect is "is the projector loaded" (llama.cpp clears
#: --mmproj whenever --no-mmproj is set, whatever the order).
_PROJECTOR_FLAGS = frozenset({"--mmproj", "--no-mmproj", "--no-mmproj-auto", "--mmproj-auto"})
#: Model-fact flags that have no profile home: a tune carrying one blocks its slot.
_BLOCKING = frozenset({"--chat-template", "--chat-template-file", "--mmproj"})
#: Segments of today's launch that hold the tune (everything else is base,
#: placement or a model fact hal0 computes).
_TUNE_SEGMENTS = frozenset({"slot_profile_template", "model_extra_args", "slot_profile"})
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ProfileMigrationError(Hal0Error):
    """The plan could not be built at all (an input that cannot be read)."""

    code = "profiles.migration_failed"


@lru_cache(maxsize=1)
def target_seeds() -> dict[str, dict[str, Any]]:
    """The seed catalog the conversion targets (new profile shape)."""
    text = (
        files("hal0.config.data")
        .joinpath("profile_centric_seeds_after.toml")
        .read_text(encoding="utf-8")
    )
    return {k: dict(v) for k, v in tomllib.loads(text).get("profile", {}).items()}


# ── plan data ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InertValue:
    """A configured value that does not reach the launch today (reported, dropped)."""

    where: str
    key: str
    value: Any


SlotStatus = Literal["unchanged", "convert", "blocked"]


@dataclass
class SlotPlan:
    file: str
    name: str
    engine: str = frozen.LLAMA
    status: SlotStatus = "unchanged"
    profile_before: str = ""
    profile_after: str = ""
    generated: bool = False
    drop_keys: list[str] = field(default_factory=list)
    set_keys: dict[str, Any] = field(default_factory=dict)
    launch_changes: list[str] = field(default_factory=list)
    inert: list[InertValue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    argv_today: tuple[str, ...] = ()
    argv_after: tuple[str, ...] = ()


@dataclass
class ModelPlan:
    model_id: str
    clear: dict[str, Any] = field(default_factory=dict)
    generated_profile: str | None = None
    register_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class StackPlan:
    stack: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    profile_rewrites: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class ConversionPlan:
    slots: list[SlotPlan] = field(default_factory=list)
    shadowed: list[str] = field(default_factory=list)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    profile_rewrites: dict[str, list[str]] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    models: list[ModelPlan] = field(default_factory=list)
    stacks: list[StackPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Stored profiles that cannot be converted as they are: name -> reasons.
    #: Reported (and counted as blocking) whether or not any slot uses them.
    blocked_profiles: dict[str, list[str]] = field(default_factory=dict)

    @property
    def blocked(self) -> list[SlotPlan]:
        return [s for s in self.slots if s.status == "blocked"]

    @property
    def blockers(self) -> list[str]:
        """Everything that needs an operator: blocked slots (by file), profiles
        (``profile:<name>``) and stacks (``stack:<name>``)."""
        out = [s.file for s in self.blocked]
        out += [f"profile:{name}" for name in sorted(self.blocked_profiles)]
        out += [f"stack:{st.stack}" for st in self.stacks if st.errors]
        return out

    @property
    def changed(self) -> bool:
        return bool(
            any(s.status != "unchanged" for s in self.slots)
            or self.profiles
            or self.profile_rewrites
            or self.renames
            or any(m.clear or m.register_path for m in self.models)
            or self.stacks
            or self.blocked_profiles
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["changed"] = self.changed
        out["blocked"] = self.blockers
        return out

    def lines(self) -> list[str]:
        """Human report, one line per finding."""
        out: list[str] = []
        for s in self.slots:
            head = f"slot {s.name} ({s.file})"
            if s.status == "unchanged":
                out.append(f"{head}: unchanged, profile {s.profile_before!r}")
            elif s.status == "blocked":
                out.append(f"{head}: BLOCKED: {s.error}")
            else:
                how = "generated" if s.generated else "existing"
                out.append(
                    f"{head}: profile {s.profile_before or '(none)'!r} -> "
                    f"{s.profile_after!r} ({how})"
                )
            if s.drop_keys and s.status != "blocked":
                out.append(f"  drop keys: {', '.join(s.drop_keys)}")
            for k, v in s.set_keys.items():
                out.append(f"  set {k} = {v!r}")
            for change in s.launch_changes:
                out.append(f"  LAUNCH CHANGE at next restart: {change}")
            for item in s.inert:
                out.append(f"  {item.where} {item.key} = {item.value!r}: inert today, dropped")
            out.extend(f"  note: {n}" for n in s.notes)
        out.extend(f"file {f}: not loaded today (shadowed); left as is" for f in self.shadowed)
        for old, new in self.renames.items():
            out.append(f"custom profile {old!r} renamed to {new!r} (a seed now has that name)")
        for name, content in self.profiles.items():
            out.append(f"new profile {name}: {json.dumps(content, sort_keys=True)}")
        for name, notes in self.profile_rewrites.items():
            out.append(f"profile {name}: rewrite ({'; '.join(notes)})")
        for name, reasons in sorted(self.blocked_profiles.items()):
            out.append(f"profile {name}: BLOCKED: {'; '.join(reasons)}")
        for m in self.models:
            if m.clear:
                out.append(
                    f"model {m.model_id}: clear tuning {json.dumps(m.clear, sort_keys=True)}"
                )
            if m.generated_profile:
                out.append(f"  unbound tuning kept as profile {m.generated_profile!r}")
            if m.register_path:
                out.append(f"model {m.model_id}: register (path {m.register_path})")
            out.extend(f"  note: {n}" for n in m.notes)
        for st in self.stacks:
            for row in st.rows:
                out.append(f"stack {st.stack} row {row['slot']}: {row['change']}")
            for name, notes in st.profile_rewrites.items():
                out.append(f"stack {st.stack} profile {name}: rewrite ({'; '.join(notes)})")
            out.extend(f"stack {st.stack}: BLOCKED: {e}" for e in st.errors)
        out.extend(f"warning: {w}" for w in self.warnings)
        if not self.changed:
            out.append("nothing to convert")
        return out


# ── flags ────────────────────────────────────────────────────────────────────


def _pairs(tokens: list[str] | tuple[str, ...]) -> list[tuple[str, str, tuple[str, ...]]]:
    from hal0.slots.argv import _split_pairs

    return [
        (str(p.canon), str(p.flag), tuple(p.values))
        for p in _split_pairs(list(tokens))
        if p.canon is not None
    ]


def effective_map(tokens: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Flag -> value as llama-server sees it (last wins; repeatable flags listed)."""
    from hal0.slots.argv import APPEND_FLAGS

    out: dict[str, Any] = {}
    for canon, _flag, values in _pairs(tokens):
        if canon in APPEND_FLAGS:
            out.setdefault(canon, []).append(values)
        else:
            out[canon] = values
    return out


def _join(tokens: list[str]) -> str:
    return " ".join(shlex.quote(t) for t in tokens)


@dataclass
class FlagConversion:
    """Free flag text under the new rules."""

    tokens: list[str] = field(default_factory=list)
    vision_off: bool = False
    threads: int | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def convert_flags(text: str | list[str], *, source: Literal["profile", "tune"]) -> FlagConversion:
    """Route every flag a profile may no longer carry to its new home.

    ``source="profile"``: a stored profile's text (hardware and managed flags
    were stripped from profile segments at launch today, so they are
    dropped here and reported). ``source="tune"``: the flags a slot launched
    with today (``--threads`` moves to the slot, ``-dev`` blocks).
    """
    out = FlagConversion()
    if isinstance(text, str):
        try:
            tokens = shlex.split(text) if text.strip() else []
        except ValueError as exc:
            out.errors.append(f"flags do not shell-split: {exc}")
            return out
    else:
        tokens = list(text)
    from hal0.slots.argv import _split_pairs

    for pair in _split_pairs(tokens):
        if pair.canon is None:
            out.tokens.extend(pair.values)
            continue
        canon, flag, values = pair.canon, str(pair.flag), list(pair.values)
        if canon == "--reasoning-format":
            joined = flag.startswith("--reasoning-format=")
            value = flag.split("=", 1)[1] if joined else (values or [""])[0]
            if value.strip().lower() == "none":
                out.notes.append(
                    "--reasoning-format none dropped (#2545; stripped at launch today)"
                )
                if joined:
                    out.tokens.extend(values)
                continue
        if canon == "--jinja":
            if source == "profile":
                out.notes.append("--jinja dropped (hal0 adds it from the runner and the model)")
            continue
        if canon in _VISION_OFF:
            out.vision_off = True
            out.notes.append(f'{flag} -> vision = "off"')
            continue
        if canon == "--mmproj-auto":
            out.notes.append('--mmproj-auto dropped (vision = "auto" is the default)')
            continue
        if canon in _BLOCKING:
            out.errors.append(f"{flag} is a model fact with no profile home; move it to the model")
            continue
        if canon == "--spec-draft-device":
            out.notes.append(f"{flag} {' '.join(values)} dropped (the slot's device decides it)")
            continue
        if canon in frozen.HARDWARE_FLAGS:
            if canon == "--threads" and source == "tune" and values:
                try:
                    out.threads = int(values[0])
                except ValueError:
                    out.errors.append(f"{flag} {values[0]} is not a thread count")
                continue
            if canon == "--device" and source == "tune":
                out.errors.append(f"{flag} reached the launch; the slot's device must say it")
                continue
            out.notes.append(f"{flag} dropped (placement belongs on the slot)")
            continue
        if canon in frozen.MANAGED_FLAGS:
            out.notes.append(f"{flag} dropped (hal0 sets it)")
            continue
        out.tokens.append(flag)
        out.tokens.extend(values)
    return out


# ── the new launch rules ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LaunchAfter:
    argv: tuple[str, ...]
    ctx: int
    mtp: bool
    jinja: bool
    vision_loaded: bool
    enable_thinking: bool | None


def default_ctx(model: Mapping[str, Any]) -> int:
    """The window for a profile with no ``ctx_size``: native capped, else 8192."""
    native = frozen.native_ctx(model)
    return min(native, frozen.CTX_DENSE_CAP) if native else frozen.CTX_SAFE_FALLBACK


def has_mtp_heads(model: Mapping[str, Any]) -> bool:
    """The model fact: explicit ``defaults.mtp`` when set, else the ``mtp`` tag."""
    defaults = model.get("defaults") if isinstance(model.get("defaults"), Mapping) else {}
    explicit = defaults.get("mtp")
    if explicit is not None:
        return bool(explicit)
    return any(str(t).strip().lower() == "mtp" for t in model.get("tags") or ())


def new_launch(
    slot: Mapping[str, Any],
    model: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    chat_template_root: str = "",
) -> LaunchAfter:
    """What a llama-server slot launches under ADR-0029: base, model, profile, slot.

    ``profile`` is new-shape content (flags, ctx_size, enable_thinking,
    vision, mtp). The launch-path change implements exactly this order and is
    pinned to this function by its parity test.
    """
    runner = frozen.effective_runner(slot, frozen.Profile(name=""))
    flags = shlex.split(str(profile.get("flags") or ""))
    embed = bool({"--embedding", "--reranking"} & set(flags))
    ctx_size = profile.get("ctx_size")
    ctx = int(ctx_size) if ctx_size else default_ctx(model)
    defaults = model.get("defaults") if isinstance(model.get("defaults"), Mapping) else {}

    base = ["--host", "0.0.0.0", "--port", str(int(slot.get("port") or 0))]
    if model.get("path"):
        base += ["--model", str(model["path"])]
    alias = model.get("_model_key") or (slot.get("model") or {}).get("default")
    if alias:
        base += ["--alias", str(alias)]
    base += ["--ctx-size", str(ctx)]

    supports = getattr(runner, "supports", None)
    jinja = (
        bool(getattr(supports, "jinja", False)) and defaults.get("jinja") is not False and not embed
    )
    model_layer = ["--jinja"] if jinja else []
    tmpl = defaults.get("chat_template")
    if tmpl and tmpl != "auto":
        model_layer += ["--chat-template-file", f"{chat_template_root}/chat-templates/{tmpl}.jinja"]
    mmproj = model.get("mmproj") if profile.get("vision", "auto") != "off" else None
    if mmproj:
        model_layer += ["--mmproj", str(mmproj)]

    mtp = (
        profile.get("mtp", "auto") != "off"
        and has_mtp_heads(model)
        and bool(getattr(supports, "mtp", False))
        and not embed
    )
    profile_layer = (shlex.split(MTP_DRAFT_BUNDLE) if mtp else []) + flags

    slot_layer: list[str] = []
    if slot.get("n_gpu_layers") is not None:
        slot_layer += ["-ngl", str(int(slot["n_gpu_layers"]))]
    threads = slot.get("threads")
    if threads is not None and int(threads) > 0:
        slot_layer += ["--threads", str(int(threads))]
    if slot.get("metrics") is None or bool(slot.get("metrics")):
        slot_layer += ["--metrics"]
    if mtp:
        backend = frozen._backend_and_class(slot, frozen.Profile(name=""))[0]
        slot_layer += [
            "--spec-draft-device",
            MTP_DRAFT_DEVICE.get((backend or "").lower(), "ROCm0"),
        ]

    from hal0.slots.argv import _dedup, _split_pairs

    argv, _removed, _w = _dedup(_split_pairs(base + model_layer + profile_layer + slot_layer))
    thinking = profile.get("enable_thinking")
    return LaunchAfter(
        argv=tuple(argv),
        ctx=ctx,
        mtp=mtp,
        jinja=jinja,
        vision_loaded=bool(mmproj),
        enable_thinking=thinking if isinstance(thinking, bool) else None,
    )


# ── names ────────────────────────────────────────────────────────────────────


def _identity(content: Mapping[str, Any]) -> dict[str, Any]:
    """A profile's tune: what launches, without informational fields."""
    out = {k: v for k, v in content.items() if k not in ("intent", "cloned_from")}
    out.setdefault("vision", "auto")
    out.setdefault("mtp", "auto")
    out["flags"] = effective_map(shlex.split(str(out.get("flags") or "")))
    return {k: v for k, v in out.items() if v not in (None, "")}


def slug_name(*parts: str, content: Mapping[str, Any], taken: Mapping[str, Any]) -> str:
    """Deterministic profile name: ``slug(parts)``, or a hashed short form.

    The short form (first 26 characters, ``-``, 5 hex of the content hash) is
    used when the slug is over 32 characters, invalid, or already names a
    profile with a different tune.
    """
    raw = re.sub(r"[^a-z0-9_-]+", "-", "-".join(p for p in parts if p).lower())
    raw = re.sub(r"-{2,}", "-", raw).strip("-_") or "profile"
    ident = _identity(content)
    digest = hashlib.sha256(json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()[:5]
    if _NAME_RE.match(raw) and (raw not in taken or _identity(taken[raw]) == ident):
        return raw
    return f"{raw[:26].rstrip('-_')}-{digest}"


# ── inputs ───────────────────────────────────────────────────────────────────


@dataclass
class Inputs:
    slots: list[tuple[str, dict[str, Any] | None, str | None]]
    profiles_raw: dict[str, Any] | None
    stacks_raw: dict[str, Any] | None
    models: dict[str, dict[str, Any]]


def _read_toml(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return tomllib.load(fh)


def read_inputs(
    *,
    slots_dir: Path | None = None,
    profiles_toml: Path | None = None,
    stacks_toml: Path | None = None,
    db_path: Path | None = None,
) -> Inputs:
    """Read raw slot/profile/stack TOML and the model rows. Never validates."""
    from hal0.config import paths

    slots_dir = slots_dir or paths.slots_config_dir()
    profiles_toml = profiles_toml or paths.profiles_toml()
    stacks_toml = stacks_toml or paths.stacks_toml()
    db_path = db_path or paths.db_path()

    slots: list[tuple[str, dict[str, Any] | None, str | None]] = []
    if slots_dir.is_dir():
        for path in sorted(slots_dir.glob("*.toml")):
            if path.name.startswith("."):
                continue
            try:
                slots.append((path.name, _read_toml(path), None))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                slots.append((path.name, None, str(exc)))
    try:
        profiles_raw = _read_toml(profiles_toml)
        stacks_raw = _read_toml(stacks_toml)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileMigrationError(f"cannot read profile/stack config: {exc}") from exc
    return Inputs(slots, profiles_raw, stacks_raw, read_model_rows(db_path))


def read_model_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    """Every ``model`` row as a plain dict, read-only, with the tuning columns
    filled from the holding table when SQL migration 012 has moved them."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "model" not in tables:
            return {}
        rows = {str(r["id"]): dict(r) for r in conn.execute("SELECT * FROM model")}
        if HOLDING_TABLE in tables:
            for held in conn.execute(f"SELECT * FROM {HOLDING_TABLE}"):
                row = rows.get(str(held["id"]))
                if row is None:
                    continue
                data = dict(held)
                for col in MODEL_TUNING_COLUMNS:
                    if data.get(col) is not None:
                        row[col] = data[col]
                extra = json.loads(row.get("extra") or "{}")
                for key in MODEL_TUNING_EXTRA_KEYS:
                    if data.get(key.lstrip("_")) is not None:
                        extra[key] = bool(data[key.lstrip("_")])
                row["extra"] = json.dumps(extra)
        return rows
    finally:
        conn.close()


def _model_tuning(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """The tuning a model row carries (what the conversion clears)."""
    if not row:
        return {}
    out = {c: row[c] for c in MODEL_TUNING_COLUMNS if c in row and row[c] not in (None, "")}
    try:
        extra = json.loads(row.get("extra") or "{}")
    except ValueError:
        extra = {}
    if isinstance(extra, dict):
        out.update({k: extra[k] for k in MODEL_TUNING_EXTRA_KEYS if k in extra})
    if row.get("mtp") is not None and not row["mtp"]:
        # Operator decision: an explicit "no MTP" is tuning (the profile's
        # mtp = "off"); the model keeps only the "has MTP heads" fact.
        out["mtp"] = False
    return out


def _row_after(row: Mapping[str, Any]) -> dict[str, Any]:
    """The model row once its tuning is cleared."""
    after = dict(row)
    for col in MODEL_TUNING_COLUMNS:
        if col in after:
            after[col] = None
    try:
        extra = json.loads(after.get("extra") or "{}")
    except ValueError:
        extra = {}
    if isinstance(extra, dict):
        for key in MODEL_TUNING_EXTRA_KEYS:
            extra.pop(key, None)
        after["extra"] = json.dumps(extra)
    if after.get("mtp") is not None and not after["mtp"]:
        after["mtp"] = None
    return after


# ── planning ─────────────────────────────────────────────────────────────────


@dataclass
class ProfileConversion:
    content: dict[str, Any]
    notes: list[str]
    errors: list[str]


def convert_profile(entry: Mapping[str, Any], family: str) -> ProfileConversion:
    """One stored profile (any shape) in the ADR-0029 shape."""
    notes: list[str] = []
    for key in PROFILE_DROP_KEYS:
        if key in entry:
            value = entry[key]
            if key in ("device_class", "backend") and value:
                notes.append(f"{key} = {value!r} dropped (fit hint; the slot decides)")
            elif key not in ("mtp", "quant") or value:
                notes.append(f"{key} dropped")
    conv = convert_flags(str(entry.get("flags") or ""), source="profile")
    notes += conv.notes
    content: dict[str, Any] = {"flags": _join(conv.tokens)}
    for key in ("ctx_size", "enable_thinking", "intent", "cloned_from"):
        if entry.get(key) not in (None, ""):
            content[key] = entry[key]
    vision = entry.get("vision")
    content["vision"] = "off" if conv.vision_off or vision == "off" else "auto"
    content["mtp"] = "off" if entry.get("mtp") == "off" else "auto"
    runner = entry.get("runner")
    if isinstance(runner, str) and runner.strip():
        content["runner"] = runner
    elif family != frozen.LLAMA and family in ENGINE_RUNNER:
        content["runner"] = ENGINE_RUNNER[family]
        notes.append(f"runner = {ENGINE_RUNNER[family]!r} made explicit")
    return ProfileConversion(content, notes, conv.errors)


def _tidy(content: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in content.items() if v not in (None, "")}
    if out.get("vision") == "auto":
        out.pop("vision")
    if out.get("mtp") == "auto":
        out.pop("mtp")
    return out


ProfileShape = Literal["old", "new", "either", "mixed"]


def profile_shape(entry: Mapping[str, Any]) -> tuple[ProfileShape, list[str]]:
    """Which shape a stored profile is in, read from its content alone.

    ``old``: it carries a key only the old shape has (a boolean ``mtp``,
    ``device_class``, ``backend``, ``quant``, ``image``) or flags the new
    shape does not accept. ``new``: it carries a key only the new shape has
    (``ctx_size``, ``enable_thinking``, ``vision``, a string ``mtp``).
    ``either``: only the keys both shapes share, with flags already in the
    new form, so converting it changes nothing. ``mixed``: old and new keys
    together, which blocks. The list names the keys that decided it.
    """
    old = [k for k in OLD_PROFILE_KEYS if k in entry]
    new = [k for k in NEW_PROFILE_KEYS if k in entry]
    mtp = entry.get("mtp")
    if isinstance(mtp, bool):
        old.append("mtp")
    elif isinstance(mtp, str):
        new.append("mtp")
    if old and new:
        return "mixed", old + new
    if new:
        return "new", new
    if old:
        return "old", old
    flags = str(entry.get("flags") or "")
    try:
        as_is = shlex.split(flags) if flags.strip() else []
    except ValueError:
        return "old", ["flags"]
    conv = convert_flags(as_is, source="profile")
    if conv.errors or conv.notes or conv.vision_off or conv.tokens != as_is:
        return "old", ["flags"]
    return "either", []


class _Planner:
    def __init__(self, inputs: Inputs) -> None:
        self.inputs = inputs
        self.plan = ConversionPlan()
        raw = inputs.profiles_raw
        entries = (raw or {}).get("profile")
        entries = entries if isinstance(entries, Mapping) else {}
        #: every stored custom profile's shape (see :func:`profile_shape`).
        self.shapes: dict[str, ProfileShape] = {}
        #: stored profiles already in the new shape: kept exactly as they are.
        self.current: dict[str, dict[str, Any]] = {}
        old_entries: dict[str, Any] = {}
        for name, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            shape, evidence = profile_shape(entry)
            self.shapes[str(name)] = shape
            if shape == "mixed":
                self.plan.blocked_profiles[str(name)] = [
                    f"mixes old-shape and new-shape keys ({', '.join(evidence)}); "
                    "it is left as is and must be fixed by hand"
                ]
            elif shape == "new":
                self.current[str(name)] = dict(entry)
            else:
                old_entries[str(name)] = entry
        # The frozen resolver only ever sees old-shape (or shape-neutral)
        # profiles: a new-shape one means something else to it.
        self.old = frozen.Catalog(None if raw is None else {**raw, "profile": old_entries})
        self.bound: set[str] = set()
        #: new-shape catalog after conversion: target seeds + converted customs.
        self.catalog: dict[str, dict[str, Any]] = {k: dict(v) for k, v in target_seeds().items()}
        #: names no converted or generated profile may take.
        self.reserved: set[str] = {str(n) for n in entries}
        #: old profile name -> its name after conversion.
        self.name_map: dict[str, str] = {}

    # -- profiles --------------------------------------------------------

    def _place(self, name: str, conv: ProfileConversion, *, new: bool) -> str:
        """Put a converted custom/demoted profile in the new catalog.

        A name a target seed now owns is kept only when the tune is the
        seed's; otherwise the custom moves to ``<name>-custom``.
        """
        content = _tidy(conv.content)
        if conv.errors:
            # Written without the offending flag; every slot still running it
            # is blocked, and the profile itself is reported as blocked.
            self.plan.blocked_profiles[name] = conv.errors
        if name in target_seeds():
            if _identity(content) == _identity(target_seeds()[name]):
                if not new:
                    self.plan.profile_rewrites[name] = [
                        "identical to the new seed of that name; the custom entry is removed"
                    ]
                return name
            renamed = self._free_name(name, "custom")
            if name in self.plan.blocked_profiles:
                self.plan.blocked_profiles[renamed] = self.plan.blocked_profiles.pop(name)
            self.plan.renames[name] = renamed
            self.catalog[renamed] = content
            self.plan.profiles[renamed] = content
            return renamed
        self.catalog[name] = content
        if new:
            self.plan.profiles[name] = content
        elif conv.notes:
            self.plan.profile_rewrites[name] = conv.notes
        return name

    def _free_name(self, name: str, suffix: str) -> str:
        """``<name>-<suffix>``, else ``-<suffix>-2``, ``-3``…: the first name no
        stored, converted or generated profile holds (never overwrites one)."""
        taken = set(self.catalog) | self.reserved | set(self.name_map.values())
        for n in itertools.count(1):
            tail = f"-{suffix}" if n == 1 else f"-{suffix}-{n}"
            candidate = name[: 32 - len(tail)].rstrip("-_") + tail
            if candidate not in taken and _NAME_RE.match(candidate):
                return candidate
        raise AssertionError("unreachable")

    def plan_profiles(self) -> None:
        # Profiles already in the new shape stay exactly as they are. One a new
        # seed shadows (same name) never launched; it is left for the operator.
        for name, content in sorted(self.current.items()):
            if name not in target_seeds():
                self.catalog[name] = content
                self.name_map[name] = name
        for name in sorted(self.old.custom):
            resolved = self.old.resolve(name)
            assert resolved is not None
            conv = convert_profile(self.old.custom[name], frozen.runtime_family(resolved))
            self.name_map[name] = self._place(name, conv, new=False)
        raw = self.inputs.profiles_raw or {}
        markers = [m for m in ("legacy_seeds_migrated", "legacy_seeds_adopted") if m in raw]
        if markers:
            self.plan.profile_rewrites["(profiles.toml)"] = [f"{m} removed" for m in markers]

    def new_name(self, old_name: str) -> str | None:
        """The profile an old name resolves to after conversion (None: nothing)."""
        if not old_name:
            return None
        if old_name in self.name_map:
            return self.name_map[old_name]
        if old_name in self.old.seeds:
            return old_name if old_name in self.catalog else None
        if old_name in self.old.demoted and old_name not in self.old.settled:
            entry = self.old.demoted[old_name]
            conv = convert_profile(entry, frozen.LLAMA)
            new = self._place(old_name, conv, new=True)
            self.name_map[old_name] = new
            return new
        if old_name in target_seeds():
            return old_name  # a new seed (agent, utility, …) names itself
        return None

    def _generate(self, base: str, suffix: str, content: dict[str, Any]) -> str:
        """A profile holding ``content``; reuses any profile with the same tune."""
        content = _tidy(content)
        ident = _identity(content)
        for name in sorted(self.catalog):
            if _identity(self.catalog[name]) == ident:
                return name
        taken = {**{n: {} for n in self.reserved}, **self.catalog}
        name = slug_name(base, suffix, content=content, taken=taken)
        if name in self.reserved and name not in self.catalog:
            name = self._free_name(name, "converted")
        self.catalog[name] = content
        self.plan.profiles[name] = content
        return name

    # -- slots -----------------------------------------------------------

    def _select_files(self) -> list[tuple[str, dict[str, Any] | None, str | None]]:
        """One file per slot: the id-keyed file wins over a same-named
        name-keyed one (``SlotManager._config_file_for``)."""
        by_name: dict[str, list[tuple[str, dict[str, Any] | None, str | None]]] = {}
        out: list[tuple[str, dict[str, Any] | None, str | None]] = []
        for fname, raw, err in self.inputs.slots:
            if raw is None:
                out.append((fname, raw, err))
                continue
            slot = frozen.hoist_slot(raw, fname[:-5])
            by_name.setdefault(str(slot.get("name")), []).append((fname, raw, err))
        for _name, files_ in sorted(by_name.items()):
            files_.sort(key=lambda f: (not f[0][:-5].isdigit(), f[0]))
            out.append(files_[0])
            self.plan.shadowed += [f[0] for f in files_[1:]]
        return sorted(out, key=lambda f: f[0])

    def plan_slots(self) -> None:
        for fname, raw, error in self._select_files():
            stem = fname[:-5]
            if raw is None:
                self.plan.slots.append(
                    SlotPlan(file=fname, name=stem, status="blocked", error=f"unreadable: {error}")
                )
                continue
            slot = frozen.hoist_slot(raw, stem)
            sp = SlotPlan(file=fname, name=str(slot.get("name") or stem))
            try:
                if not self._already_converted(raw, slot, sp):
                    self._plan_slot(raw, slot, sp)
            except (ValueError, Hal0Error) as exc:
                sp.status, sp.error = "blocked", str(exc)
            self.plan.slots.append(sp)

    def _profile_is_current(self, named: str) -> bool:
        """Does ``named`` mean the same profile in the new catalog as it does
        to a converted slot (so a slot naming it needs no conversion)?"""
        if not named:
            return False  # a converted slot always names its profile
        shape = self.shapes.get(named)
        if shape == "new":
            return True
        if shape == "either":
            # Valid in both shapes, unless a new seed now owns the name (the
            # entry is renamed and the slot re-pointed).
            return named not in target_seeds()
        if shape == "old":
            return False
        return named in target_seeds()

    def _already_converted(
        self, raw: Mapping[str, Any], slot: Mapping[str, Any], sp: SlotPlan
    ) -> bool:
        """Read the slot's shape from its content (ADR-0029 §7).

        A slot whose file carries no old-shape key, whose profile means the
        same in the new catalog and whose model row carries no tuning is
        already converted: it is never handed to the frozen resolver. Any
        old-shape content is converted, whatever an earlier run recorded.
        Old-shape slot content next to an already-converted profile cannot be
        evaluated, so it blocks.
        """
        named = str(slot.get("profile") or "")
        if self.shapes.get(named) == "mixed":
            raise ValueError(
                f"profile {named!r} mixes old-shape and new-shape keys; fix it by hand first"
            )
        probe = SlotPlan(file=sp.file, name=sp.name, engine=frozen.engine_for(slot, self.old))
        self._shape_keys(raw, slot, probe)
        old_keys = [*probe.drop_keys, *probe.set_keys]
        file_old = bool(old_keys or probe.inert)
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        model_id = str(table.get("default") or "")
        model_old = bool(_model_tuning(self.inputs.models.get(model_id) if model_id else None))
        current = self._profile_is_current(named)
        if current and not file_old and not model_old:
            sp.profile_before = sp.profile_after = named
            sp.notes.append("already in the new shape")
            return True
        if self.shapes.get(named) != "new":
            return False
        if file_old:
            raise ValueError(
                f"carries old-shape keys ({', '.join(old_keys) or 'inert tuning'}) but its "
                f"profile {named!r} is already converted; remove the keys by hand"
            )
        # A converted slot whose model row still carries tuning: the tuning
        # no longer reaches this slot. It is kept as a profile of its own
        # (planned as unbound) and cleared from the model.
        sp.profile_before = sp.profile_after = named
        sp.notes.append(f"already in the new shape; model {model_id!r} tuning is not applied to it")
        return True

    def _shape_keys(self, raw: Mapping[str, Any], slot: Mapping[str, Any], sp: SlotPlan) -> None:
        for key in SLOT_DROP_KEYS:
            if key in slot:
                sp.drop_keys.append(key)
        if "backend" in slot and not slot.get("device"):
            from hal0.model_meta import map_backend_to_device

            sp.set_keys["device"] = map_backend_to_device(str(slot["backend"]))
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        sp.drop_keys += [f"[model].{k}" for k in MODEL_TABLE_DROP if k in table]
        server = raw.get("server") if isinstance(raw.get("server"), Mapping) else {}
        if "extra_args" in server:
            sp.drop_keys.append("[server].extra_args")
            if str(server.get("extra_args") or "").strip():
                sp.inert.append(InertValue("slot", "extra_args", server["extra_args"]))
        if isinstance(raw.get("defaults"), Mapping):
            sp.drop_keys.append("[defaults]")
        if slot.get("parallel") is not None:
            sp.inert.append(InertValue("slot", "parallel", slot["parallel"]))
        if slot.get("chat_template") not in (None, "", "auto"):
            sp.inert.append(InertValue("slot", "chat_template", slot["chat_template"]))
        for key in ("mtp", "enable_thinking", "vision"):
            if key in slot:
                sp.inert.append(InertValue("slot", key, slot[key]))
        rope = table.get("rope_freq_base")
        if rope not in (None, 0, 0.0):
            sp.inert.append(InertValue("slot", "rope_freq_base", rope))
        image = slot.get("image")
        if isinstance(image, str) and image:
            if sp.engine == "comfyui" and not slot.get("image_pin"):
                sp.set_keys["image_pin"] = image
            else:
                sp.inert.append(InertValue("slot", "image", image))

    def _plan_slot(self, raw: Mapping[str, Any], slot: dict[str, Any], sp: SlotPlan) -> None:
        sp.engine = frozen.engine_for(slot, self.old)
        named = str(slot.get("profile") or "")
        sp.profile_before = sp.profile_after = named
        self._shape_keys(raw, slot, sp)
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        model_id = str(table.get("default") or "")
        row = self.inputs.models.get(model_id) if model_id else None
        if model_id:
            self.bound.add(model_id)
        if sp.engine != frozen.LLAMA:
            self._plan_other_engine(slot, sp, model_id, row)
        elif not model_id:
            self._plan_modelless(slot, sp)
        else:
            self._plan_llama(slot, sp, model_id, row)
        if named and sp.profile_after in self.plan.blocked_profiles:
            raise ValueError(
                f"profile {named!r} cannot be converted: "
                + "; ".join(self.plan.blocked_profiles[sp.profile_after])
            )
        if (
            sp.profile_after != sp.profile_before
            or sp.drop_keys
            or sp.set_keys
            or sp.launch_changes
        ):
            sp.status = "convert"

    def _base_for(self, slot: Mapping[str, Any], sp: SlotPlan, launched: str) -> str:
        """The profile name the slot runs after conversion, before tuning."""
        named = sp.profile_before
        new = self.new_name(named) if named else None
        if new is not None:
            if new != named:
                sp.notes.append(f"profile {named!r} is now {new!r}")
            return new
        implied = TYPE_SEED.get(str(slot.get("type") or ""), "")
        target = implied or self.new_name(launched) or "chat"
        why = f"profile {named!r} does not exist" if named else "no profile"
        sp.notes.append(f"{why}; today it launches {launched!r}; now {target!r}")
        return target

    def _plan_llama(
        self, slot: dict[str, Any], sp: SlotPlan, model_id: str, row: Mapping[str, Any] | None
    ) -> None:
        model = frozen.model_info_from_row(row) if row else {"_model_key": model_id}
        model_after = frozen.model_info_from_row(_row_after(row)) if row else model
        old = frozen.evaluate(slot, model, self.old)
        sp.argv_today = old.argv
        base = self._base_for(slot, sp, old.profile_launched)
        fixed_profile = False
        if (old.profile_missing or not sp.profile_before) and base in TYPE_SEED.values():
            # Today the slot launches without its type's mode flag; it now
            # gets the seed that carries it.
            fixed_profile = base != old.profile_launched
            if fixed_profile:
                old = frozen.evaluate({**slot, "profile": base}, model, self.old)
                sp.launch_changes.append(
                    f"launches with the {base!r} profile (its type's mode flag) instead of "
                    f"{sp.profile_before or 'no profile'!r}"
                )

        tune = [
            (canon, flag, values)
            for canon, flag, values in _pairs(old.argv)
            if old.sources.get(canon) in _TUNE_SEGMENTS
        ]
        bundle = {c: v for c, _f, v in _pairs(shlex.split(frozen.mtp_bundle(old.mtp_backend)))}
        tokens: list[str] = []
        for canon, flag, values in tune:
            if (
                old.mtp
                and canon in bundle
                and bundle[canon] == values
                and canon != "--spec-draft-device"
            ):
                continue
            if old.mtp and canon == "--spec-draft-device" and bundle.get(canon) == values:
                continue
            tokens += [flag, *values]
        conv = convert_flags(tokens, source="tune")
        if conv.errors:
            raise ValueError("; ".join(conv.errors))
        sp.notes += [n for n in conv.notes if not n.startswith("--jinja")]
        if conv.threads is not None:
            own = slot.get("threads")
            if not (own is not None and int(own) > 0):
                sp.set_keys["threads"] = conv.threads
        slot_after = {**slot, **sp.set_keys}
        self._note_unhonoured_ceiling(slot, old, sp)

        vision_off = conv.vision_off or (bool(model.get("mmproj")) and not old.vision_loaded)
        if vision_off and model.get("mmproj") and old.mmproj:
            sp.notes.append(
                "--no-mmproj overrode --mmproj today (no projector loaded): vision = off"
            )
        runner = frozen.effective_runner(slot_after, frozen.Profile(name=""))
        embed = bool({"--embedding", "--reranking"} & set(conv.tokens))
        auto_drafts = (
            has_mtp_heads(model_after)
            and bool(getattr(getattr(runner, "supports", None), "mtp", False))
            and not embed
        )
        base_content = self.catalog.get(base, {"flags": ""})
        if base_content.get("ctx_size") == old.ctx or old.ctx != default_ctx(model_after):
            ctx_size: int | None = old.ctx
        else:
            ctx_size = None
        target: dict[str, Any] = {
            "flags": _join(conv.tokens),
            "ctx_size": ctx_size,
            "enable_thinking": old.enable_thinking,
            "vision": "off" if vision_off else "auto",
            "mtp": "off" if (auto_drafts and not old.mtp) else "auto",
        }
        want = {**target, "runner": base_content.get("runner")}
        if _identity(_tidy(want)) == _identity(base_content):
            sp.profile_after = base
        else:
            want.update(cloned_from=base, intent=f"Converted: slot {sp.name}, model {model_id}")
            sp.profile_after = self._generate(base, model_id, want)
            sp.generated = sp.profile_after in self.plan.profiles

        after = new_launch(slot_after, model_after, self.catalog[sp.profile_after])
        sp.argv_after = after.argv
        self._diff(sp, old, after, fixed_profile)

    def _diff(self, sp: SlotPlan, old: frozen.LaunchToday, after: LaunchAfter, fixed: bool) -> None:
        """Every difference is a reported launch change or blocks the slot."""
        before = effective_map(old.argv)
        now = effective_map(after.argv)
        unexplained: list[str] = []
        for key in sorted(set(before) | set(now)):
            if before.get(key) == now.get(key):
                continue
            if key in _PROJECTOR_FLAGS:
                continue  # compared as "projector loaded" below

            if key == "--spec-draft-device" and old.mtp and after.mtp:
                sp.launch_changes.append(
                    f"MTP draft device {before.get(key)} -> {now.get(key)} (from the slot's device)"
                )
                continue
            if (
                key == "--jinja"
                and old.jinja
                and not after.jinja
                and {"--embedding", "--reranking"} & set(after.argv)
            ):
                sp.launch_changes.append("--jinja no longer added in embedding/reranking mode")
                continue
            if old.mtp and not after.mtp and key.startswith("--spec-"):
                continue  # reported once below
            unexplained.append(f"{key}: {before.get(key)} -> {now.get(key)}")
        if old.mtp and not after.mtp:
            sp.launch_changes.append(
                "MTP drafting stops: the model forced it on a runner without MTP support"
            )
        if old.enable_thinking != after.enable_thinking:
            unexplained.append(
                f"thinking default: {old.enable_thinking} -> {after.enable_thinking}"
            )
        if old.vision_loaded != after.vision_loaded:
            unexplained.append(f"projector loaded: {old.vision_loaded} -> {after.vision_loaded}")
        if unexplained and not fixed:
            raise ValueError("the converted launch differs: " + "; ".join(unexplained))
        if unexplained:
            sp.launch_changes.append("with its type's profile: " + "; ".join(unexplained))

    @staticmethod
    def _note_unhonoured_ceiling(
        slot: Mapping[str, Any], launch: frozen.LaunchToday, sp: SlotPlan
    ) -> None:
        """A ``[model].context_size`` above what launches is an inert value."""
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        ceiling = table.get("context_size")
        try:
            value = int(ceiling) if ceiling is not None else None
        except (TypeError, ValueError):
            sp.inert.append(InertValue("slot", "context_size", ceiling))
            return
        if value is None or value <= launch.ctx:
            return
        sp.inert.append(InertValue("slot", "context_size", value))
        sp.notes.append(f"ceiling {value} is not honoured today (launches {launch.ctx})")

    def _plan_modelless(self, slot: Mapping[str, Any], sp: SlotPlan) -> None:
        base = self._base_for(slot, sp, "chat")
        sp.profile_after = base
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        raw_ceiling = table.get("context_size")
        if raw_ceiling is None:
            return
        try:
            ceiling = int(raw_ceiling)
        except (TypeError, ValueError):
            sp.inert.append(InertValue("slot", "context_size", raw_ceiling))
            return
        if not (frozen.CTX_MIN_PLAUSIBLE <= ceiling <= frozen.CTX_MAX_PLAUSIBLE):
            sp.inert.append(InertValue("slot", "context_size", raw_ceiling))
            return
        base_ctx = self.catalog.get(base, {}).get("ctx_size")
        if ceiling == (base_ctx or frozen.CTX_DENSE_CAP):
            return
        content = {
            **self.catalog.get(base, {"flags": ""}),
            "ctx_size": ceiling,
            "cloned_from": base,
            "intent": f"Converted: slot {sp.name} context {ceiling}",
        }
        sp.profile_after = self._generate(base, f"ctx{ceiling}", content)
        sp.generated = sp.profile_after in self.plan.profiles

    def _plan_other_engine(
        self, slot: Mapping[str, Any], sp: SlotPlan, model_id: str, row: Mapping[str, Any] | None
    ) -> None:
        engine = sp.engine
        tuning = _model_tuning(row)
        if tuning.get("extra_args"):
            sp.inert.append(InertValue("model", "extra_args", tuning["extra_args"]))
        old_flags = frozen.engine_flags(slot, self.old, engine)
        base = self.new_name(sp.profile_before) if sp.profile_before else None
        if base is None:
            base = ENGINE_SEED.get(engine, "chat")
            if old_flags is None:
                sp.launch_changes.append(
                    f"profile {sp.profile_before or '(none)'!r} resolves to nothing, so it "
                    f"fails to launch today; now {base!r}"
                )
            else:
                sp.notes.append(f"profile {sp.profile_before or '(none)'!r} -> {base!r}")
        base_content = self.catalog.get(base, {"flags": ""})
        flags_now = old_flags if old_flags is not None else str(base_content.get("flags") or "")
        tokens = shlex.split(flags_now) if flags_now.strip() else []
        target: dict[str, Any] = {
            "flags": _join(tokens),
            "runner": ENGINE_RUNNER.get(engine),
            "vision": base_content.get("vision"),
            "mtp": base_content.get("mtp"),
            "ctx_size": base_content.get("ctx_size"),
        }
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        if engine == "flm":
            # FLM's window is always explicit after conversion: today it is the
            # slot ctx or 8192 (providers/flm.py build_env).
            target["ctx_size"] = int(frozen.flm_ctx(slot))
        elif table.get("context_size") is not None:
            sp.inert.append(InertValue("slot", "context_size", table["context_size"]))
        if isinstance(tuning.get("_enable_thinking"), bool):
            target["enable_thinking"] = tuning["_enable_thinking"]
        if engine == "comfyui" and not target["flags"]:
            target["flags"] = frozen.COMFYUI_DEFAULT_FLAGS
        if _identity(_tidy(target)) == _identity(
            _tidy({**base_content, "runner": ENGINE_RUNNER.get(engine)})
        ):
            sp.profile_after = base
            return
        target.update(cloned_from=base, intent=f"Converted: slot {sp.name}, model {model_id}")
        sp.profile_after = self._generate(base, model_id or engine, target)
        sp.generated = sp.profile_after in self.plan.profiles

    # -- models and stacks -----------------------------------------------

    def plan_models(self) -> None:
        for model_id, row in sorted(self.inputs.models.items()):
            tuning = _model_tuning(row)
            if not tuning:
                continue
            mp = ModelPlan(model_id=model_id, clear=tuning)
            if model_id not in self.bound:
                self._plan_unbound(mp, tuning)
            self.plan.models.append(mp)

    def _plan_unbound(self, mp: ModelPlan, tuning: Mapping[str, Any]) -> None:
        conv = convert_flags(str(tuning.get("extra_args") or ""), source="profile")
        live = {
            "flags": _join(conv.tokens) if tuning.get("extra_args") else None,
            "ctx_size": tuning.get("context_size"),
            "enable_thinking": tuning.get("_enable_thinking"),
            "vision": "off" if tuning.get("_vision") is False or conv.vision_off else None,
            "mtp": "off" if tuning.get("mtp") is False else None,
        }
        if all(v in (None, "") for v in live.values()):
            return
        if conv.errors:
            mp.notes.append("extra_args not kept: " + "; ".join(conv.errors))
            live["flags"] = None
        base = self.new_name(str(tuning.get("profile") or "")) or "chat"
        if tuning.get("profile") and base == "chat" and tuning["profile"] != "chat":
            mp.notes.append(f"preferred profile {tuning['profile']!r} does not exist")
        content = {**self.catalog.get(base, {"flags": ""})}
        content.update({k: v for k, v in live.items() if v not in (None, "")})
        content.update(
            cloned_from=base, intent=f"Converted: model {mp.model_id} (not bound to a slot)"
        )
        mp.generated_profile = self._generate(base, mp.model_id, content)

    def plan_stacks(self) -> None:
        stacks = (self.inputs.stacks_raw or {}).get("stack")
        if not isinstance(stacks, Mapping):
            return
        by_slot = {s.name: s for s in self.plan.slots}
        for stack_name, stack in sorted(stacks.items()):
            if not isinstance(stack, Mapping):
                continue
            st = StackPlan(stack=str(stack_name))
            embedded = stack.get("profiles") if isinstance(stack.get("profiles"), Mapping) else {}
            for pname, entry in sorted(embedded.items()):
                if not isinstance(entry, Mapping):
                    continue
                shape, evidence = profile_shape(entry)
                if shape == "new":
                    continue  # already converted
                if shape == "mixed":
                    st.errors.append(
                        f"embedded profile {pname!r} mixes old-shape and new-shape keys "
                        f"({', '.join(evidence)}); fix it by hand"
                    )
                    continue
                conv = convert_profile(entry, frozen.LLAMA)
                if conv.errors:
                    st.errors.append(f"embedded profile {pname!r}: " + "; ".join(conv.errors))
                if conv.notes:
                    st.profile_rewrites[str(pname)] = conv.notes
            rows = stack.get("slots") if isinstance(stack.get("slots"), list) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                change = self._plan_stack_row(row, embedded, by_slot)
                if change:
                    st.rows.append({"slot": str(row.get("slot") or ""), "change": change})
            if st.rows or st.profile_rewrites or st.errors:
                self.plan.stacks.append(st)

    def _plan_stack_row(
        self,
        row: Mapping[str, Any],
        embedded: Mapping[str, Any],
        by_slot: Mapping[str, SlotPlan],
    ) -> str:
        parts: list[str] = []
        present = [k for k in STACK_ROW_TUNING_KEYS if k in row]
        if present:
            shown = [
                f"{k}={row[k]!r}"
                for k in present
                if row[k] not in (None, "") and not (k == "vision" and row[k] is False)
            ]
            parts.append(
                f"drop {', '.join(present)}"
                + (f" (inert today: {', '.join(shown)})" if shown else "")
            )
        pname = str(row.get("profile") or "")
        if pname and pname not in embedded:
            sp = by_slot.get(str(row.get("slot") or ""))
            if (
                sp is not None
                and sp.status != "blocked"
                and sp.profile_before == pname
                and sp.profile_after != pname
            ):
                parts.append(f"profile {pname!r} -> {sp.profile_after!r} (the converted slot's)")
            else:
                new = self.new_name(pname)
                if new is None:
                    parts.append(f"profile {pname!r} does not exist; applying it fails")
                elif new != pname:
                    parts.append(f"profile {pname!r} -> {new!r}")
        return "; ".join(parts)


def plan_conversion(inputs: Inputs | None = None) -> ConversionPlan:
    """Plan the conversion of this install (or of ``inputs``). Writes nothing."""
    inputs = inputs or read_inputs()
    planner = _Planner(inputs)
    planner.plan_profiles()
    planner.plan_slots()
    planner.plan_models()
    planner.plan_stacks()
    plan = planner.plan
    if plan.blockers and "(profiles.toml)" in plan.profile_rewrites:
        plan.profile_rewrites.pop("(profiles.toml)")
        plan.warnings.append(
            "the demoted-seed markers in profiles.toml stay until nothing is blocked"
        )
    return plan


__all__ = [
    "HOLDING_TABLE",
    "MTP_DRAFT_BUNDLE",
    "STATE_FILE",
    "ConversionPlan",
    "FlagConversion",
    "InertValue",
    "Inputs",
    "LaunchAfter",
    "ModelPlan",
    "ProfileConversion",
    "ProfileMigrationError",
    "SlotPlan",
    "StackPlan",
    "convert_flags",
    "convert_profile",
    "default_ctx",
    "effective_map",
    "has_mtp_heads",
    "new_launch",
    "plan_conversion",
    "read_inputs",
    "read_model_rows",
    "slug_name",
    "target_seeds",
]
