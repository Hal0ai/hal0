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
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

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


# Tuning flags a config variant may set — the seam whitelist (hal0-benchctl
# validate_extra) MINUS the ones the runner controls itself (-p/-n/-d/-r). A
# variant flag outside this set is rejected at plan time (fail fast, before the
# seam does).
_TUNE_FLAGS = frozenset({"-b", "-ub", "-ngl", "-fa", "-ctk", "-ctv", "-t", "-mmp", "-pg"})


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


def fetch_registry_models(api: str = DEFAULT_API, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Read the model registry from hal0-api ``GET /api/models``.

    Kept a small standalone client (not a class) so tests inject a plain list
    instead and never hit the network. Accepts either a bare JSON list or a
    ``{"models": [...]}`` / ``{"data": [...]}`` envelope, matching the tolerant
    shape server_ab.py uses for ``/api/slots``.
    """
    import json

    req = urllib.request.Request(f"{api.rstrip('/')}{REGISTRY_PATH}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    data = json.loads(payload) if payload else []
    if isinstance(data, dict):
        return data.get("models") or data.get("data") or []
    return data


# hal0 /api/models backend token -> benchlab lane token. The registry reports a
# model's runner backend as "vulkan"/"rocm"; benchlab's lanes are "vulkan_radv"/
# "rocm" (the llama-bench backend names the seam whitelists, hal0-benchctl:34).
_BACKEND_TO_LANE = {"vulkan": "vulkan_radv", "vulkan_radv": "vulkan_radv", "rocm": "rocm"}


def _model_caps(m: dict[str, Any]) -> set[str]:
    """The capability set a suite selector matches against.

    hal0's real ``/api/models`` (verified on-box 2026-07-05) splits a model's
    labels across ``capabilities`` (e.g. ["chat","vision"]) and ``tags`` (e.g.
    "coder","tool-calling","mtp"). The roster selector wants the chat+coder
    roster, and "coder" lives in ``tags`` — so we match against the UNION of
    ``capabilities`` + ``tags`` (+ ``type``). ``caps`` is also accepted for the
    synthetic registry entries the unit tests inject."""
    caps: set[str] = set()
    for field_name in ("caps", "capabilities", "tags"):
        caps.update(m.get(field_name) or [])
    if m.get("type"):
        caps.add(m["type"])
    return caps


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


def _resolve_lane(model: dict[str, Any], lane: str) -> str:
    """Map the matrix lane token to a concrete lane. ``"default"`` resolves to
    the model's preferred lane: the test hint (``default_lane``/``lane``) or, on a
    real box, the first entry of the registry's ``backends`` list mapped through
    ``_BACKEND_TO_LANE`` (e.g. "vulkan" -> "vulkan_radv"). Falls back to rocm."""
    if lane != "default":
        return lane
    hint = model.get("default_lane") or model.get("lane")
    if hint:
        return _BACKEND_TO_LANE.get(hint, hint)
    for backend in model.get("backends") or []:
        if backend in _BACKEND_TO_LANE:
            return _BACKEND_TO_LANE[backend]
    return "rocm"


def _sampler_block(sampler: str) -> dict[str, Any]:
    """The workload sampler block for a matrix sampler token. Mirrors
    server_ab.py's greedy-vs-production split (DESIGN §3.2 workload.sampler)."""
    if sampler == "production":
        return {"mode": "production", "temp": 0.6, "top_p": 0.95, "top_k": 20}
    return {"mode": "greedy"}


def _resolve_profile(model: dict[str, Any], depth: int) -> Config:
    """Resolve the plan-time config block from a registry entry (DESIGN §3 config).

    Two shapes are accepted: the synthetic unit-test entry (``profile`` = {argv,
    env, kv, spec, ctx}) and hal0's real ``/api/models`` entry (``defaults`` =
    {extra_args (a string), context_size, n_gpu_layers, rope_freq_base}). The
    real registry hands resolved flags as one ``extra_args`` STRING, so we
    shlex-split it into an argv list — deterministic, so a merged profile-flag PR
    that changes ``extra_args`` moves the cell_key and re-benches exactly that
    cell (DESIGN §6), while an unchanged profile hashes identically run-to-run."""
    profile = model.get("profile")
    if profile:  # synthetic/test shape: already-structured profile
        return Config(
            argv=list(profile.get("argv") or []),
            env=dict(profile.get("env") or {}),
            kv=dict(profile.get("kv") or {}),
            spec=profile.get("spec"),
            parallel=int(profile.get("parallel", 1) or 1),
            ctx=int(profile.get("ctx", depth) or depth),
        )
    defaults = model.get("defaults") or {}
    extra = defaults.get("extra_args") or ""
    try:
        argv = shlex.split(extra) if isinstance(extra, str) else list(extra)
    except ValueError:
        argv = extra.split() if isinstance(extra, str) else []
    return Config(
        argv=argv,
        env={},
        kv={},
        spec=None,
        parallel=1,
        ctx=int(defaults.get("context_size") or depth),
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
    model: dict[str, Any], lane: str, kind: str, depth: int, sampler: str,
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
    appendix in handoffs/box-bringup-prompt-2026-07-05.md.
    """
    return Identity(
        model=Model(
            id=model["id"],
            gguf=model.get("gguf") or model.get("path") or "",
            sha256=model.get("sha256", ""),
            quant=model.get("quant", ""),
            size_bytes=int(model.get("size_bytes", 0) or 0),
            caps=sorted(_model_caps(model)),
        ),
        engine=Engine(kind="llama-bench" if kind in ("pp", "tg") else "llama-server"),
        lane=lane,
        config=_apply_flags(_resolve_profile(model, depth), flags or {}),
        workload=Workload(
            kind=kind,
            depth=depth,
            n_prompt=depth if kind == "pp" else 0,
            n_gen=0 if kind == "pp" else int(model.get("n_gen", 256) or 256),
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
                print(f"[plan] ignoring non-whitelisted config flag {k!r} in variant {c.get('label')!r}")
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
    current = store.newest_ok_by_cell()  # cell_key -> newest ok record
    max_age = timedelta(days=suite.staleness.max_age_days)

    configs = _validated_configs(suite.matrix.configs)
    stale: list[Cell] = []
    for model in _select_models(suite, registry_models):
        for lane_token in suite.matrix.lanes:
            lane = _resolve_lane(model, lane_token)
            for kind in suite.cells.kinds:
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
                                )
                            )

    # Order by value (DESIGN §5.1): higher suite priority first, then
    # never-measured before merely-aged, then cheap-before-expensive within a
    # model (pp before tg, shallow before deep) so a budget-truncated session
    # still publishes something coherent. Stable sort on model_id groups a
    # model's cells together.
    kind_cost = {"pp": 0, "tg": 1, "chat": 2, "reuse": 2, "embed": 1, "rerank": 1, "batch": 3}
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
