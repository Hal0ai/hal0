"""schema.py — the schema-2 benchmark record (DESIGN §3.2) as typed dataclasses.

WHY dataclasses over pydantic: the record is the source-of-truth line format in
`records.jsonl` and must be constructable/serializable on the box with zero
third-party deps (same stdlib-only rule as server_ab.py). Dataclasses +
`asdict` give us a typed surface for the runner/publisher and a plain-dict JSON
shape for the store, and nothing to install.

The record splits deliberately into two blocks that answer different questions:

  * the IDENTITY block — model / engine / lane / config / workload — answers
    *WHAT was measured*. It and only it feeds `cell_key()`. Two records with the
    same cell_key measure the same thing; the newest `ok` one is the current
    value and older ones are that cell's history (the trend line).

  * the ENVIRONMENT block — `host` (incl. `hal0_version`) — answers *WHERE it
    was measured*. It is deliberately NOT in cell_key: a hal0 point-release, a
    kernel bump, or (future) a second box must NOT fork a cell's identity, or
    every upgrade would orphan the entire dataset and the trend line would
    reset. host is carried for provenance/regression-gating and reserved for
    the multi-box future (DESIGN §0 non-goal, §14.4), where cell_key would gain
    an explicit host dimension — a deliberate schema change, not a side effect.

Because resolved argv/env/KV/depth/sampler/digests are all inside the identity
block, any provenance drift changes the key, which is exactly what turns
auto-update (§6) into a set-difference instead of a hand-maintained policy file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    """Terminal state of a single cell-run (DESIGN §3.2 `outcome`).

    Only ``OK`` records count as a measured value for staleness (§6) and
    publishing (§9); the rest are recorded for diagnosis and never published.
    ``SKIPPED_CONTENDED`` means the number exists but the GPU was not idle, so
    it is not clean enough to publish (smoke suite, DESIGN §4).
    """

    OK = "ok"
    FAILED = "failed"
    SKIPPED_CONTENDED = "skipped-contended"
    OOM = "oom"
    HANG = "hang"


# --------------------------------------------------------------------------- #
# Identity block — WHAT was measured. Every field here feeds cell_key().
# --------------------------------------------------------------------------- #


@dataclass
class Model:
    """The model under test. ``sha256`` (the GGUF digest) is what makes a
    re-pull of the same logical id invalidate its cells (DESIGN §6.1)."""

    id: str
    gguf: str = ""
    sha256: str = ""
    quant: str = ""
    size_bytes: int = 0
    caps: list[str] = field(default_factory=list)


@dataclass
class Engine:
    """The engine + the local-only runner image that built it. ``image_digest``
    and ``llamacpp_build`` are identity: a rebuilt runner image measures a
    different thing even with identical argv (DESIGN §3 provenance)."""

    kind: str = "llama-bench"  # "llama-bench" | "llama-server"
    image: str = ""
    image_digest: str = ""
    llamacpp_build: str = ""
    decode_tune: str = ""


@dataclass
class Config:
    """The RESOLVED, post-dedup llama.cpp configuration — argv/env as the server
    actually ran them, so a merged profile-flag PR that changes resolved argv
    invalidates exactly the affected cells and nothing else (DESIGN §6)."""

    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    kv: dict[str, str] = field(default_factory=dict)  # main_k/main_v/draft_k/draft_v
    spec: dict[str, Any] | None = None  # speculative/MTP block, or None
    parallel: int = 1
    ctx: int = 32768


@dataclass
class Workload:
    """What was driven at the engine: kind (pp/tg/chat/batch/embed/rerank/reuse),
    the ctx-fill depth axis, prompt/gen sizes, sampler, and concurrency."""

    kind: str = "tg"
    depth: int = 0
    n_prompt: int = 0
    n_gen: int = 0
    sampler: dict[str, Any] = field(default_factory=lambda: {"mode": "greedy"})
    concurrency: int = 1


@dataclass
class Identity:
    """The full identity block — the sole input to ``cell_key()``. Grouping the
    five identity dataclasses here (rather than as loose record fields) makes the
    cell_key contract literal: hash(Identity), never anything outside it."""

    model: Model
    engine: Engine
    lane: str  # "rocm" | "vulkan_radv" | "default"
    config: Config
    workload: Workload


# --------------------------------------------------------------------------- #
# Environment block — WHERE it was measured. NOT part of cell_key.
# --------------------------------------------------------------------------- #


@dataclass
class Host:
    """The box + software environment. ``hal0_version`` is required (DESIGN §3.2
    host block) so a record can always be attributed to a hal0 release for
    regression gating — but it lives here, outside cell_key, on purpose (see the
    module docstring)."""

    name: str = "hal0"
    platform: str = ""
    gpu: str = ""
    kernel: str = ""
    rocm: str = ""
    mem_gb: int = 0
    hal0_version: str = ""
    exclusive: bool = False


# --------------------------------------------------------------------------- #
# Results blocks — full detail, not just the median.
# --------------------------------------------------------------------------- #


@dataclass
class Rep:
    """One repetition, raw (DESIGN §3.2 reps[]). ``timings_raw`` keeps the
    verbatim llama-server timings block so nothing measured is discarded."""

    t_s: float | None = None
    prefill_ts: float | None = None
    decode_ts: float | None = None
    ttft_ms: float | None = None
    accept_rate: float | None = None
    drafted: int | None = None
    accepted: int | None = None
    timings_raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Summary:
    """Derived medians/percentiles for display. Regression detection (§11) reads
    ``decode_ts_med`` (or the cell's governing metric) from here."""

    decode_ts_med: float | None = None
    decode_ts_stddev: float | None = None
    prefill_ts_med: float | None = None
    ttft_ms_p50: float | None = None
    ttft_ms_p95: float | None = None
    accept_med: float | None = None
    aggregate_ts: float | None = None  # batch mode fills these
    per_stream_ts_med: float | None = None


@dataclass
class Telemetry:
    """Sampled amdgpu counters around the run (DESIGN §3 telemetry). All fields
    nullable: if debugfs GTT counters are locked down the harness still records
    the run (§14.3). ``throttled`` flags a run whose clock dropped >10%."""

    vram_peak_mb: int | None = None
    gtt_peak_mb: int | None = None
    gpu_edge_temp_max_c: int | None = None
    gpu_power_avg_w: int | None = None
    throttled: bool | None = None


@dataclass
class Record:
    """One schema-2 record: one cell x one run. This is the exact object
    appended (as canonical JSON) to ``records.jsonl``.

    ``cell_key`` is stored redundantly on the record (it is derivable from
    ``identity``) so the store/reindex can group by it without re-hashing, and
    so a greppable records.jsonl is self-describing.
    """

    run_id: str
    suite: str
    trigger: str
    identity: Identity
    host: Host
    outcome: Outcome
    cell_key: str = ""  # filled by __post_init__ if empty
    reps: list[Rep] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)
    telemetry: Telemetry = field(default_factory=Telemetry)
    artifacts: str = ""
    note: str = ""
    # Display-only label for the config variant a config-matrix suite ran (e.g.
    # "default", "b1024", "kv-q8"). NOT part of cell_key — the variant's flags are
    # already inside identity.config.argv (which IS hashed); this is just a
    # human-readable tag for the board/drawer. Empty for single-config suites.
    config: str = ""
    schema: int = 2

    def __post_init__(self) -> None:
        # Keep the stored cell_key and the identity block in lockstep: if a
        # caller built the record without one, derive it now so the invariant
        # "record.cell_key == cell_key(record.identity)" always holds.
        if not self.cell_key:
            self.cell_key = cell_key(self.identity)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict JSON shape for the store. ``outcome`` is flattened to its
        string value so the line is valid JSON without a custom encoder."""
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


# --------------------------------------------------------------------------- #
# cell_key — the content address of a measurement.
# --------------------------------------------------------------------------- #


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace. This is the
    canonicalization cell_key depends on — two identity blocks that are equal as
    data hash identically regardless of dict insertion order."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cell_key(identity: Identity | dict[str, Any]) -> str:
    """The dedup/staleness key: ``sha256:`` + hex of the canonical-JSON identity
    block (DESIGN §3.2).

    Feeds cell_key: model (incl. gguf sha256 + quant), engine (incl. image
    digest + llamacpp build + decode_tune), lane, the RESOLVED config
    (argv/env/kv/spec/parallel/ctx), and the workload (kind/depth/prompt/gen/
    sampler/concurrency). Change any of these and the key changes — that is the
    entire mechanism behind auto-update as a set-difference (§6).

    Does NOT feed cell_key: everything in the ``host`` block (name/platform/gpu/
    kernel/rocm/mem/hal0_version/exclusive), the results, the run_id, suite,
    trigger, note, or artifacts path. host is environment, not identity — see the
    module docstring for why an upgrade must not fork a cell.

    Accepts either an ``Identity`` dataclass or an already-plain identity dict
    (the store's reindex path hands us dicts read back from records.jsonl).
    """
    payload = asdict(identity) if isinstance(identity, Identity) else identity
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
