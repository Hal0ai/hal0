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
    <arch>.context_length
    <arch>.pooling_type

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
# tokenizer vocabs we'd skim past.  Beyond this we treat the file as
# malformed-or-unknown and return ``None``.
_HEADER_WINDOW_BYTES = 8 * 1024 * 1024

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
    }
)


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
    other keys are present only if the underlying KV block carried them.

    Specifically extracts:
        * ``general.architecture``
        * ``general.embedding_length``
        * ``general.file_type``
        * ``<arch>.context_length``
        * ``<arch>.pooling_type``

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
            # First pass: scan KVs, capturing static keys (general.*) and
            # any <arch>.context_length / <arch>.pooling_type we can match
            # once we know the arch string. Because keys aren't guaranteed
            # to be ordered, we capture the arch first if it appears
            # later by doing a single linear scan with on-the-fly match.
            #
            # The spec doesn't promise ordering; in practice general.architecture
            # is the first or second KV in every model I've seen, so single-pass
            # is fine. We never load tensor data either way.
            try:
                for _ in range(kv_count):
                    key = r.gguf_string()
                    vtype = r.u32()

                    want = key in _INTERESTING_KEYS_STATIC
                    if not want and arch is not None:
                        want = key == f"{arch}.context_length" or key == f"{arch}.pooling_type"
                    if not want and arch is None and key.endswith(".context_length"):
                        # Capture arch-prefixed context_length even if we
                        # haven't seen general.architecture yet (rare but
                        # legal).  We stash both the value and the prefix
                        # so we can promote later if it matches.
                        want = True

                    if want:
                        value = _read_value(r, vtype)
                        out[key] = value
                        if key == "general.architecture" and isinstance(value, str):
                            arch = value
                    else:
                        _skip_value(r, vtype)
            except GGUFParseError:
                # Partial-read is OK: return whatever we already collected.
                # An empty `out` (no real keys) still carries version +
                # tensor_count which is enough to confirm "yes, this is GGUF".
                pass

            # Promote arch-prefixed keys into stable aliases so callers
            # don't have to know the architecture name.
            if arch:
                ctx_key = f"{arch}.context_length"
                pool_key = f"{arch}.pooling_type"
                if ctx_key in out:
                    out["context_length"] = out[ctx_key]
                if pool_key in out:
                    out["pooling_type"] = out[pool_key]
            else:
                # No arch but we may have captured a *.context_length on
                # the speculative branch above.  Promote the first one.
                for k, v in out.items():
                    if k.endswith(".context_length") and k != "context_length":
                        out["context_length"] = v
                        break

            return out
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
]
