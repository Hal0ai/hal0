"""planner.py — the pure-function planner (DESIGN §2, §6).

The planner is the whole reason auto-update is "set and forget": it takes a
suite + the live model registry + the store and returns the list of *stale*
cells to run. It touches no GPU, writes nothing, and is deterministic given its
inputs — so it runs anywhere, any time, and (DESIGN §5.4) makes the runner
*resumable by construction*: re-planning after a crash recomputes exactly the
cells that still lack an ok record. There is no queue state to persist.

Staleness (DESIGN §6) is a two-clause set-difference against the store:

    A cell is stale iff
      (1) no ok record exists for its cell_key  -- never measured, which
          INCLUDES the case where any identity input changed (new model digest,
          new runner-image digest, changed resolved argv, new llama.cpp build,
          new depth/sampler): the key is simply absent from the store; OR
      (2) the newest ok record for its cell_key is older than the suite's
          staleness.max_age_days.

Because resolved argv/env/digests are inside cell_key (schema.py), clause (1)
does all the provenance-drift work for free — a merged flag PR invalidates
exactly the cells it changed, no re-bench checklist.
"""

from __future__ import annotations

import shlex
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from . import adapters, harness
from .adapters import GUIDELLM_PIN, LLAMA_BENCHY_PIN
from .adapters.guidellm import GUIDELLM_BIN
from .adapters.llama_benchy import LLAMA_BENCHY_BIN
from .schema import Config, Engine, Identity, Model, Workload, cell_key
from .store import Store
from .suites import Suite

# hal0-api base — the registry lives behind the same :8080 service benchlab
# consumes everywhere (DESIGN preamble). Overridable for tests/other boxes.
DEFAULT_API = "http://127.0.0.1:8080"

# Endpoint assumption (documented per task): the model registry is read via
#   GET {api}/api/models
# returning a JSON list (or {"models": [...]}) of registry entries, each with at
# least: id, caps (list[str]), installed (bool), gguf, sha256, quant,
# size_bytes, and a default lane hint. This is the same public registry surface
# hal0's dashboard uses; benchlab only ever reads it.
REGISTRY_PATH = "/api/models"


# Tuning flags a config variant may set — the seam's llama-bench flag
# whitelist (hal0-benchctl exec) MINUS the ones the runner controls itself
# (-p/-n/-d/-r). A
# variant flag outside this set is rejected at plan time (fail fast, before the
# seam does).
_TUNE_FLAGS = frozenset({"-b", "-ub", "-ngl", "-fa", "-ctk", "-ctv", "-t", "-lm", "-pg"})

# Prompt length for the pp measurement at every depth (the community-standard
# pp512). The depth axis is the KV fill (llama-bench -d): a pp cell measures
# prefilling _PP_PROMPT tokens ON TOP OF `depth` tokens of context, a tg cell
# measures decoding n_gen tokens at that same fill.
_PP_PROMPT = 512

# The measurement kinds the runner can execute: Tier A (llama-bench via the
# seam), the GuideLLM/llama-benchy OSS adapters (bench Phase 3), and the
# remaining server_ab modes. plan() rejects a suite kind outside this set
# loudly — the old behavior silently fell back to server_ab `--mode ab`,
# which mislabeled the record.
#
# "chat" drives GuideLLM (adapters/guidellm.py), not server_ab, as of Phase 3
# (docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md design
# decision 1). "http_pp"/"http_tg" are NEW Phase-3 kinds: llama-bench
# vocabulary (pp/tg x depth) driven over a live slot's HTTP endpoint via
# llama-benchy (adapters/llama_benchy.py) — no suite ships them yet, but the
# planner must accept them so a suite can opt in later (design decision 3).
KNOWN_KINDS = frozenset({"pp", "tg", "chat", "reuse", "embed", "rerank", "http_pp", "http_tg"})

# Tier-A kinds — the only ones that ever reach harness.lane_specs()[cell.lane]
# (runner._tier_a_cmd / _tier_a_record). A Tier-B/C cell's lane is display-only
# (server_ab/adapters never look it up), so only a Tier-A cell's resolved lane
# is validated against the harness's known lanes (finding 5). "http_pp"/
# "http_tg" measure a live HTTP slot like chat/reuse/embed/rerank do — they
# are Tier-B-like (unprivileged, no exclusive seam), not Tier A.
_TIER_A_KINDS = frozenset({"pp", "tg"})

# kind -> (console-script binary, pin string) for the two Phase-3 OSS
# adapters. plan() rejects a suite whose cells need a tool that isn't
# installed — loudly, at plan time, naming the pin — rather than letting the
# runner discover the gap per-cell mid-session (design decision 5). embed/
# rerank/reuse (still server_ab) and pp/tg (the seam) need no external tool
# on the unprivileged runner's PATH, so they are not in this map.
_ADAPTER_TOOL_FOR_KIND: dict[str, tuple[str, str]] = {
    "chat": (GUIDELLM_BIN, GUIDELLM_PIN),
    "http_pp": (LLAMA_BENCHY_BIN, LLAMA_BENCHY_PIN),
    "http_tg": (LLAMA_BENCHY_BIN, LLAMA_BENCHY_PIN),
}

# cell kind -> the schema.Engine.kind an adapter-measured cell reports. Tier-A
# (pp/tg) and the remaining server_ab kinds keep their pre-Phase-3 values.
_ENGINE_KIND_FOR_KIND: dict[str, str] = {
    "pp": "llama-bench",
    "tg": "llama-bench",
    "chat": "guidellm",
    "http_pp": "llama-benchy",
    "http_tg": "llama-benchy",
}


@dataclass
class Cell:
    """One planned unit of work: a fully-resolved identity + the suite context
    the runner needs (reps, exclusivity, config flags) and the staleness reason
    (for `benchlab plan` output and record ordering)."""

    cell_key: str
    identity: Identity
    suite_id: str
    kind: str
    reps: int
    exclusive: bool
    priority: int
    reason: str  # "never-measured" | "stale:>{n}d"
    model_id: str
    lane: str
    depth: int
    flags: dict = field(default_factory=dict)  # config-variant tuning flags for the seam
    config_label: str = "default"
    # #1773: the HF repo id GuideLLM's --tokenizer resolves for a "chat" cell
    # (see _resolve_tokenizer). Empty for every other kind — display/runner
    # plumbing only, NOT part of cell_key (a tokenizer swap doesn't change
    # what was measured, just what dataset built the request).
    tokenizer: str = ""


def fetch_registry_models(api: str = DEFAULT_API, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Read the model registry from hal0-api ``GET /api/models``.

    Kept a small standalone client (not a class) so tests inject a plain list
    instead and never hit the network. Accepts either a bare JSON list or a
    ``{"models": [...]}`` / ``{"data": [...]}`` envelope, matching the tolerant
    shape server_ab.py uses for ``/api/slots``.
    """
    import json

    from hal0.service_identity import attach_service_auth, open_url

    # #2478: on an auth-required box /api/models is a CLIENT route, so present
    # the box service key (env, then /etc/hal0/api.env — readable by the hal0
    # uid, ADR-0002). Client tier first: nothing the bench reads needs admin.
    # Attached only for a loopback --api, and never forwarded on a redirect.
    # open_url keeps a loopback request away from the proxy environment (#2505).
    req = urllib.request.Request(f"{api.rstrip('/')}{REGISTRY_PATH}", method="GET")
    attach_service_auth(req, prefer="client")
    with open_url(req, timeout=timeout) as resp:
        payload = resp.read()
    data = json.loads(payload) if payload else []
    if isinstance(data, dict):
        return data.get("models") or data.get("data") or []
    return data


class _ModelDictRegistry:
    """A registry over the planner's own model dicts, for the native-window lookup."""

    def __init__(self, models: list[dict[str, Any]]) -> None:
        self._by_id = {str(m["id"]): m for m in models if m.get("id")}

    def get(self, model_id: str) -> dict[str, Any]:
        return self._by_id[model_id]


def _read_slot_cfgs() -> list[dict[str, Any]]:
    """Every slot's flat config from /etc/hal0/slots, sorted by slot name.

    Read straight from the TOMLs (the bench CLI runs outside the API process)
    through the loader's own flattening, so the dict is the shape the launch
    path's resolvers take."""
    from hal0.config import paths
    from hal0.config.loader import _flatten_slot_toml, _read_toml

    directory = paths.slots_config_dir()
    if not directory.exists():
        return []
    cfgs: list[dict[str, Any]] = []
    for toml_path in sorted(directory.glob("*.toml")):
        try:
            raw = _read_toml(toml_path)
            name = str(raw.get("name") or (raw.get("slot") or {}).get("name") or toml_path.stem)
            cfgs.append(_flatten_slot_toml(raw, slot_name=name))
        except Exception as exc:  # one unreadable slot file never blinds the planner
            print(f"[plan] skipping slot file {toml_path}: {exc}", file=sys.stderr)
    return sorted(cfgs, key=lambda c: str(c.get("name") or ""))


def attach_slot_profiles(
    models: list[dict[str, Any]], slots: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Return ``models`` with each served model's slot profile attached (ADR-0029).

    A model row holds file facts only; the tune a cell is measured under is the
    profile of the slot that runs the model. For every model some slot names
    (``[model].default``) this sets ``model["profile"]`` from the same
    resolvers the launch path uses —
    :func:`hal0.providers.container.resolve_slot_profile` for the tune and
    :func:`hal0.providers.container.slot_effective_context` for the window
    (``ctx_size`` is the EFFECTIVE window, not just the profile's raw value).
    When several slots serve one model the first by slot name whose profile
    resolves wins. A model no slot serves, a profile that does not resolve, or
    an entry that already carries its own ``profile`` is left as it was (the
    planner then benches the runner's defaults). Never raises: the planner
    must keep working on a box whose config it cannot read.

    ``slots`` injects the flat slot configs (tests); ``None`` reads them from
    disk. The input list and its dicts are not mutated.
    """
    try:
        from hal0.providers.container import resolve_slot_profile, slot_effective_context

        slot_cfgs = _read_slot_cfgs() if slots is None else slots
        registry = _ModelDictRegistry(models)
        out: list[dict[str, Any]] = []
        for model in models:
            attached = model
            if model.get("id") and not model.get("profile"):
                mid = str(model["id"])
                for cfg in slot_cfgs:
                    table = cfg.get("model")
                    if not isinstance(table, dict) or str(table.get("default") or "") != mid:
                        continue
                    try:
                        profile = resolve_slot_profile(cfg)
                    except Exception:
                        continue  # a slot naming a missing profile cannot be benched as-is
                    ctx, ctx_source = slot_effective_context(cfg, registry)
                    attached = {
                        **model,
                        "profile": {
                            "slot": str(cfg.get("name") or ""),
                            "name": str(getattr(profile, "name", "") or ""),
                            "flags": str(getattr(profile, "flags", "") or ""),
                            "ctx_size": ctx,
                            "ctx_source": ctx_source,
                            "enable_thinking": getattr(profile, "enable_thinking", None),
                            "vision": getattr(profile, "vision", "auto"),
                            "speculation": getattr(profile, "speculation", "auto"),
                            "runner": getattr(profile, "runner", None),
                        },
                    }
                    break
            out.append(attached)
        return out
    except Exception as exc:
        print(f"[plan] could not attach slot profiles ({exc}); planning without", file=sys.stderr)
        return models


# hal0 /api/models backend token -> benchlab lane token. The registry reports a
# model's runner backend as "vulkan"/"rocm"; benchlab's lanes are "vulkan_radv"/
# "rocm" (the lane names harness.lane_specs() defines).
#
# #1888 redirected the registry's bare "vulkan" hint to the **rocm** lane, and
# #1948 LEAVES IT THERE deliberately, for a reason that outlived the defect:
# the hint is a MODEL label ("this GGUF runs on llama.cpp"), not a statement
# about which device a slot was created on. Every AMD GPU lane shares one
# runner image, so that label is satisfied by either lane — and rocm is the
# lane with the long trend history, so it is the right thing to bench when
# nothing more specific was asked for. NOT a speed claim: the §3-C matrix
# measures Vulkan ahead on both metrics (+13.96% prefill, +20.45% decode).
# Continuity of an existing series is the reason.
#
# An explicit "vulkan_radv" resolves to itself and is a supported lane again
# whenever the pinned runner image can serve it (``harness.lane_is_supported``),
# so a suite that wants the Vulkan numbers simply names it.
#
# That continuity argument only holds on a box that HAS a ROCm lane (#2511): on
# a Vulkan-only or CPU-only box a rocm cell dies at once, so the bare "vulkan"
# hint and the no-hint fallback resolve through :func:`_preferred_gpu_lane`,
# which keeps rocm where ``rocm_lane_present()`` says it exists and otherwise
# picks a lane the box supports. The table below is the ROCm-present answer.
_BACKEND_TO_LANE = {"vulkan": "rocm", "vulkan_radv": "vulkan_radv", "rocm": "rocm"}


#: Sentinel "lane": the box has no usable GPU lane (#2511). Never a real lane.
NO_GPU_LANE = "no-gpu-lane"


def _preferred_gpu_lane() -> str:
    """The lane an unspecific request (bare ``vulkan`` hint, or no hint) benches.

    ``rocm`` where the box has a ROCm lane (device-node check shared with the
    install seed and the picker, #2452) so existing trend series continue;
    otherwise ``vulkan_radv`` when the pinned runner can serve it and a render
    node is usable (``harness.vulkan_lane_usable``). With neither, returns
    :data:`NO_GPU_LANE`, which :func:`_lane_is_plannable` skips loudly instead
    of planning cells that die (#2511). Cells run rootful via hal0-benchctl, so
    both probes use ``for_uid=0``.
    """
    from hal0.providers._gpu import rocm_lane_present

    if rocm_lane_present(for_uid=0):
        return "rocm"
    if harness.vulkan_lane_usable():
        return "vulkan_radv"
    return NO_GPU_LANE


def _model_caps(m: dict[str, Any]) -> set[str]:
    """The capability set a suite selector matches against.

    hal0's real ``/api/models`` (verified on-box 2026-07-05) splits a model's
    labels across ``capabilities`` (e.g. ["chat","vision"]) and ``tags`` (e.g.
    "coder","tool-calling","mtp"). The roster selector wants the chat+coder
    roster, and "coder" lives in ``tags`` — so we match against the UNION of
    ``capabilities`` + ``tags`` (+ ``type``). ``caps`` is also accepted for the
    synthetic registry entries the unit tests inject.

    The freeform lists are unmaintained in practice: on a 49-model box only a
    handful carry a semantic label, and this set is what stamps
    ``identity.model.caps`` onto every benchmark record — which is where the
    public leaderboard's capability pills come from (#1823). So the TYPED
    fields are folded in too, following the ``mtp`` fact precedent below:
    they are the surface an operator actually edits (``PUT /api/models``),
    so a typed edit should reach the roster without also hand-editing a
    string list.

    Only unambiguous positives are folded. Every typed flag here is
    tri-state, and ``None`` means "unset / decided elsewhere", never "this
    model lacks the capability" — so ``None`` and ``False`` both add nothing
    rather than asserting an absence.

    Widening this set is safe for selection: suites match with ``caps_any``,
    so extra labels can only make a model match more selectors, never fewer.
    """
    caps: set[str] = set()
    for field_name in ("caps", "capabilities", "tags"):
        caps.update(m.get(field_name) or [])
    if m.get("type"):
        caps.add(m["type"])
    # ``mtp`` is the model fact "the file has MTP heads" — surface it into
    # the match set so rosters keyed on "mtp" keep selecting those rows.
    if m.get("mtp") is True:
        caps.add("mtp")
    # Vision is the projector: a model that carries an mmproj can see. Whether
    # a slot loads it is its profile's `vision`, a tune — not a model fact.
    if m.get("mmproj"):
        caps.add("vision")
    # ModelCapabilities.tool_calling — the one typed-bool surface for the
    # omni-router's tool-call gate.
    if (m.get("capability_flags") or {}).get("tool_calling") is True:
        caps.add("tool-calling")
    # The profile that tunes this model (attached by the caller, see
    # ``_resolve_profile``) with enable_thinking True runs it thinking ON, so
    # it demonstrably supports it. None is "unset", not incapacity.
    if (m.get("profile") or {}).get("enable_thinking") is True:
        caps.add("reasoning")
    return caps


def _resolve_tokenizer(m: dict[str, Any]) -> str | None:
    """The HF repo id a ``chat`` cell's GuideLLM run can trust for
    ``--tokenizer`` (issue #1773), or ``None`` if this registry entry has
    none.

    Preference order: an explicit/pull-defaulted ``tokenizer_repo``
    (registry/model.py's ``Model.tokenizer_repo`` — settable by an
    operator, defaulted from ``hf_repo`` by the pull path when that linkage
    already exists), falling back to the model's own ``hf_repo`` directly
    (the same repo the GGUF was pulled from, when no override was needed).
    A local-only model id/path is never a trustworthy HF repo id on its
    own — that is exactly the bug this function exists to gate at plan
    time instead of inside a mid-session GuideLLM crash."""
    tok = str(m.get("tokenizer_repo") or "").strip()
    if tok:
        return tok
    hf_repo = str(m.get("hf_repo") or "").strip()
    return hf_repo or None


def _is_tier_a_incompatible(m: dict[str, Any]) -> str | None:
    """Return a reason string if this registry entry can't be a llama-bench
    (pp/tg) roster subject, else None.

    WHY this exists (verified on-box 2026-07-05, a hard reality the DESIGN §4
    selector didn't anticipate): hal0's ``/api/models`` auto-scan labels EVERY
    installed asset ``type=chat`` / ``capabilities=['chat']`` — including SD-XL /
    FLUX / LTX diffusion checkpoints, a TTS tokenizer, and rerankers. Selecting
    on caps alone would put 29 "models" on the roster, most of which llama-bench
    can't measure. We filter to generative GGUFs:
      * a ``.gguf`` path (excludes ``.safetensors`` diffusion/TTS/comfyui assets);
      * not an embedder/reranker (correctly-tagged ones carry the embed/rerank
        cap; mislabeled ones — e.g. jina-reranker reporting cap 'chat' — are
        caught by name).
    A missing path (the synthetic registry entries the unit tests inject) is NOT
    filtered — the tests have no gguf and must still plan."""
    path = str(m.get("gguf") or m.get("path") or "")
    if path and not path.endswith(".gguf"):
        return "not-a-gguf"
    if _model_caps(m) & {"embed", "rerank", "embedding"}:
        return "embed/rerank"
    hay = f"{m.get('id', '')} {m.get('name', '')}".lower()
    if "rerank" in hay or "embedder" in hay or "embedding" in hay:
        return "embed/rerank-by-name"
    return None


def _select_models(suite: Suite, registry_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the suite [selector] to the registry (DESIGN §4). Explicit
    include/exclude override caps/installed filters. For a Tier-A suite (pp/tg
    cells) an extra compatibility gate drops non-generative-GGUF assets that
    hal0's registry mislabels as chat (see ``_is_tier_a_incompatible``)."""
    sel = suite.selector
    if sel.include_only and not sel.include:
        # An operator-curated suite with an unset include list selects NOTHING
        # (suites.Selector.include_only) — never "everything by accident".
        print(
            f"[plan] suite {suite.id!r}: include_only with an empty include — selecting no models",
            file=sys.stderr,
        )
        return []
    tier_a = bool({"pp", "tg"}.intersection(suite.cells.kinds))
    chosen: list[dict[str, Any]] = []
    for m in registry_models:
        mid = m.get("id")
        if not mid:
            continue
        if sel.exclude and mid in sel.exclude:
            continue
        explicit = bool(sel.include and mid in sel.include)
        if sel.include and not explicit:
            continue
        if not explicit:
            # caps/installed filters only apply to the non-explicit path; an
            # explicit include is an operator override.
            if sel.installed and not m.get("installed", False):
                continue
            if sel.caps_any and not _model_caps(m).intersection(sel.caps_any):
                continue
            if tier_a and _is_tier_a_incompatible(m):
                continue
        chosen.append(m)
    return chosen


def _lane_is_plannable(
    lane: str,
    *,
    suite_id: str,
    warned: set[str] | None = None,
) -> bool:
    """May cells be planned for ``lane``? Explains itself on stderr if not.

    Split out of :func:`plan_cells` so the skip decision and its explanation
    live together and can be tested directly, without standing up a whole
    suite plus a model roster to observe one branch.

    ``warned`` de-dupes the message to once per lane per plan; pass ``None``
    for a one-shot check that always explains.
    """
    if lane == NO_GPU_LANE:
        if warned is None or lane not in warned:
            if warned is not None:
                warned.add(lane)
            print(
                f"WARNING: suite {suite_id!r} asked for the default lane, but there is "
                "no usable GPU lane: no ROCm lane and no Vulkan-capable render "
                "node/image — SKIPPING those cells. Name a lane explicitly to override.",
                file=sys.stderr,
            )
        return False
    if harness.lane_is_supported(lane):
        return True
    if warned is None or lane not in warned:
        if warned is not None:
            warned.add(lane)
        print(
            f"WARNING: suite {suite_id!r} names the lane {lane!r}, which this install "
            "cannot produce trustworthy numbers for — SKIPPING those cells. "
            "The pinned runner image's Vulkan backend is not validated: on the "
            "ade07ba lineage it emits invalid tokens for every model (#1888), and "
            "llama-bench counts tokens without reading them, so the sweep would "
            "publish throughput for output that is not language. Repin the runner "
            "to a Vulkan-validated image to enable this lane.",
            file=sys.stderr,
        )
    return False


def _resolve_lane(model: dict[str, Any], lane: str) -> str:
    """Map the matrix lane token to a concrete lane. ``"default"`` resolves to
    the model's preferred lane: the test hint (``default_lane``/``lane``) or, on a
    real box, the first entry of the registry's ``backends`` list mapped through
    ``_BACKEND_TO_LANE``. The bare "vulkan" hint and the no-hint fallback go
    through :func:`_preferred_gpu_lane` (rocm only where a ROCm lane exists,
    #2511). An explicit lane token, or an explicit rocm/vulkan_radv hint, is
    returned as given."""
    if lane != "default":
        return lane
    hint = model.get("default_lane") or model.get("lane")
    if hint:
        return _mapped_lane(hint)
    for backend in model.get("backends") or []:
        if backend in _BACKEND_TO_LANE:
            return _mapped_lane(backend)
    return _preferred_gpu_lane()


def _mapped_lane(token: str) -> str:
    if token == "vulkan":
        return _preferred_gpu_lane()
    return _BACKEND_TO_LANE.get(token, token)


def _sampler_block(sampler: str) -> dict[str, Any]:
    """The workload sampler block for a matrix sampler token. Mirrors
    server_ab.py's greedy-vs-production split (DESIGN §3.2 workload.sampler)."""
    if sampler == "production":
        return {"mode": "production", "temp": 0.6, "top_p": 0.95, "top_k": 20}
    return {"mode": "greedy"}


def _resolve_profile(model: dict[str, Any], depth: int) -> Config:
    """Resolve the plan-time config block from a registry entry (DESIGN §3 config).

    A model row holds file facts only (ADR-0029); the tune a cell is measured
    under is the ``profile`` the caller attaches to the entry — the profile of
    the slot that runs the model. Two shapes are accepted: the synthetic
    unit-test entry (``profile`` = {argv, env, kv, spec, ctx}) and a hal0
    profile (``profile`` = {flags (one string), ctx_size, …}). The flags string
    is shlex-split into an argv list — deterministic, so a merged profile-flag
    PR that changes ``flags`` moves the cell_key and re-benches exactly that
    cell (DESIGN §6), while an unchanged profile hashes identically run-to-run.
    An entry with no profile benches the runner's own defaults."""
    profile = model.get("profile") or {}
    if "argv" in profile:  # synthetic/test shape: already-structured profile
        argv = list(profile.get("argv") or [])
    else:
        flags = profile.get("flags") or ""
        try:
            argv = shlex.split(flags) if isinstance(flags, str) else list(flags)
        except ValueError:
            argv = flags.split() if isinstance(flags, str) else []
    spec = profile.get("spec")
    if spec is None:
        # The launch tunes with no argv spelling: they change what the slot
        # runs, so they belong in the cell identity (non-defaults only, so a
        # plain profile hashes the same as one that never named them).
        tunes = {
            key: profile[key]
            for key, default in (
                ("speculation", "auto"),
                ("vision", "auto"),
                ("enable_thinking", None),
            )
            if key in profile and profile[key] != default
        }
        spec = {"profile": tunes} if tunes else None
    return Config(
        argv=argv,
        env=dict(profile.get("env") or {}),
        kv=dict(profile.get("kv") or {}),
        spec=spec,
        parallel=int(profile.get("parallel", 1) or 1),
        ctx=int(profile.get("ctx") or profile.get("ctx_size") or depth),
    )


def _apply_flags(config: Config, flags: dict) -> Config:
    """Fold a config variant's tuning flags into the resolved config so they feed
    cell_key: append the flag tokens to argv, and reflect -ctk/-ctv in kv."""
    if not flags:
        return config
    config.argv = list(config.argv) + _flag_tokens(flags)
    if flags.get("-ctk") or flags.get("-ctv"):
        config.kv = {
            **config.kv,
            "main_k": str(flags.get("-ctk", config.kv.get("main_k", ""))),
            "main_v": str(flags.get("-ctv", config.kv.get("main_v", ""))),
        }
    return config


def _flag_tokens(flags: dict) -> list[str]:
    """Deterministic ``["-b","1024","-fa","0"]`` token list from a variant's
    flags (sorted by flag), so equal variants hash identically."""
    tokens: list[str] = []
    for flag in sorted(flags):
        tokens.extend([flag, str(flags[flag])])
    return tokens


def _build_identity(
    model: dict[str, Any],
    lane: str,
    kind: str,
    depth: int,
    sampler: str,
    flags: dict | None = None,
) -> Identity:
    """Resolve one candidate cell's identity from a registry entry + axis point.

    IMPORTANT (verified on-box 2026-07-05, deviates from DESIGN §3): the engine
    provenance the runner will actually observe — image DIGEST and llama.cpp
    BUILD number — is NOT knowable at plan time (it only exists after the sweep
    writes its .meta.json / llama-bench row). DESIGN §3 puts both in cell_key;
    doing that here would make every cell perpetually stale, because the planner
    could never predict the build the run will report. So the plan-time identity
    leaves the engine block EMPTY (excluded from cell_key), the runner reuses
    THIS identity verbatim for the record's cell_key (so plan↔run converge —
    DESIGN §5.4), and the real observed image/build are stamped onto the record's
    engine block for DISPLAY only (roster chips, run drawer). See the results
    appendix in docs/archive/handoffs/box-bringup-prompt-2026-07-05.md.
    """
    is_pp_shaped = kind in ("pp", "http_pp")
    return Identity(
        model=Model(
            id=model["id"],
            gguf=model.get("gguf") or model.get("path") or "",
            sha256=model.get("sha256", ""),
            quant=model.get("quant", ""),
            size_bytes=int(model.get("size_bytes", 0) or 0),
            caps=sorted(_model_caps(model)),
        ),
        engine=Engine(kind=_ENGINE_KIND_FOR_KIND.get(kind, "llama-server")),
        lane=lane,
        config=_apply_flags(_resolve_profile(model, depth), flags or {}),
        workload=Workload(
            kind=kind,
            depth=depth,
            n_prompt=_PP_PROMPT if is_pp_shaped else 0,
            n_gen=0 if is_pp_shaped else int(model.get("n_gen", 256) or 256),
            sampler=_sampler_block(sampler),
            concurrency=1,
        ),
    )


def _newest_ts(record: dict[str, Any]) -> datetime | None:
    """Parse a record's run_id stamp to a tz-aware datetime for the age check."""
    run_id = record.get("run_id") or ""
    stamp = run_id.split("Z-")[0] + "Z" if "Z-" in run_id else run_id
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _validated_configs(configs: list[dict]) -> list[dict]:
    """Drop any non-whitelisted flag from each config variant (the seam would
    reject it anyway) with a warning, so a bad flag degrades that variant rather
    than crashing the plan. Always returns at least the default variant."""
    out: list[dict] = []
    for c in configs or []:
        flags: dict = {}
        for k, v in (c.get("flags") or {}).items():
            if k in _TUNE_FLAGS:
                flags[k] = v
            else:
                print(
                    f"[plan] ignoring non-whitelisted config flag {k!r} in variant {c.get('label')!r}",
                    file=sys.stderr,  # stdout may be a --json stream
                )
        out.append({"label": c.get("label") or "default", "flags": flags})
    return out or [{"label": "default", "flags": {}}]


def plan(
    suite: Suite,
    registry_models: list[dict[str, Any]],
    store: Store,
    now: datetime | None = None,
) -> list[Cell]:
    """Expand ``suite.selector x matrix x cells`` into candidate cells, compute
    each cell_key, and return only the stale ones (DESIGN §6), ordered by value.

    ``registry_models`` is passed in (not fetched here) so the function stays
    pure/testable; the CLI calls ``fetch_registry_models`` and hands the result
    in. ``now`` is injectable for deterministic staleness tests.
    """
    now = now or datetime.now(UTC)
    unknown = [k for k in suite.cells.kinds if k not in KNOWN_KINDS]
    if unknown:
        raise ValueError(
            f"suite {suite.id!r}: unknown cell kind(s) {unknown} (known: {sorted(KNOWN_KINDS)})"
        )
    # Tool availability is a plan-time policy (design decision 5): a kind
    # whose adapter binary is missing is rejected here, loudly, naming the
    # pin — never discovered mid-session as a per-cell failure loop.
    missing_tools = sorted(
        k
        for k in suite.cells.kinds
        if k in _ADAPTER_TOOL_FOR_KIND
        and adapters.resolve_tool(_ADAPTER_TOOL_FOR_KIND[k][0]) is None
    )
    if missing_tools:
        detail = "; ".join(
            f"{k!r} needs {_ADAPTER_TOOL_FOR_KIND[k][0]!r} (pin: {_ADAPTER_TOOL_FOR_KIND[k][1]})"
            for k in missing_tools
        )
        raise ValueError(
            f"suite {suite.id!r}: required tool(s) not found (PATH or venv bin) — {detail}"
        )
    current = store.newest_ok_by_cell()  # cell_key -> newest ok record
    max_age = timedelta(days=suite.staleness.max_age_days)
    known_lanes = set(harness.lane_specs())

    configs = _validated_configs(suite.matrix.configs)
    selected_models = _select_models(suite, registry_models)

    # #1773: a "chat" cell's GuideLLM run needs a resolvable tokenizer (a
    # local-only model id like "chadrock-35b-ace-saber-rocmfp4-mtp" is not
    # itself a HF repo — see adapters/guidellm.py's module docstring). Same
    # plan-time policy as the missing-tool gate above: reject loudly here,
    # naming the field to set, instead of letting guidellm crash mid-session
    # trying to resolve the model id against huggingface.co.
    if "chat" in suite.cells.kinds:
        no_tokenizer = sorted(
            str(m.get("id"))
            for m in selected_models
            if m.get("id") and _resolve_tokenizer(m) is None
        )
        if no_tokenizer:
            raise ValueError(
                f"suite {suite.id!r}: 'chat' cell(s) need a resolvable tokenizer — "
                f"model(s) {no_tokenizer} have no tokenizer_repo and no "
                "hf_repo (set the model's tokenizer_repo to a HF tokenizer repo "
                "id, e.g. 'Qwen/Qwen3-4B-GGUF')"
            )

    stale: list[Cell] = []
    warned_unsupported: set[str] = set()
    for model in selected_models:
        tokenizer = _resolve_tokenizer(model) or ""
        for lane_token in suite.matrix.lanes:
            lane = _resolve_lane(model, lane_token)
            # An unsupported lane is SKIPPED, loudly (review B1). It used to be
            # warn-and-plan, which was defensible while the only unsupported
            # lane was permanently retired and no shipped suite named it — the
            # warning was for a deliberate `--backends` opt-in. Neither holds
            # now: `lane-matrix.toml` names `vulkan_radv` outright, and
            # unsupported is a property of the pinned image rather than of the
            # lane, so warn-and-plan would mean shipping a suite that produces
            # garbage records on any install still carrying a broken runner.
            if not _lane_is_plannable(lane, suite_id=suite.id, warned=warned_unsupported):
                continue
            for kind in suite.cells.kinds:
                if kind in _TIER_A_KINDS and lane not in known_lanes:
                    # Fail fast at plan time, not with a bare KeyError out of
                    # the runner (finding 5) — e.g. a suite spelling the
                    # registry's own "vulkan" hint instead of the lane token
                    # "vulkan_radv" that _BACKEND_TO_LANE maps it to.
                    raise ValueError(
                        f"suite {suite.id!r}: unknown lane {lane!r} (from matrix lane "
                        f"{lane_token!r}, model {model.get('id')!r}) for Tier-A kind "
                        f"{kind!r} — known lanes: {sorted(known_lanes)}"
                    )
                for depth in suite.matrix.depths:
                    for sampler in suite.matrix.samplers:
                        for cfg in configs:
                            flags = cfg["flags"]
                            identity = _build_identity(model, lane, kind, depth, sampler, flags)
                            key = cell_key(identity)

                            reason: str | None = None
                            existing = current.get(key)
                            if existing is None:
                                reason = "never-measured"  # clause (1)
                            else:
                                ts = _newest_ts(existing)
                                if ts is None or (now - ts) > max_age:
                                    reason = f"stale:>{suite.staleness.max_age_days}d"  # (2)

                            if reason is None:
                                continue  # fresh ok record exists — not stale

                            stale.append(
                                Cell(
                                    cell_key=key,
                                    identity=identity,
                                    suite_id=suite.id,
                                    kind=kind,
                                    reps=suite.matrix.reps,
                                    exclusive=suite.exclusive,
                                    priority=suite.priority,
                                    reason=reason,
                                    model_id=model["id"],
                                    lane=lane,
                                    depth=depth,
                                    flags=flags,
                                    config_label=cfg["label"],
                                    tokenizer=tokenizer if kind == "chat" else "",
                                )
                            )

    # Order by value (DESIGN §5.1): higher suite priority first, then
    # never-measured before merely-aged, then cheap-before-expensive within a
    # model (pp before tg, shallow before deep) so a budget-truncated session
    # still publishes something coherent. Stable sort on model_id groups a
    # model's cells together.
    kind_cost = {
        "pp": 0,
        "tg": 1,
        "chat": 2,
        "reuse": 2,
        "embed": 1,
        "rerank": 1,
        "batch": 3,
        "http_pp": 0,
        "http_tg": 1,
    }
    stale.sort(
        key=lambda c: (
            -c.priority,
            0 if c.reason == "never-measured" else 1,
            c.model_id,
            kind_cost.get(c.kind, 5),
            c.depth,
        )
    )
    return stale
