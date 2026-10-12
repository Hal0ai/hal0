"""Plan the move of an install onto profile-centric config (ADR-0029).

Model rows keep file facts, profiles hold every tune, slots hold placement.
This module plans that move for one install: for every slot it works out what
the slot launches today (:mod:`hal0.config.migrations.profile_centric_frozen`)
and which profile reproduces it under the new rules, then lists the profiles
to generate, the slot re-points, the keys to drop, the model tuning to clear
and every value that changes nothing today ("inert").

This first phase only PLANS. It writes nothing; ``hal0 profile migrate``
prints the plan. The writer, the backup and the restore arrive with the launch
path that reads the new shape, so no install can hold half of it.

One switch decides what happens to inert values (slot ``[server].extra_args``,
``parallel``, a context ceiling that never took effect, stack-row tuning):
:data:`DEFAULT_INERT_POLICY`. ``"preserve_launch"`` (the default) keeps what
launches and reports every inert value. ``"apply_intent"`` folds them into the
generated profile instead, which changes those slots at their next restart.
The policy is read in exactly one place, :func:`inert_additions`.

Every rule is "old shape? convert : leave", so an install already in the new
shape plans nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sqlite3
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hal0.config.migrations import profile_centric_frozen as frozen
from hal0.errors import Hal0Error

# ── the inert-value switch ───────────────────────────────────────────────────

InertPolicy = Literal["preserve_launch", "apply_intent"]

#: ``preserve_launch``: what launches is preserved and inert values are only
#: reported. ``apply_intent``: inert values are folded into the generated
#: profile (changes those slots at their next restart).
DEFAULT_INERT_POLICY: InertPolicy = "preserve_launch"
INERT_POLICIES: tuple[InertPolicy, ...] = ("preserve_launch", "apply_intent")


@dataclass(frozen=True)
class InertValue:
    """A configured value that does not reach the launch today."""

    where: str
    key: str
    value: Any
    applied: bool = False


@dataclass(frozen=True)
class InertAdditions:
    """What the inert-value policy adds to one slot's generated profile."""

    flags: tuple[str, ...] = ()
    ctx_size: int | None = None
    vision_off: bool = False
    enable_thinking: bool | None = None


def inert_additions(
    policy: InertPolicy, inert: list[InertValue]
) -> tuple[InertAdditions, list[InertValue]]:
    """THE switch: turn inert values into profile content, or not.

    Returns ``(additions, inert)`` where ``inert`` is the same list with
    ``applied`` set on every value the policy used. Under ``preserve_launch``
    nothing is added or applied. Under ``apply_intent`` free flag text is
    appended last (so it wins), ``parallel`` becomes ``--parallel``, an
    unhonoured ceiling becomes ``ctx_size``, and ``vision = false`` /
    ``enable_thinking`` carry over. Nothing else reads the policy.
    """
    if policy not in INERT_POLICIES:
        raise ValueError(f"unknown inert-value policy {policy!r}")
    if policy == "preserve_launch":
        return InertAdditions(), list(inert)
    flags: list[str] = []
    ctx: int | None = None
    vision_off = False
    thinking: bool | None = None
    marked: list[InertValue] = []
    for item in inert:
        used = True
        if item.key in ("extra_args", "server_extra_args") and str(item.value or "").strip():
            tokens, _ = frozen._strip(
                shlex.split(str(item.value)), frozen.MANAGED_FLAGS | frozen.HARDWARE_FLAGS
            )
            flags += tokens
        elif item.key == "parallel" and item.value:
            flags += ["--parallel", str(int(item.value))]
        elif item.key == "context_size" and item.value:
            ctx = int(item.value)
        elif item.key == "vision" and item.value is False:
            vision_off = True
        elif item.key == "enable_thinking" and isinstance(item.value, bool):
            thinking = item.value
        else:
            used = False
        marked.append(InertValue(item.where, item.key, item.value, applied=used))
    return InertAdditions(tuple(flags), ctx, vision_off, thinking), marked


# ── plan data ────────────────────────────────────────────────────────────────


class ProfileMigrationError(Hal0Error):
    """The plan could not be built at all (an input that cannot be read)."""

    code = "profiles.migration_failed"


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


@dataclass
class ModelPlan:
    model_id: str
    clear: dict[str, Any] = field(default_factory=dict)
    generated_profile: str | None = None
    register_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class StackRowPlan:
    stack: str
    slot: str
    drop_keys: list[str] = field(default_factory=list)
    inert: list[InertValue] = field(default_factory=list)
    profile_after: str | None = None


@dataclass
class ConversionPlan:
    policy: InertPolicy
    slots: list[SlotPlan] = field(default_factory=list)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    profile_rewrites: dict[str, list[str]] = field(default_factory=dict)
    models: list[ModelPlan] = field(default_factory=list)
    stacks: list[StackRowPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            any(s.status != "unchanged" for s in self.slots)
            or self.profiles
            or self.profile_rewrites
            or any(m.clear or m.register_path for m in self.models)
            or self.stacks
        )

    @property
    def blocked(self) -> list[SlotPlan]:
        return [s for s in self.slots if s.status == "blocked"]

    def lines(self) -> list[str]:
        """Human report, one line per finding."""
        out = [f"policy for inert values: {self.policy}"]
        for s in self.slots:
            if s.status == "unchanged":
                out.append(f"slot {s.name} ({s.file}): unchanged, profile {s.profile_before!r}")
                continue
            if s.status == "blocked":
                out.append(f"slot {s.name} ({s.file}): BLOCKED: {s.error}")
                continue
            how = "generated" if s.generated else "existing"
            out.append(
                f"slot {s.name} ({s.file}): profile {s.profile_before or '(none)'!r} -> "
                f"{s.profile_after!r} ({how})"
            )
            if s.drop_keys:
                out.append(f"  drop keys: {', '.join(s.drop_keys)}")
            for k, v in s.set_keys.items():
                out.append(f"  set {k} = {v!r}")
            for change in s.launch_changes:
                out.append(f"  LAUNCH CHANGE at next restart: {change}")
            for item in s.inert:
                verb = "applied" if item.applied else "inert today, not applied"
                out.append(f"  {item.where} {item.key} = {item.value!r}: {verb}")
            out.extend(f"  note: {n}" for n in s.notes)
        for name, content in self.profiles.items():
            out.append(f"new profile {name}: {json.dumps(content, sort_keys=True)}")
        for name, dropped in self.profile_rewrites.items():
            out.append(f"profile {name}: rewrite ({'; '.join(dropped)})")
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
        for r in self.stacks:
            out.append(f"stack {r.stack} row {r.slot}: drop {', '.join(r.drop_keys)}")
            for item in r.inert:
                verb = "applied" if item.applied else "inert today, not applied"
                out.append(f"  {item.key} = {item.value!r}: {verb}")
            if r.profile_after:
                out.append(f"  profile -> {r.profile_after!r}")
        out.extend(f"warning: {w}" for w in self.warnings)
        if not self.changed:
            out.append("nothing to convert")
        return out


# ── constants of the new shape ───────────────────────────────────────────────

#: Slot keys that leave the slot (ADR-0029 decision 3).
SLOT_TUNING_KEYS: tuple[str, ...] = (
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
)
MODEL_TABLE_DROP: tuple[str, ...] = ("context_size", "n_gpu_layers", "rope_freq_base", "extra")
#: Model row columns that hold tuning (ADR-0029 decision 3).
MODEL_TUNING_COLUMNS: tuple[str, ...] = (
    "profile",
    "extra_args",
    "context_size",
    "rope_freq_base",
    "n_gpu_layers",
    "preferred_runner",
)
MODEL_TUNING_EXTRA_KEYS: tuple[str, ...] = ("_enable_thinking", "_vision")
#: Profile keys that leave the profile.
PROFILE_DROP_KEYS: tuple[str, ...] = ("mtp", "device_class", "backend", "quant", "image")
STACK_ROW_TUNING_KEYS: tuple[str, ...] = ("vision", "mtp", "enable_thinking", "server_extra_args")

#: Engines whose runner key a converted profile names explicitly.
ENGINE_RUNNER: dict[str, str] = {
    "flm": "flm",
    "kokoro": "kokoro",
    "moonshine": "moonshine",
    "qwen3tts": "qwen3tts",
    "comfyui": "comfyui",
}
#: Seed profile per non-llama engine (for a profile-less slot).
ENGINE_SEED: dict[str, str] = {
    "flm": "flm",
    "kokoro": "kokoro",
    "moonshine": "moonshine",
    "qwen3tts": "qwen3-tts",
    "comfyui": "comfyui",
}
#: Engines whose profile flags carry the model file (``--model_path``).
MODEL_PATH_ENGINES: frozenset[str] = frozenset({"kokoro", "moonshine", "qwen3tts"})
#: Slot type -> seed profile that carries its mode flag.
TYPE_SEED: dict[str, str] = {"embedding": "embedding", "reranking": "reranking"}
FLM_DEFAULT_CTX = 8192

_BASE_CANON = frozenset({"--host", "--port", "--model", "--alias", "--ctx-size"})
_PLACEMENT_CANON = frozenset({"--n-gpu-layers", "--metrics"})
_MODEL_FACT_CANON = frozenset({"--chat-template-file", "--mmproj", "--jinja"})
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ── flag helpers ─────────────────────────────────────────────────────────────


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


def _join(pairs: list[tuple[str, str, tuple[str, ...]]]) -> str:
    tokens: list[str] = []
    for _canon, flag, values in pairs:
        tokens.append(flag)
        tokens.extend(values)
    return " ".join(shlex.quote(t) for t in tokens)


def slug_name(*parts: str, content: Mapping[str, Any], taken: Mapping[str, Any]) -> str:
    """Deterministic profile name: ``slug(parts)``, or a hashed short form.

    The short form (first 26 characters, ``-``, 5 hex of the content hash) is
    used when the slug is over 32 characters, invalid, or already names a
    profile with different content.
    """
    raw = re.sub(r"[^a-z0-9_-]+", "-", "-".join(p for p in parts if p).lower())
    raw = re.sub(r"-{2,}", "-", raw).strip("-_") or "profile"
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:5]
    if _NAME_RE.match(raw) and (raw not in taken or taken[raw] == content):
        return raw
    return f"{raw[:26].rstrip('-_')}-{digest}"


# ── what a profile gives under the new rules ─────────────────────────────────


@dataclass(frozen=True)
class NewRuleView:
    """A profile read the way the new launch path reads it."""

    flags: tuple[str, ...]
    vision_off: bool
    ctx_size: int | None
    enable_thinking: bool | None


def new_rule_view(profile: frozen.Profile | None) -> NewRuleView:
    """Profile flags minus what the new rules compute elsewhere.

    ``--jinja`` is a model fact, hardware and managed flags belong to the
    slot and base layers, and ``--no-mmproj`` becomes ``vision = "off"``.
    """
    if profile is None:
        return NewRuleView((), False, None, None)
    tokens = shlex.split(profile.flags) if profile.flags.strip() else []
    tokens = [t for t in tokens if t != "--jinja"]
    tokens, _ = frozen._strip(tokens, frozen.HARDWARE_FLAGS | frozen.MANAGED_FLAGS)
    vision_off = "--no-mmproj" in tokens
    tokens = [t for t in tokens if t != "--no-mmproj"]
    raw = profile.raw
    ctx = raw.get("ctx_size") if isinstance(raw.get("ctx_size"), int) else None
    thinking = raw.get("enable_thinking") if isinstance(raw.get("enable_thinking"), bool) else None
    if raw.get("vision") == "off":
        vision_off = True
    return NewRuleView(tuple(tokens), vision_off, ctx, thinking)


def new_rule_flags(text: str) -> tuple[str, bool]:
    """Free flag text under the new rules: ``(flags, vision_off)``."""
    view = new_rule_view(frozen.Profile(name="", flags=text))
    return " ".join(shlex.quote(t) for t in view.flags), view.vision_off


def _tune_key(content: Mapping[str, Any]) -> dict[str, Any]:
    """A generated profile's identity: everything but the informational intent."""
    return {k: v for k, v in content.items() if k != "intent"}


def _existing_content(profile: frozen.Profile | None) -> dict[str, Any] | None:
    """An operator profile's tune in generated-profile form, for dedupe."""
    if profile is None:
        return None
    view = new_rule_view(profile)
    content = {
        "flags": " ".join(shlex.quote(t) for t in view.flags),
        "ctx_size": view.ctx_size,
        "enable_thinking": view.enable_thinking,
        "vision": "off" if view.vision_off else None,
        "runner": profile.runner,
    }
    return {k: v for k, v in content.items() if v not in (None, "", ())}


def _clone_content(profile: frozen.Profile | None) -> dict[str, Any]:
    """The new-shape content of an existing profile, for a generated clone."""
    if profile is None:
        return {"flags": ""}
    flags, vision_off = new_rule_flags(profile.flags)
    return {
        "flags": flags,
        "vision": "off" if vision_off else None,
        "runner": profile.runner,
    }


def default_ctx(model: Mapping[str, Any]) -> int:
    """The new rules' window for a profile with no ``ctx_size``."""
    native = frozen.native_ctx(model)
    return min(native, frozen.CTX_DENSE_CAP) if native else frozen.CTX_SAFE_FALLBACK


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
    """Every ``model`` row as a plain dict, read-only. Missing DB -> empty."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model'"
        ).fetchone()
        if not has_table:
            return {}
        return {str(r["id"]): dict(r) for r in conn.execute("SELECT * FROM model")}
    finally:
        conn.close()


# ── planning ─────────────────────────────────────────────────────────────────


def _model_tuning(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out = {
        c: row[c] for c in MODEL_TUNING_COLUMNS if c in row and row[c] is not None and row[c] != ""
    }
    try:
        extra = json.loads(row.get("extra") or "{}")
    except ValueError:
        extra = {}
    if isinstance(extra, dict):
        out.update({k: extra[k] for k in MODEL_TUNING_EXTRA_KEYS if k in extra})
    return out


class _Planner:
    def __init__(self, inputs: Inputs, policy: InertPolicy) -> None:
        self.inputs = inputs
        self.policy = policy
        self.catalog = frozen.Catalog(inputs.profiles_raw)
        self.plan = ConversionPlan(policy=policy)
        self.bound: set[str] = set()
        # every profile name a generated one must not collide with
        self.taken: dict[str, Any] = {
            n: None for n in self.catalog.names() | set(self.catalog.demoted)
        }

    # -- profiles --------------------------------------------------------

    def _generate(self, base: str, suffix: str, content: dict[str, Any]) -> str:
        """Name and record a generated profile; identical tunes share one.

        Identity is the tune itself: ``intent`` (which names the slot or model
        it came from) is informational and kept from the first occurrence.
        """
        content = {k: v for k, v in content.items() if v not in (None, "", ())}
        tune = _tune_key(content)
        for name, existing in self.plan.profiles.items():
            if _tune_key(existing) == tune:
                return name
        bare = {k: v for k, v in tune.items() if k != "cloned_from"}
        for name in sorted(self.catalog.custom):
            if _existing_content(self.catalog.resolve(name)) == bare:
                return name
        name = slug_name(base, suffix, content=tune, taken=self.taken)
        self.taken[name] = tune
        self.plan.profiles[name] = content
        return name

    def _adopt_demoted(self, name: str) -> None:
        """A slot names a demoted seed the old catalog adopted on demand."""
        if name in self.plan.profiles:
            return
        entry = dict(self.catalog.demoted[name])
        content = {k: v for k, v in entry.items() if k not in PROFILE_DROP_KEYS}
        self.plan.profiles[name] = content
        self.taken[name] = content

    def plan_profile_rewrites(self) -> None:
        for name, entry in self.catalog.custom.items():
            dropped = [k for k in PROFILE_DROP_KEYS if k in entry]
            flags = str(entry.get("flags") or "")
            try:
                tokens = shlex.split(flags) if flags.strip() else []
            except ValueError:
                self.plan.warnings.append(f"profile {name}: flags do not shell-split")
                continue
            if "--jinja" in tokens:
                dropped.append("--jinja (a model fact)")
            if "--no-mmproj" in tokens:
                dropped.append('--no-mmproj -> vision = "off"')
            _, hw = frozen._strip(tokens, frozen.HARDWARE_FLAGS | frozen.MANAGED_FLAGS)
            if hw:
                dropped.append(f"{' '.join(hw)} (slot/base flags, stripped at launch today)")
            if dropped:
                self.plan.profile_rewrites[name] = dropped
        raw = self.inputs.profiles_raw or {}
        for marker in ("legacy_seeds_migrated", "legacy_seeds_adopted"):
            if marker in raw:
                self.plan.profile_rewrites.setdefault("(profiles.toml)", []).append(marker)

    # -- slots -----------------------------------------------------------

    def plan_slots(self) -> None:
        for fname, raw, error in self.inputs.slots:
            stem = fname[:-5]
            if raw is None:
                self.plan.slots.append(
                    SlotPlan(file=fname, name=stem, status="blocked", error=f"unreadable: {error}")
                )
                continue
            slot = frozen.hoist_slot(raw, stem)
            try:
                self.plan.slots.append(self._plan_slot(fname, raw, slot))
            except (ValueError, Hal0Error) as exc:
                self.plan.slots.append(
                    SlotPlan(
                        file=fname,
                        name=str(slot.get("name") or stem),
                        status="blocked",
                        profile_before=str(slot.get("profile") or ""),
                        error=str(exc),
                    )
                )

    def _shape_keys(self, raw: Mapping[str, Any], slot: Mapping[str, Any], sp: SlotPlan) -> None:
        """Keys that leave the slot, and inert values they carried."""
        for key in SLOT_TUNING_KEYS:
            if key in slot:
                sp.drop_keys.append(key)
        if "backend" in slot and not slot.get("device"):
            from hal0.model_meta import map_backend_to_device

            sp.set_keys["device"] = map_backend_to_device(str(slot["backend"]))
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        for key in MODEL_TABLE_DROP:
            if key in table:
                sp.drop_keys.append(f"[model].{key}")
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
            sp.drop_keys.append("image")

    def _plan_slot(self, fname: str, raw: Mapping[str, Any], slot: dict[str, Any]) -> SlotPlan:
        name = str(slot.get("name") or fname[:-5])
        engine = frozen.engine_for(slot, self.catalog)
        named = str(slot.get("profile") or "")
        sp = SlotPlan(file=fname, name=name, engine=engine, profile_before=named)
        sp.profile_after = named
        self._shape_keys(raw, slot, sp)
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        model_id = str(table.get("default") or "")
        row = self.inputs.models.get(model_id) if model_id else None
        if model_id:
            self.bound.add(model_id)
        model = (
            frozen.model_info_from_row(row)
            if row
            else ({"_model_key": model_id} if model_id else {})
        )
        model_tuning = _model_tuning(row)
        resolved = self.catalog.resolve(named) if named else None
        demoted_ref = bool(named) and named not in self.catalog.names() and resolved is not None
        if demoted_ref:
            self._adopt_demoted(named)
            sp.notes.append(f"profile {named!r} exists only as a demoted seed; adopted as custom")

        # Old shape? convert : leave. A slot with no tuning keys, an existing
        # profile and an untuned model already launches what the new rules
        # give it, so it is left alone (this is what makes a rerun a no-op).
        old_shape = bool(sp.drop_keys or sp.set_keys or model_tuning or resolved is None)
        if not old_shape:
            return sp

        if engine != frozen.LLAMA:
            self._plan_other_engine(slot, sp, model, model_tuning, row)
        elif not model_id:
            self._plan_modelless(slot, sp)
        else:
            self._plan_llama(slot, sp, model)
        if sp.status != "blocked":
            sp.status = "convert"
        return sp

    def _plan_llama(self, slot: dict[str, Any], sp: SlotPlan, model: Mapping[str, Any]) -> None:
        launch = frozen.evaluate(slot, model, self.catalog)
        sp.argv_today = launch.argv
        named = sp.profile_before
        launched = launch.profile_launched
        if launch.profile_missing or not named:
            implied = TYPE_SEED.get(str(slot.get("type") or ""), "")
            why = (
                f"profile {named!r} does not exist (launches {launched!r})"
                if named
                else f"no profile (launches {launched!r})"
            )
            if implied and implied != launched:
                sp.launch_changes.append(f"{why}; now {implied!r}, which carries the mode flag")
                launch = frozen.evaluate({**slot, "profile": implied}, model, self.catalog)
                launched = implied
            else:
                sp.notes.append(f"{why}; re-pointed to what launches")

        bundle = _pairs(shlex.split(frozen.mtp_bundle(launch.mtp_backend)))
        bundle_values = {canon: values for canon, _flag, values in bundle}
        tune: list[tuple[str, str, tuple[str, ...]]] = []
        for canon, flag, values in _pairs(launch.argv):
            if canon in _BASE_CANON or canon in _PLACEMENT_CANON or canon in _MODEL_FACT_CANON:
                continue
            if canon == "--threads":
                # A thread count that reached the argv from free text moves to
                # the slot's placement field when the slot sets none itself.
                own = slot.get("threads")
                if not (own is not None and int(own) > 0) and values:
                    sp.set_keys["threads"] = int(values[0])
                continue
            if canon == "--device":
                raise ValueError(f"{flag} in the launch tune cannot move to a profile")
            # The new launch path adds the MTP bundle itself (and the draft
            # device from the slot); keep only bundle values the tune overrode.
            if (
                launch.mtp
                and canon in bundle_values
                and (canon == "--spec-draft-device" or bundle_values[canon] == values)
            ):
                continue
            tune.append((canon, flag, values))
        if launch.mtp:
            backend = frozen._backend_and_class(slot, frozen.Profile(name=""))[0]
            if (backend or "rocm") != "rocm":
                sp.launch_changes.append(f"MTP draft device ROCm0 -> the {backend} device")

        tokens = shlex.split(_join(tune))
        vision_off = "--no-mmproj" in tokens
        tokens = [t for t in tokens if t != "--no-mmproj"]
        if vision_off and model.get("mmproj") and launch.mmproj:
            sp.notes.append(
                "--no-mmproj and --mmproj were both emitted; llama.cpp drops the "
                'projector when --no-mmproj is set, so vision was off: vision = "off"'
            )
        defaults = model.get("defaults") if isinstance(model.get("defaults"), Mapping) else {}
        if defaults.get("vision") is False:
            vision_off = True

        self._note_unhonoured_ceiling(slot, launch, sp)
        adds, sp.inert = inert_additions(self.policy, sp.inert)
        tokens += list(adds.flags)
        vision_off = vision_off or adds.vision_off
        ctx = adds.ctx_size or launch.ctx
        thinking = (
            adds.enable_thinking if adds.enable_thinking is not None else launch.enable_thinking
        )
        target = {
            "flags": " ".join(shlex.quote(t) for t in tokens),
            "ctx_size": ctx if ctx != default_ctx(model) else None,
            "enable_thinking": thinking,
            "vision": "off" if vision_off else None,
        }
        base_profile = self.catalog.resolve(launched)
        view = new_rule_view(base_profile)
        same = (
            effective_map(view.flags) == effective_map(tokens)
            and view.vision_off == vision_off
            and (view.ctx_size or default_ctx(model)) == ctx
            and view.enable_thinking == thinking
        )
        if same and (launched in self.catalog.names() or launched in self.plan.profiles):
            sp.profile_after = launched
            return
        runner = base_profile.runner if base_profile is not None else None
        content = {
            **target,
            "runner": runner,
            "cloned_from": launched,
            "intent": f"Converted: slot {sp.name}, model {model.get('_model_key')}",
        }
        sp.profile_after = self._generate(launched, str(model.get("_model_key") or ""), content)
        sp.generated = sp.profile_after in self.plan.profiles

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
            value = None
        if value is None or value <= launch.ctx:
            return
        sp.inert.append(InertValue("slot", "context_size", value))
        sp.notes.append(f"ceiling {value} is not honoured today (launches {launch.ctx})")

    def _plan_modelless(self, slot: Mapping[str, Any], sp: SlotPlan) -> None:
        named = sp.profile_before
        resolved = self.catalog.resolve(named) if named else None
        base = named
        if resolved is None:
            base = TYPE_SEED.get(str(slot.get("type") or ""), "chat")
            sp.notes.append(
                f"profile {named!r} does not exist; re-pointed to {base!r}"
                if named
                else f"no profile; assigned {base!r}"
            )
        sp.profile_after = base
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        try:
            ceiling = int(table["context_size"]) if table.get("context_size") is not None else None
        except (TypeError, ValueError):
            ceiling = None
        if ceiling is None or ceiling == frozen.CTX_DENSE_CAP:
            return
        base_profile = self.catalog.resolve(base)
        content = {
            **_clone_content(base_profile),
            "ctx_size": ceiling,
            "cloned_from": base,
            "intent": f"Converted: slot {sp.name} context {ceiling}",
        }
        sp.profile_after = self._generate(base, f"ctx{ceiling}", content)
        sp.generated = sp.profile_after in self.plan.profiles

    def _plan_other_engine(
        self,
        slot: Mapping[str, Any],
        sp: SlotPlan,
        model: Mapping[str, Any],
        model_tuning: Mapping[str, Any],
        row: Mapping[str, Any] | None,
    ) -> None:
        engine = sp.engine
        named = sp.profile_before
        resolved = self.catalog.resolve(named) if named else None
        base = named
        if resolved is None:
            base = ENGINE_SEED.get(engine, named)
            sp.launch_changes.append(
                f"profile {named or '(none)'!r} resolves to nothing; now {base!r}"
            )
            resolved = self.catalog.resolve(base)
        sp.profile_after = base
        if model_tuning.get("extra_args"):
            sp.inert.append(InertValue("model", "extra_args", model_tuning["extra_args"]))
        table = slot.get("model") if isinstance(slot.get("model"), Mapping) else {}
        live: dict[str, Any] = {}
        thinking = model_tuning.get("_enable_thinking")
        if isinstance(thinking, bool):
            live["enable_thinking"] = thinking
        if engine == "flm":
            # providers.flm.build_env: the slot ctx is FLM's real window, and a
            # pre-container ``[defaults]`` table still supplies it and load_*.
            old_defaults = slot.get("defaults") if isinstance(slot.get("defaults"), Mapping) else {}
            ctx = (
                table.get("context_size")
                or slot.get("ctx_size")
                or old_defaults.get("context_size")
            )
            if ctx and int(ctx) != FLM_DEFAULT_CTX:
                live["ctx_size"] = int(ctx)
            if slot.get("npu") is None and old_defaults:
                sp.set_keys["npu"] = {
                    "asr": bool(old_defaults.get("load_asr")),
                    "embed": bool(old_defaults.get("load_embed")),
                    "chat": True,
                }
        elif table.get("context_size") is not None:
            sp.inert.append(InertValue("slot", "context_size", table["context_size"]))
        if engine in MODEL_PATH_ENGINES and resolved is not None:
            self._check_model_path(sp, resolved, model, row)
        if live:
            content = {
                **_clone_content(resolved),
                "runner": ENGINE_RUNNER.get(engine),
                "cloned_from": base,
                "intent": f"Converted: slot {sp.name}, model {model.get('_model_key')}",
                **live,
            }
            sp.profile_after = self._generate(base, str(model.get("_model_key") or ""), content)
            sp.generated = sp.profile_after in self.plan.profiles

    def _check_model_path(
        self,
        sp: SlotPlan,
        profile: frozen.Profile,
        model: Mapping[str, Any],
        row: Mapping[str, Any] | None,
    ) -> None:
        try:
            tokens = shlex.split(profile.flags)
        except ValueError:
            return
        if "--model_path" not in tokens or tokens.index("--model_path") + 1 >= len(tokens):
            return
        ppath = tokens[tokens.index("--model_path") + 1]
        model_id = str(model.get("_model_key") or "")
        if row is None:
            if model_id:
                self.plan.models.append(ModelPlan(model_id=model_id, register_path=ppath))
                sp.notes.append(f"model {model_id!r} has no row; registered from {ppath}")
            return
        if row.get("path") != ppath:
            sp.status = "blocked"
            sp.error = (
                f"profile {profile.name!r} --model_path {ppath} differs from model "
                f"{model_id!r} path {row.get('path')}"
            )

    # -- models and stacks -----------------------------------------------

    def plan_models(self) -> None:
        for model_id, row in sorted(self.inputs.models.items()):
            tuning = _model_tuning(row)
            if not tuning:
                continue
            mp = ModelPlan(model_id=model_id, clear=tuning)
            if model_id not in self.bound:
                live = {
                    "flags": tuning.get("extra_args"),
                    "ctx_size": tuning.get("context_size"),
                    "enable_thinking": tuning.get("_enable_thinking"),
                    "vision": "off" if tuning.get("_vision") is False else None,
                }
                if any(v not in (None, "") for v in live.values()):
                    base = str(tuning.get("profile") or "")
                    base_profile = self.catalog.resolve(base) if base else None
                    if base_profile is None:
                        if base:
                            mp.notes.append(f"preferred profile {base!r} does not exist")
                        base = "chat"
                        base_profile = self.catalog.resolve(base)
                    if live["flags"]:
                        flags, vision_off = new_rule_flags(str(live["flags"]))
                        live["flags"] = flags
                        if vision_off:
                            live["vision"] = "off"
                    else:
                        # An untuned model launched with its slot profile's flags.
                        cloned = _clone_content(base_profile)
                        live["flags"] = cloned.get("flags", "")
                        live["vision"] = live["vision"] or cloned.get("vision")
                    content = {
                        **live,
                        "cloned_from": base,
                        "intent": f"Converted: model {model_id} (not bound to a slot)",
                    }
                    mp.generated_profile = self._generate(base, model_id, content)
            self.plan.models.append(mp)

    def plan_stacks(self) -> None:
        stacks = (self.inputs.stacks_raw or {}).get("stack")
        if not isinstance(stacks, Mapping):
            return
        for stack_name, stack in sorted(stacks.items()):
            rows = stack.get("slots") if isinstance(stack, Mapping) else None
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, Mapping):
                    continue
                present = [k for k in STACK_ROW_TUNING_KEYS if k in row]
                if not present:
                    continue
                rp = StackRowPlan(stack=str(stack_name), slot=str(row.get("slot") or ""))
                rp.drop_keys = present
                # ``vision = false`` is the StackSlotEntry default every saved row
                # carries, not a choice: the key is dropped without a report line.
                items = [
                    InertValue("stack", k, row[k])
                    for k in present
                    if row[k] not in (None, "") and not (k == "vision" and row[k] is False)
                ]
                adds, rp.inert = inert_additions(self.policy, items)
                if adds.flags or adds.vision_off or adds.enable_thinking is not None:
                    base = str(row.get("profile") or "chat")
                    cloned = _clone_content(self.catalog.resolve(base))
                    extra = " ".join(shlex.quote(t) for t in adds.flags)
                    content = {
                        **cloned,
                        "flags": f"{cloned.get('flags', '')} {extra}".strip(),
                        "vision": "off" if adds.vision_off else cloned.get("vision"),
                        "enable_thinking": adds.enable_thinking,
                        "cloned_from": base,
                        "intent": f"Converted: stack {stack_name} row {rp.slot}",
                    }
                    rp.profile_after = self._generate(base, f"{stack_name}-{rp.slot}", content)
                self.plan.stacks.append(rp)


def plan_conversion(
    inputs: Inputs | None = None,
    *,
    policy: InertPolicy = DEFAULT_INERT_POLICY,
) -> ConversionPlan:
    """Plan the conversion of this install (or of ``inputs``). Writes nothing."""
    planner = _Planner(inputs or read_inputs(), policy)
    planner.plan_profile_rewrites()
    planner.plan_slots()
    planner.plan_models()
    planner.plan_stacks()
    return planner.plan


__all__ = [
    "DEFAULT_INERT_POLICY",
    "INERT_POLICIES",
    "ConversionPlan",
    "InertAdditions",
    "InertPolicy",
    "InertValue",
    "Inputs",
    "ModelPlan",
    "NewRuleView",
    "ProfileMigrationError",
    "SlotPlan",
    "StackRowPlan",
    "default_ctx",
    "effective_map",
    "inert_additions",
    "new_rule_view",
    "plan_conversion",
    "read_inputs",
    "read_model_rows",
    "slug_name",
]
