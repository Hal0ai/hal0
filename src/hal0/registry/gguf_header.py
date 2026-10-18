"""GGUF header parser — read-only metadata extraction.

Parses the GGUF v1-v3 magic + KV metadata block. Designed to NEVER load
the model weights: we mmap a small initial window and read forward only
until we've collected the keys we care about (or exhausted the KV block).

Spec: https://github.com/ggerganov/ggml/blob/master/docs/gguf.md

Wire layout (little-endian):
    magic           4 bytes   = b"GGUF"
    version         u32
    tensor_count    u64
    metadata_kv     u64
    repeated:
        key         (u64 len + utf-8 bytes)
        value_type  u32   (enum 0..12)
        value       <varies — see _read_value>

Value-type enum:
    0=u8 1=i8 2=u16 3=i16 4=u32 5=i32 6=f32 7=bool
    8=string 9=array 10=u64 11=i64 12=f64
Array layout: u32 elem_type + u64 length + length elements of that type.

Only the keys requested by :func:`read_gguf_header` are returned —
specifically::

    general.architecture
    general.embedding_length
    general.tags
    <arch>.context_length
    <arch>.pooling_type
    <arch>.attention.causal

The parser skips other KV values without materialising them whenever it
can, to keep memory + token cost bounded.
"""

from __future__ import annotations

import mmap
import struct
from pathlib import Path
from typing import Any

# ── public surface ─────────────────────────────────────────────────────────

_GGUF_MAGIC = b"GGUF"

# Hard cap on how much of the file we'll mmap to find KV metadata. GGUF
# headers are typically a few KB; a 1 MiB window covers even very large
# tokenizer vocabs.  Beyond this we treat the file as
# malformed-or-unknown and return ``None``.
_HEADER_WINDOW_BYTES = 16 * 2**20

# Value-type enum (per spec).
_GGUF_TYPE_UINT8 = 0
_GGUF_TYPE_INT8 = 1
_GGUF_TYPE_UINT16 = 2
_GGUF_TYPE_INT16 = 3
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_INT32 = 5
_GGUF_TYPE_FLOAT32 = 6
_GGUF_TYPE_BOOL = 7
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9
_GGUF_TYPE_UINT64 = 10
_GGUF_TYPE_INT64 = 11
_GGUF_TYPE_FLOAT64 = 12

# (struct_fmt, size_bytes) for fixed-width scalar types.
_SCALAR_FORMATS: dict[int, tuple[str, int]] = {
    _GGUF_TYPE_UINT8: ("<B", 1),
    _GGUF_TYPE_INT8: ("<b", 1),
    _GGUF_TYPE_UINT16: ("<H", 2),
    _GGUF_TYPE_INT16: ("<h", 2),
    _GGUF_TYPE_UINT32: ("<I", 4),
    _GGUF_TYPE_INT32: ("<i", 4),
    _GGUF_TYPE_FLOAT32: ("<f", 4),
    _GGUF_TYPE_BOOL: ("<B", 1),
    _GGUF_TYPE_UINT64: ("<Q", 8),
    _GGUF_TYPE_INT64: ("<q", 8),
    _GGUF_TYPE_FLOAT64: ("<d", 8),
}

# Keys we always want when present. Anything else gets skipped without
# decoding the value body (we still need to walk past it).
_INTERESTING_KEYS_STATIC: frozenset[str] = frozenset(
    {
        "general.architecture",
        "general.embedding_length",
        "general.name",
        "general.basename",
        "general.size_label",
        # llama.cpp LLAMA_FTYPE enum — the authoritative quantisation
        # signal (WS-13); registry/detect maps it to a "Q4_K_M"-style label.
        "general.file_type",
        # #1838: capability tie-breakers for headers that omit
        # <arch>.pooling_type — a reranker/embed converter that drops the
        # pooling key still tends to carry these. general.tags is an array
        # of free-text strings (e.g. ["reranker", "cross-encoder"]).
        "general.tags",
        # Speculative-decoding drafter facts (ADR-0031 §2). The two
        # ``target_layers`` keys are int32 arrays; _read_value materialises
        # them as list[int].
        "dflash.embedding_length",
        "dflash.block_size",
        "dflash.target_layers",
        "dflash.selector_top_k",
        "eagle3.target_hidden_size",
        "eagle3.target_layers",
        "lilicorr.candidate_topk",
        "tokenizer.ggml.model",
        "tokenizer.ggml.bos_token_id",
        "tokenizer.ggml.eos_token_id",
        "general.base_model.0.name",
        "general.base_model.0.repo_url",
    }
)

#: ``<arch>.<suffix>`` keys collected for whatever architecture the file names.
_ARCH_KEY_SUFFIXES: tuple[str, ...] = (
    "context_length",
    "pooling_type",
    "attention.causal",
    "block_count",
    "vocab_size",
    "nextn_predict_layers",
    "embedding_length",
)

#: How many tensor names ``tensor_names_prefix`` carries at most.
_TENSOR_NAME_PREFIX_COUNT = 64


class GGUFParseError(Exception):
    """Raised on a truncated / malformed GGUF header.

    Callers usually convert this into a ``None`` return — see
    :func:`read_gguf_header`.
    """


# ── reader ─────────────────────────────────────────────────────────────────


class _Reader:
    """Cursor over a bytes-like blob with bounds-checked reads."""

    __slots__ = ("buf", "limit", "pos")

    def __init__(self, buf: bytes | mmap.mmap, limit: int) -> None:
        self.buf = buf
        self.pos = 0
        self.limit = limit

    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > self.limit:
            raise GGUFParseError(
                f"truncated read: want {n} at offset {self.pos}, limit {self.limit}"
            )
        out = bytes(self.buf[self.pos : self.pos + n])
        self.pos += n
        return out

    def skip(self, n: int) -> None:
        if n < 0 or self.pos + n > self.limit:
            raise GGUFParseError(
                f"truncated skip: want {n} at offset {self.pos}, limit {self.limit}"
            )
        self.pos += n

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def gguf_string(self) -> str:
        length = self.u64()
        # Sanity: a single KV string longer than the header window is a
        # corruption smell; reject it before we OOM.
        if length > self.limit:
            raise GGUFParseError(f"string length {length} exceeds buffer limit {self.limit}")
        raw = self.read(length)
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - decode w/ replace shouldn't raise
            raise GGUFParseError(f"string decode failed: {exc}") from exc


# ── value read / skip ─────────────────────────────────────────────────────


def _read_value(r: _Reader, vtype: int) -> Any:
    """Read and return a GGUF value of ``vtype``. Used for keys we want."""
    if vtype in _SCALAR_FORMATS:
        fmt, size = _SCALAR_FORMATS[vtype]
        (val,) = struct.unpack(fmt, r.read(size))
        if vtype == _GGUF_TYPE_BOOL:
            return bool(val)
        return val
    if vtype == _GGUF_TYPE_STRING:
        return r.gguf_string()
    if vtype == _GGUF_TYPE_ARRAY:
        elem_type = r.u32()
        length = r.u64()
        return [_read_value(r, elem_type) for _ in range(length)]
    raise GGUFParseError(f"unknown GGUF value type: {vtype}")


def _skip_value(r: _Reader, vtype: int) -> None:
    """Skip a GGUF value of ``vtype`` without materialising it."""
    if vtype in _SCALAR_FORMATS:
        _, size = _SCALAR_FORMATS[vtype]
        r.skip(size)
        return
    if vtype == _GGUF_TYPE_STRING:
        length = r.u64()
        if length > r.limit:
            raise GGUFParseError(f"string length {length} exceeds buffer limit {r.limit}")
        r.skip(length)
        return
    if vtype == _GGUF_TYPE_ARRAY:
        elem_type = r.u32()
        length = r.u64()
        if elem_type in _SCALAR_FORMATS:
            _, size = _SCALAR_FORMATS[elem_type]
            # Multiplication can't overflow practically: we bounds-check inside skip.
            r.skip(size * length)
            return
        # Variable-width array (strings or nested arrays) — walk each element.
        for _ in range(length):
            _skip_value(r, elem_type)
        return
    raise GGUFParseError(f"unknown GGUF value type: {vtype}")


# ── public API ────────────────────────────────────────────────────────────


def read_gguf_header(path: str | Path) -> dict[str, Any] | None:
    """Parse the GGUF header and return a dict of interesting KV pairs.

    Returns ``None`` if the file is missing, is not GGUF (bad magic), or
    is so truncated/malformed that no keys can be extracted. The returned
    dict always contains ``"version"`` and ``"tensor_count"`` on success;
    other keys are present only if the underlying KV block carried them
    (see :func:`read_gguf_header_bytes`).

    The file is mmap'd read-only over a small leading window; no tensor
    weights are touched.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size < 24:  # magic(4) + version(4) + tensor_count(8) + kv_count(8)
        return None

    try:
        f = open(p, "rb")  # noqa: SIM115 - managed manually so mmap can outlive scope cleanly
    except OSError:
        return None

    try:
        window = min(size, _HEADER_WINDOW_BYTES)
        try:
            mm = mmap.mmap(f.fileno(), window, access=mmap.ACCESS_READ)
        except (OSError, ValueError):
            # mmap can fail on certain filesystems (e.g. some FUSE mounts);
            # fall back to a buffered read of the window.
            f.seek(0)
            buf: bytes | mmap.mmap = f.read(window)
            mm = None  # type: ignore[assignment]
        else:
            buf = mm
        try:
            return _parse_header(buf, window)
        finally:
            if mm is not None:
                with _suppress_close():
                    mm.close()
    finally:
        with _suppress_close():
            f.close()


def read_gguf_header_bytes(buf: bytes, *, require_complete: bool = False) -> dict[str, Any] | None:
    """Parse a GGUF header out of an in-memory byte window.

    Same result shape as :func:`read_gguf_header` (which delegates here after
    reading its window). Used for headers fetched by HTTP range. ``None``
    when ``buf`` is not GGUF. With ``require_complete`` it is also ``None``
    when the window ends inside the KV block, so a ranged reader knows to
    fetch more instead of accepting a partial dict.
    """
    if len(buf) < 24:
        return None
    return _parse_header(buf, len(buf), require_complete=require_complete)


def _is_arch_key(key: str, arch: str | None) -> bool:
    if arch is not None:
        return any(key == f"{arch}.{s}" for s in _ARCH_KEY_SUFFIXES)
    # Arch not seen yet (the spec doesn't promise ordering, #1838 codex
    # review): capture by suffix and promote below once/if arch turns up.
    return any(key.endswith(f".{s}") for s in _ARCH_KEY_SUFFIXES)


def _parse_header(
    buf: bytes | mmap.mmap, window: int, *, require_complete: bool = False
) -> dict[str, Any] | None:
    r = _Reader(buf, window)

    magic = r.read(4)
    if magic != _GGUF_MAGIC:
        return None

    version = r.u32()
    tensor_count = r.u64()
    kv_count = r.u64()

    out: dict[str, Any] = {
        "version": version,
        "tensor_count": tensor_count,
    }

    arch: str | None = None
    kv_complete = False
    # Single linear scan; the spec doesn't promise ordering, but
    # general.architecture is first or second in every model seen.
    # Tensor data is never loaded.
    try:
        for _ in range(kv_count):
            key = r.gguf_string()
            vtype = r.u32()

            if key in _INTERESTING_KEYS_STATIC or _is_arch_key(key, arch):
                value = _read_value(r, vtype)
                out[key] = value
                if key == "general.architecture" and isinstance(value, str):
                    arch = value
            else:
                _skip_value(r, vtype)
        kv_complete = True
    except GGUFParseError:
        # Partial-read is OK: return whatever we already collected.
        # An empty `out` (no real keys) still carries version +
        # tensor_count which is enough to confirm "yes, this is GGUF".
        pass

    if require_complete and not kv_complete:
        return None

    if kv_complete and tensor_count:
        names = _read_tensor_names(r, min(tensor_count, _TENSOR_NAME_PREFIX_COUNT))
        if names is not None:
            out["tensor_names_prefix"] = names

    # Promote arch-prefixed keys into stable aliases so callers
    # don't have to know the architecture name.
    aliases = (
        ("context_length", "context_length"),
        ("pooling_type", "pooling_type"),
        ("attention.causal", "attention_causal"),
    )
    if arch:
        for suffix, alias in aliases:
            if f"{arch}.{suffix}" in out:
                out[alias] = out[f"{arch}.{suffix}"]
    else:
        # No arch but we may have captured a *.context_length etc. on the
        # speculative branch above. Promote the first one of each.
        for suffix, alias in aliases:
            if alias in out:
                continue
            for k, v in out.items():
                if k.endswith(f".{suffix}") and k != alias:
                    out[alias] = v
                    break

    return out


def _read_tensor_names(r: _Reader, count: int) -> list[str] | None:
    """First ``count`` tensor names, or ``None`` if the block is cut off."""
    names: list[str] = []
    try:
        for _ in range(count):
            names.append(r.gguf_string())
            n_dims = r.u32()
            if n_dims > 8:
                return None
            r.skip(8 * n_dims + 4 + 8)  # dims, type, data offset
    except GGUFParseError:
        return None
    return names


#: How far into a file :func:`read_gguf_tensor_types` reads. The tensor-info
#: block follows the KV block (which carries the tokenizer, a few MB at most);
#: 64 MiB covers both for every model hal0 has seen.
_TENSOR_INFO_WINDOW_BYTES = 64 * 1024 * 1024


def read_gguf_tensor_types(path: str | Path) -> frozenset[int] | None:
    """The ggml tensor type ids a GGUF's tensor-info block declares.

    Walks past every KV pair, then reads each tensor's ``type`` field (no
    tensor data). Returns ``None`` when the file is not GGUF or the block
    cannot be reached; a block cut off by the read window returns the types
    seen so far.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
        f = open(p, "rb")  # noqa: SIM115 - closed below
    except OSError:
        return None
    try:
        window = min(size, _TENSOR_INFO_WINDOW_BYTES)
        if window < 24:
            return None
        try:
            mm: mmap.mmap | None = mmap.mmap(f.fileno(), window, access=mmap.ACCESS_READ)
            buf: bytes | mmap.mmap = mm  # type: ignore[assignment]
        except (OSError, ValueError):
            mm = None
            buf = f.read(window)
        try:
            r = _Reader(buf, window)
            if r.read(4) != _GGUF_MAGIC:
                return None
            r.u32()  # version
            tensor_count = r.u64()
            kv_count = r.u64()
            types: set[int] = set()
            try:
                for _ in range(kv_count):
                    r.gguf_string()
                    _skip_value(r, r.u32())
                for _ in range(tensor_count):
                    r.gguf_string()  # name
                    n_dims = r.u32()
                    if n_dims > 8:
                        raise GGUFParseError(f"implausible n_dims {n_dims}")
                    r.skip(8 * n_dims)
                    types.add(r.u32())
                    r.skip(8)  # data offset
            except GGUFParseError:
                if not types:
                    return None
            return frozenset(types)
        finally:
            if mm is not None:
                with _suppress_close():
                    mm.close()
    finally:
        with _suppress_close():
            f.close()


# ── tiny helper ───────────────────────────────────────────────────────────


class _suppress_close:
    """Context manager that swallows OSError on close — keeps cleanup quiet."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return exc_type is not None and issubclass(exc_type, OSError)


__all__ = [
    "GGUFParseError",
    "read_gguf_header",
    "read_gguf_header_bytes",
    "read_gguf_tensor_types",
]
