"""Fabricated GGUF headers shared by the registry and API tests.

No real model files are vendored: these helpers write the minimal wire format
described in :mod:`hal0.registry.gguf_header` (KV block, optional tensor-info
stubs, no tensor data).
"""

from __future__ import annotations

import struct
from pathlib import Path

from hal0.registry.gguf_header import (
    _GGUF_TYPE_ARRAY,
    _GGUF_TYPE_INT32,
    _GGUF_TYPE_STRING,
    _GGUF_TYPE_UINT32,
)


def _enc_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _enc_kv(key: str, vtype: int, value_bytes: bytes) -> bytes:
    return _enc_str(key) + struct.pack("<I", vtype) + value_bytes


def _build_gguf(
    version: int,
    kvs: list[tuple[str, int, bytes]],
    tensor_count: int = 0,
    tensor_names: list[str] | None = None,
) -> bytes:
    """Assemble a GGUF header with the given KV list.

    Tensor info is written only when ``tensor_names`` is given (each tensor
    is a 1-D f32 stub); data sections are always omitted.
    """
    if tensor_names is not None:
        tensor_count = len(tensor_names)
    out = bytearray()
    out += b"GGUF"
    out += struct.pack("<I", version)
    out += struct.pack("<Q", tensor_count)
    out += struct.pack("<Q", len(kvs))
    for key, vtype, vbytes in kvs:
        out += _enc_kv(key, vtype, vbytes)
    for name in tensor_names or []:
        out += _enc_str(name) + struct.pack("<I", 1) + struct.pack("<Q", 8)
        out += struct.pack("<I", 0) + struct.pack("<Q", 0)
    return bytes(out)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _i32_array(values: list[int]) -> bytes:
    return struct.pack("<IQ", _GGUF_TYPE_INT32, len(values)) + b"".join(
        struct.pack("<i", v) for v in values
    )


def write_gguf(
    path: Path,
    arch: str,
    *,
    uints: dict[str, int] | None = None,
    strings: dict[str, str] | None = None,
    int_arrays: dict[str, list[int]] | None = None,
    tensor_names: list[str] | None = None,
) -> Path:
    """Write a header-only GGUF for ``arch`` with the given typed keys."""
    kvs: list[tuple[str, int, bytes]] = [
        ("general.architecture", _GGUF_TYPE_STRING, _enc_str(arch))
    ]
    kvs += [(k, _GGUF_TYPE_UINT32, _u32(v)) for k, v in (uints or {}).items()]
    kvs += [(k, _GGUF_TYPE_STRING, _enc_str(v)) for k, v in (strings or {}).items()]
    kvs += [(k, _GGUF_TYPE_ARRAY, _i32_array(v)) for k, v in (int_arrays or {}).items()]
    path.write_bytes(_build_gguf(3, kvs, tensor_names=tensor_names))
    return path
