"""Pre-load validation guards before ``POST /v1/load`` (ADR-0007).

Lemonade's ``Router`` implements a "nuclear" evict-on-failure policy:
when ``/v1/load`` fails for any reason *except* file-not-found, every
WrappedServer in the pool is evicted and the load is retried. Confirmed
live in the 2026-05-22 spike — server log verbatim:

    "Load failed with non-file-not-found error, evicting all models
     and retrying..."

For hal0 v0.2 this means a single bad pull mid-day blasts every warm
slot. ADR-0007 §1 mitigates by steering failures into the file-not-found
exemption: validate file existence + integrity + GGUF magic in this
module BEFORE we call ``LemonadeClient.load()``. When validation fails,
the slot's load is short-circuited — Lemonade never sees the bad load,
the other loaded models stay loaded, the dashboard surfaces an explicit
error class (``checksum_mismatch``, ``not_a_gguf``, ...).

The race window (ADR-0007 §3) — file deleted between ``preload_validate``
returning OK and ``/v1/load`` arriving at lemond — is explicitly
accepted. It reduces blast radius from "any bad load" to "raced file
delete in the millisecond between validate and load", which is a
pattern we don't see in prod. See ADR-0007 §3 for the full reasoning.

See also:
    docs/internal/adr/0007-nuclear-evict-all-mitigation.md
    docs/internal/lemonade-spike-findings-2026-05-22.md
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.lemonade.errors import LemonadeTimeoutError

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig
    from hal0.lemonade.client import LemonadeClient
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

log = logging.getLogger(__name__)


# GGUF magic bytes, little-endian. Every GGUF file starts with these
# four bytes; this is the canonical sniff per the GGUF spec.
GGUF_MAGIC: bytes = b"GGUF"

# Streaming sha256 chunk size. 64 KiB matches the brief; small enough
# that even a 14B Q5 (~10 GiB) only burns a few seconds of wall time
# and the OS page cache absorbs the rest. ADR-0007 §Consequences: the
# latency is accepted in exchange for blast-radius reduction.
_SHA256_CHUNK_BYTES: int = 64 * 1024

# Model "kinds" that ship as GGUF and therefore must pass the magic
# check. Anything else (kokoro/moonshine/sd-cpp/whispercpp) uses a
# different on-disk format — sha256 + size still apply, but the GGUF
# magic check is skipped per the brief.
#
# Inference rules (in order):
#   1. explicit ``Model.metadata['kind']`` if present
#   2. ``Model.backends`` contains any GGUF-capable backend
#      (vulkan/rocm/cuda/cpu) → treat as GGUF
#   3. ``Model.capabilities`` contains chat/embed/rerank → treat as GGUF
#   4. otherwise non-GGUF (e.g. moonshine/kokoro/comfyui assets)
_GGUF_BACKENDS: frozenset[str] = frozenset({"vulkan", "rocm", "cuda", "cpu"})
_NON_GGUF_BACKENDS: frozenset[str] = frozenset(
    {"moonshine", "kokoro", "sd-cpp", "whispercpp", "comfyui"}
)
_GGUF_CAPS: frozenset[str] = frozenset({"chat", "embed", "rerank", "vision"})


# ── PreloadError hierarchy ────────────────────────────────────────────


class PreloadError(Exception):
    """Base class for pre-load validation failures (ADR-0007 §1, §2).

    Subclass instances carry the offending model path so the SlotManager
    can surface it as the slot's ERROR-state message verbatim. Catching
    ``PreloadError`` at the SlotManager layer is the signal to set
    ``state = ERROR`` and NOT call ``LemonadeClient.load()`` — the
    whole point of the mitigation.
    """

    # Short stable token used in structured logs + dashboard labels.
    kind: str = "preload_error"

    def __init__(self, path: str | Path, message: str | None = None) -> None:
        self.path = str(path)
        super().__init__(message or f"{self.kind}: {self.path}")


class FileNotFound(PreloadError):
    """Model file is missing at the registry-declared path (ADR-0007 §1).

    The ONLY pre-load failure mode Lemonade itself handles safely (its
    evict-all policy explicitly exempts file-not-found). We still
    intercept here so the dashboard sees a typed error instead of a
    naked HTTP 4xx from lemond.
    """

    kind = "file_not_found"


class ChecksumMismatch(PreloadError):
    """File sha256 doesn't match the registry entry (ADR-0007 §1).

    Catches partial downloads, on-disk corruption, and the case where a
    /mnt/ai-models drop-in was edited out-of-band. Streaming sha256 so
    we don't load multi-GB models into memory just to validate them.
    """

    kind = "checksum_mismatch"


class SizeMismatch(PreloadError):
    """File size on disk doesn't match registry ``size_bytes`` (ADR-0007 §1).

    Cheap precheck before the sha256 walk — a partial download fails
    here before we burn IO on the full hash. (We still compute the
    hash for clarity in tests, but in prod this short-circuits the
    most common corruption pattern.)
    """

    kind = "size_mismatch"


class NotAGGUF(PreloadError):
    """First four bytes of the file aren't the GGUF magic (ADR-0007 §1).

    Skipped for non-GGUF model kinds (moonshine, kokoro, sd-cpp,
    whispercpp). See ``_is_gguf_kind`` below.
    """

    kind = "not_a_gguf"


class LoadTimeout(PreloadError):
    """``/v1/load`` exceeded its hard timeout (ADR-0007 §5).

    Lemonade serializes loads and queues pending loads indefinitely; a
    stuck queue would otherwise block hal0-api forever. We convert
    ``LemonadeTimeoutError`` from the client layer into this typed
    ``PreloadError`` so SlotManager can treat all pre-load failures
    uniformly (one except clause, one state-machine path).

    Per ADR-0007 §4: do NOT retry on this error. Retry would re-issue
    /v1/load, potentially triggering the nuclear-evict-all on the
    retry attempt.
    """

    kind = "load_timeout"


# ── public API ────────────────────────────────────────────────────────


def preload_validate(
    slot_cfg: SlotConfig,
    model_entry: Model,
    *,
    registry: ModelRegistry | None = None,
) -> None:
    """Validate a model on disk BEFORE calling ``LemonadeClient.load()``.

    Implements the four pre-load guards from ADR-0007 §1:

      1. File exists at ``model_entry.path`` → ``FileNotFound``
      2. ``stat().st_size`` matches registry ``size_bytes`` → ``SizeMismatch``
         (skipped if registry entry has ``size_bytes == 0``, treated as
         "unknown" per the Model schema)
      3. sha256 streamed over the file matches registry sha256
         → ``ChecksumMismatch`` (skipped if registry has no sha256
         recorded — e.g. user-dropped /mnt/ai-models entries)
      4. First four bytes equal ``GGUF`` magic → ``NotAGGUF``
         (skipped for non-GGUF model kinds; see ``_is_gguf_kind``)

    The function is sync (no async I/O) because the SlotManager's lock
    is already held when this runs and we don't want to release it
    around an await. The streaming hash on a 14B Q5 takes ~3-5s on a
    warm page cache — accepted per ADR-0007 §Consequences.

    Race exception (ADR-0007 §3):
        If validation passes at T0 and the file is deleted before
        ``/v1/load`` arrives at lemond, we hit evict-all anyway. The
        race window is bounded by the actual file-delete pattern,
        which is vanishingly rare in prod. Documented here, accepted,
        no further mitigation.

    Args:
        slot_cfg: The slot's parsed TOML config. Currently advisory —
            held in the signature so future per-slot tunables (e.g.
            "skip sha256 for dev mode") have a place to land.
        model_entry: The registry ``Model`` whose ``path``, ``size_bytes``,
            ``metadata['sha256']``, and capability/backend fields are
            consulted.
        registry: Optional ``ModelRegistry`` reference. Unused today
            but kept in the signature so future implementations (e.g.
            cross-checking sha256 against a sidecar manifest) don't
            need a signature break. Pass ``None`` in tests.

    Raises:
        PreloadError: a subclass of ``PreloadError`` per the rules above.

    See:
        docs/internal/adr/0007-nuclear-evict-all-mitigation.md §1, §3
    """
    del slot_cfg, registry  # reserved for future use; see docstring

    path = Path(model_entry.path)

    # ── 1. file exists ────────────────────────────────────────────────
    # Use is_file() not exists(): a broken symlink or a directory at
    # the registry path is just as broken as a missing file.
    if not path.is_file():
        log.warning(
            "lemonade.preload.file_not_found",
            extra={"model_id": model_entry.id, "path": str(path)},
        )
        raise FileNotFound(path)

    # ── 2. size matches registry ──────────────────────────────────────
    # size_bytes == 0 is the registry's "unknown" sentinel (see
    # Model.size_bytes default). Skip the check in that case rather
    # than fail every drop-in.
    actual_size = path.stat().st_size
    if model_entry.size_bytes and actual_size != model_entry.size_bytes:
        log.warning(
            "lemonade.preload.size_mismatch",
            extra={
                "model_id": model_entry.id,
                "path": str(path),
                "expected": model_entry.size_bytes,
                "actual": actual_size,
            },
        )
        raise SizeMismatch(
            path,
            message=(
                f"size_mismatch: {path} — registry says "
                f"{model_entry.size_bytes} bytes, disk has {actual_size}"
            ),
        )

    # ── 3. sha256 matches registry ────────────────────────────────────
    # Streamed in _SHA256_CHUNK_BYTES chunks — never loads the full
    # file into memory. sha256 lives in metadata['sha256'] per the
    # registry/pull.py contract (PR #137-era convention).
    expected_sha = _registry_sha256(model_entry)
    if expected_sha:
        actual_sha = _sha256_file(path)
        if actual_sha.lower() != expected_sha.lower():
            log.warning(
                "lemonade.preload.checksum_mismatch",
                extra={
                    "model_id": model_entry.id,
                    "path": str(path),
                    "expected": expected_sha,
                    "actual": actual_sha,
                },
            )
            raise ChecksumMismatch(
                path,
                message=(
                    f"checksum_mismatch: {path} — registry sha256 "
                    f"{expected_sha[:12]}..., disk sha256 {actual_sha[:12]}..."
                ),
            )

    # ── 4. GGUF magic bytes ───────────────────────────────────────────
    # Only for kinds that actually ship as GGUF. moonshine/kokoro/etc.
    # use their own formats; their integrity is covered by 1-3 above.
    if _is_gguf_kind(model_entry):
        with path.open("rb") as fh:
            first_four = fh.read(4)
        if first_four != GGUF_MAGIC:
            log.warning(
                "lemonade.preload.not_a_gguf",
                extra={
                    "model_id": model_entry.id,
                    "path": str(path),
                    "first_four_hex": first_four.hex(),
                },
            )
            raise NotAGGUF(
                path,
                message=(
                    f"not_a_gguf: {path} — first 4 bytes were "
                    f"{first_four!r}, expected {GGUF_MAGIC!r}"
                ),
            )

    log.debug(
        "lemonade.preload.ok",
        extra={"model_id": model_entry.id, "path": str(path)},
    )


async def safe_load(
    client: LemonadeClient,
    slot_cfg: SlotConfig,
    model_entry: Model,
    *,
    registry: ModelRegistry | None = None,
    recipe: str | None = None,
    ctx_size: int | None = None,
    llamacpp_backend: str | None = None,
    llamacpp_args: list[str] | None = None,
) -> dict[str, Any]:
    """Pre-validate then ``POST /v1/load`` — the ADR-0007-safe load path.

    This is the function callers (today: future ``LemonadeProvider``;
    tomorrow: anywhere we issue a Lemonade load) MUST use instead of
    bare ``client.load(...)``. It enforces:

      * ADR-0007 §1 — pre-validation in front of every load
      * ADR-0007 §4 — NO retry on failure
      * ADR-0007 §5 — convert ``LemonadeTimeoutError`` to
        ``LoadTimeout`` so SlotManager handles all pre-load failures
        through a single ``except PreloadError`` clause

    Race exception (ADR-0007 §3):
        File-deleted-between-validate-and-load remains possible. This
        function does not re-validate after the load returns; if
        lemond's evict-all fires due to a raced delete, the caller's
        cached "loaded" list is stale on the next health probe and
        the dashboard surfaces the truth on the next poll.

    Args:
        client: Open ``LemonadeClient`` to drive.
        slot_cfg: Slot config (forwarded to ``preload_validate``).
        model_entry: Registry entry to validate + load. Its ``id`` is
            passed to lemond as ``model_name``.
        registry: Optional registry handle for the validator.
        recipe, ctx_size, llamacpp_backend, llamacpp_args: forwarded
            to ``LemonadeClient.load`` verbatim.

    Returns:
        The parsed JSON body of ``POST /v1/load`` on success.

    Raises:
        PreloadError: validation failed OR /v1/load timed out. Caller
            (SlotManager) maps this to slot ``state=ERROR`` and does
            NOT retry. Other ``LemonadeError`` subclasses (HTTP 5xx
            from lemond, connect-refused) propagate unchanged — they're
            already typed appropriately.

    See:
        docs/internal/adr/0007-nuclear-evict-all-mitigation.md §1, §4, §5
    """
    # Pre-validate (sync). Raises PreloadError → propagates → caller
    # short-circuits the load. /v1/load is never touched.
    preload_validate(slot_cfg, model_entry, registry=registry)

    try:
        return await client.load(
            model_entry.id,
            recipe=recipe,
            ctx_size=ctx_size,
            llamacpp_backend=llamacpp_backend,
            llamacpp_args=llamacpp_args,
        )
    except LemonadeTimeoutError as exc:
        # ADR-0007 §5: surface as PreloadError.LoadTimeout so the
        # SlotManager's pre-load failure path catches it uniformly.
        # NO retry — see ADR-0007 §4.
        log.warning(
            "lemonade.preload.load_timeout",
            extra={"model_id": model_entry.id, "path": model_entry.path},
        )
        raise LoadTimeout(
            model_entry.path,
            message=f"load_timeout: /v1/load timed out for model_name={model_entry.id!r}",
        ) from exc


# ── internals ─────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    """Stream-hash ``path`` with sha256, returning a hex digest.

    Reads in ``_SHA256_CHUNK_BYTES`` chunks so multi-GB models don't
    spike RSS. Synchronous on purpose — see ``preload_validate``
    docstring for the rationale (lock held, no async needed).
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_SHA256_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _registry_sha256(model_entry: Model) -> str | None:
    """Pull the recorded sha256 out of a registry ``Model`` entry.

    sha256 lives in ``metadata['sha256']`` per the registry/pull.py
    convention — the pull engine writes it during the streaming
    download. Returns None for entries that have no recorded hash
    (e.g. /mnt/ai-models drop-ins that bypassed the pull path);
    those skip the checksum check rather than fail.
    """
    md = getattr(model_entry, "metadata", None) or {}
    value = md.get("sha256")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_gguf_kind(model_entry: Model) -> bool:
    """Return True iff ``model_entry`` is a GGUF-format model.

    Used to gate the GGUF magic-byte check (rule 4 in
    ``preload_validate``). Non-GGUF kinds (moonshine, kokoro,
    sd-cpp, whispercpp, comfyui assets) skip the magic check —
    their integrity is covered by size + sha256 alone.

    Detection rules (first match wins):
      1. ``metadata['kind']`` set explicitly (overrides everything)
      2. Any backend in ``_NON_GGUF_BACKENDS`` → not GGUF
      3. Any backend in ``_GGUF_BACKENDS`` → GGUF
      4. Any capability in ``_GGUF_CAPS`` (chat/embed/rerank/vision)
         → GGUF
      5. Filename ends with ``.gguf`` → GGUF
      6. Fallback: treat as GGUF (conservative — runs the magic check;
         the file either passes or surfaces a typed ``NotAGGUF`` error
         instead of slipping through with no integrity gate at all).
    """
    md = getattr(model_entry, "metadata", None) or {}
    kind = md.get("kind")
    if isinstance(kind, str) and kind.strip():
        kl = kind.strip().lower()
        if kl in {"moonshine", "kokoro", "sd-cpp", "whispercpp", "comfyui"}:
            return False
        if kl in {"llama", "gguf", "flm"}:
            return True
        # Unknown explicit kind — fall through to backend/cap rules.

    backends = {b.lower() for b in getattr(model_entry, "backends", []) or []}
    if backends & _NON_GGUF_BACKENDS:
        return False
    if backends & _GGUF_BACKENDS:
        return True

    caps = {c.lower() for c in getattr(model_entry, "capabilities", []) or []}
    # ASR/TTS capabilities → non-GGUF formats today (whisper/kokoro).
    if {"asr", "tts"} & caps:
        return False
    if caps & _GGUF_CAPS:
        return True

    if str(model_entry.path).lower().endswith(".gguf"):
        return True

    # Conservative fallback: run the magic check. If a non-GGUF file
    # slipped through detection it'll surface a typed NotAGGUF error
    # at the call site, which is preferable to silently loading a
    # corrupt or wrong-format file into Lemonade.
    return True
