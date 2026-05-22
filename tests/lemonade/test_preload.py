"""Unit tests for ``hal0.lemonade.preload`` (ADR-0007 §1, §3, §4, §5).

The preload module's only job is to guard ``/v1/load`` so the
nuclear-evict-all policy never trips on a corrupt/missing/wrong-format
model. Each ``PreloadError`` subclass gets its own test; a happy-path
test confirms a well-formed GGUF passes all four guards; an
end-to-end ``safe_load`` test confirms the timeout-to-PreloadError
conversion required by ADR-0007 §5.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import LemonadeTimeoutError
from hal0.lemonade.preload import (
    GGUF_MAGIC,
    ChecksumMismatch,
    FileNotFound,
    LoadTimeout,
    NotAGGUF,
    PreloadError,
    SizeMismatch,
    _is_gguf_kind,
    _sha256_file,
    preload_validate,
    safe_load,
)
from hal0.registry.model import Model

# A SlotConfig stub — preload_validate doesn't read its fields today
# (they're reserved for future per-slot tunables). Use a sentinel
# object so the type-check in the signature passes without forcing
# every test to construct a full SlotConfig with all required fields.
_SLOT_CFG = object()


def _gguf_payload(body: bytes = b"\x00" * 1024) -> bytes:
    """Build a minimum-viable GGUF file: 4-byte magic + arbitrary body."""
    return GGUF_MAGIC + body


def _write_model_file(tmp_path: Path, payload: bytes, name: str = "test.gguf") -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


def _model_entry(
    path: Path,
    payload: bytes,
    *,
    model_id: str = "test-model",
    backends: list[str] | None = None,
    capabilities: list[str] | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> Model:
    """Build a registry ``Model`` with sha256 + size populated from payload.

    Mirrors what the pull engine writes: sha256 in metadata, size_bytes
    on the top-level field.
    """
    md: dict[str, Any] = {"sha256": hashlib.sha256(payload).hexdigest()}
    if metadata_extra:
        md.update(metadata_extra)
    return Model(
        id=model_id,
        path=str(path),
        size_bytes=len(payload),
        backends=backends if backends is not None else ["vulkan"],
        capabilities=capabilities if capabilities is not None else ["chat"],
        metadata=md,
    )


# ── happy path ────────────────────────────────────────────────────────


def test_preload_validate_passes_on_well_formed_gguf(tmp_path: Path) -> None:
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    # No exception → all four guards passed.
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


# ── 1. file exists ────────────────────────────────────────────────────


def test_file_not_found_when_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.gguf"
    # Build the entry without writing the file.
    entry = Model(
        id="ghost",
        path=str(missing),
        size_bytes=1024,
        backends=["vulkan"],
        capabilities=["chat"],
        metadata={"sha256": "0" * 64},
    )
    with pytest.raises(FileNotFound) as exc_info:
        preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
    assert str(missing) in str(exc_info.value)
    assert exc_info.value.kind == "file_not_found"
    # PreloadError is the documented catch-all.
    assert isinstance(exc_info.value, PreloadError)


def test_file_not_found_when_path_is_a_directory(tmp_path: Path) -> None:
    """A directory at the registry path is just as broken as a missing file."""
    d = tmp_path / "model_dir"
    d.mkdir()
    entry = Model(
        id="dirmodel",
        path=str(d),
        size_bytes=0,
        backends=["vulkan"],
        capabilities=["chat"],
        metadata={},
    )
    with pytest.raises(FileNotFound):
        preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


# ── 2. size matches ───────────────────────────────────────────────────


def test_size_mismatch_when_disk_size_differs_from_registry(tmp_path: Path) -> None:
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    # Lie about the size — pretend registry says a different number.
    entry.size_bytes = len(payload) + 1
    with pytest.raises(SizeMismatch) as exc_info:
        preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
    assert exc_info.value.kind == "size_mismatch"
    assert str(p) in str(exc_info.value)


def test_size_zero_in_registry_skips_size_check(tmp_path: Path) -> None:
    """``size_bytes == 0`` is the registry's 'unknown' sentinel."""
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    entry.size_bytes = 0
    # Should not raise — size check skipped, other guards pass.
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


# ── 3. sha256 matches ─────────────────────────────────────────────────


def test_checksum_mismatch_on_corrupt_sha256(tmp_path: Path) -> None:
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    # Replace the recorded sha with a wrong one.
    entry.metadata["sha256"] = "deadbeef" * 8  # 64 hex chars but wrong
    with pytest.raises(ChecksumMismatch) as exc_info:
        preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
    assert exc_info.value.kind == "checksum_mismatch"
    assert str(p) in exc_info.value.path


def test_checksum_check_skipped_when_registry_has_no_sha(tmp_path: Path) -> None:
    """Drop-ins under /mnt/ai-models often lack a sha256 — must not fail them."""
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    entry.metadata.pop("sha256", None)
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


def test_streaming_sha256_covers_multi_chunk_files(tmp_path: Path) -> None:
    """The streaming hash must cover a file larger than one chunk.

    ``_SHA256_CHUNK_BYTES`` is 64 KiB — write 200 KiB so the hash
    loop runs multiple times. This guards against a regression where
    the loop reads only the first chunk and short-circuits.
    """
    # Use the GGUF magic so the magic check passes too — we want
    # the test to fail ONLY if streaming sha was broken.
    body = b"A" * (200 * 1024)
    payload = GGUF_MAGIC + body
    p = _write_model_file(tmp_path, payload)

    # Sanity: streamed sha matches a one-shot stdlib hash.
    streamed = _sha256_file(p)
    expected = hashlib.sha256(payload).hexdigest()
    assert streamed == expected
    assert len(payload) > 64 * 1024  # confirm we exceeded one chunk

    # Validation passes when the recorded sha is correct.
    entry = _model_entry(p, payload)
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]

    # And flips with a one-byte tweak (catches multi-chunk corruption).
    p.write_bytes(GGUF_MAGIC + b"A" * (200 * 1024 - 1) + b"B")
    with pytest.raises(ChecksumMismatch):
        preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


# ── 4. GGUF magic ─────────────────────────────────────────────────────


def test_not_a_gguf_when_magic_missing(tmp_path: Path) -> None:
    """File starts with the wrong magic — must raise even if size+sha match."""
    payload = b"NOPE" + b"\x00" * 1024
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    with pytest.raises(NotAGGUF) as exc_info:
        preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
    assert exc_info.value.kind == "not_a_gguf"


def test_gguf_magic_check_skipped_for_moonshine_kind(tmp_path: Path) -> None:
    """ASR/TTS files don't use GGUF — magic check must skip them."""
    payload = b"ONNX" + b"\x00" * 1024  # not GGUF magic
    p = _write_model_file(tmp_path, payload, name="moonshine.bin")
    entry = _model_entry(
        p,
        payload,
        model_id="moonshine-small-en",
        backends=["moonshine"],
        capabilities=["asr"],
    )
    # Should pass: size + sha agree; GGUF magic skipped for moonshine kind.
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


def test_gguf_magic_check_skipped_for_kokoro_kind(tmp_path: Path) -> None:
    payload = b"KOKO" + b"\x00" * 1024
    p = _write_model_file(tmp_path, payload, name="kokoro.bin")
    entry = _model_entry(
        p,
        payload,
        model_id="kokoro-en",
        backends=["kokoro"],
        capabilities=["tts"],
    )
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


def test_explicit_kind_in_metadata_overrides_inference(tmp_path: Path) -> None:
    """``metadata['kind']`` wins over backend/capability inference."""
    payload = b"WHAT" + b"\x00" * 1024  # not GGUF
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(
        p,
        payload,
        backends=["vulkan"],  # would imply GGUF
        capabilities=["chat"],
        metadata_extra={"kind": "moonshine"},  # but explicit override wins
    )
    # No NotAGGUF raised — explicit kind takes precedence.
    preload_validate(_SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]


# ── _is_gguf_kind table ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "backends,capabilities,metadata,path_ext,expected",
    [
        # GGUF backends → True
        (["vulkan"], [], {}, ".gguf", True),
        (["rocm"], ["chat"], {}, "", True),
        (["cuda"], [], {}, "", True),
        (["cpu"], ["embed"], {}, "", True),
        # Non-GGUF backends → False
        (["moonshine"], ["asr"], {}, "", False),
        (["kokoro"], ["tts"], {}, "", False),
        (["sd-cpp"], [], {}, "", False),
        # Caps imply GGUF
        ([], ["chat"], {}, "", True),
        ([], ["embed"], {}, "", True),
        ([], ["rerank"], {}, "", True),
        # ASR/TTS caps → not GGUF
        ([], ["asr"], {}, "", False),
        ([], ["tts"], {}, "", False),
        # Explicit kind wins
        (["vulkan"], ["chat"], {"kind": "kokoro"}, "", False),
        (["moonshine"], ["asr"], {"kind": "gguf"}, "", True),
        # Filename suffix fallback
        ([], [], {}, ".gguf", True),
        # Total unknown → conservative True (will sniff magic at call site)
        ([], [], {}, "", True),
    ],
)
def test_is_gguf_kind_table(
    backends: list[str],
    capabilities: list[str],
    metadata: dict[str, Any],
    path_ext: str,
    expected: bool,
) -> None:
    entry = Model(
        id="t",
        path=f"/tmp/x{path_ext}",
        size_bytes=0,
        backends=backends,
        capabilities=capabilities,
        metadata=metadata,
    )
    assert _is_gguf_kind(entry) is expected


# ── PreloadError shape ────────────────────────────────────────────────


def test_all_preload_error_subclasses_carry_path() -> None:
    """Every variant — including LoadTimeout — must surface the path."""
    for cls in (FileNotFound, ChecksumMismatch, SizeMismatch, NotAGGUF, LoadTimeout):
        exc = cls("/var/lib/hal0/models/foo.gguf")
        assert exc.path == "/var/lib/hal0/models/foo.gguf"
        assert isinstance(exc, PreloadError)
        # The class-level ``kind`` is the stable token for structured logs.
        assert isinstance(cls.kind, str) and cls.kind


# ── safe_load: ADR-0007 §5 timeout-to-PreloadError ────────────────────


def _mock_lemonade_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


@pytest.mark.asyncio
async def test_safe_load_calls_v1_load_on_validation_pass(tmp_path: Path) -> None:
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload, model_id="hermes-4-14b")

    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/v1/load"
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_lemonade_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        body = await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        assert body == {"status": "loaded"}
        assert captured["body"]["model_name"] == "hermes-4-14b"


@pytest.mark.asyncio
async def test_safe_load_skips_v1_load_when_validation_fails(tmp_path: Path) -> None:
    """The whole point of ADR-0007 — pre-validation MUST short-circuit /v1/load.

    If safe_load lets a corrupt model through to /v1/load, the nuclear-
    evict-all policy will blast every loaded model on the failure.
    """
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)
    entry.metadata["sha256"] = "bad" * 32  # corrupt

    calls = {"count": 0}

    def h(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_lemonade_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(ChecksumMismatch):
            await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        # Critical: /v1/load was NEVER called.
        assert calls["count"] == 0


@pytest.mark.asyncio
async def test_safe_load_converts_timeout_to_preload_load_timeout(tmp_path: Path) -> None:
    """ADR-0007 §5: /v1/load timeout surfaces as PreloadError.LoadTimeout.

    SlotManager handles all pre-load failures via a single
    ``except PreloadError`` clause, so the timeout must be wrapped.
    """
    payload = _gguf_payload()
    p = _write_model_file(tmp_path, payload)
    entry = _model_entry(p, payload)

    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated hang")

    async with _mock_lemonade_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LoadTimeout) as exc_info:
            await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        # The original LemonadeTimeoutError is chained via __cause__.
        assert isinstance(exc_info.value.__cause__, LemonadeTimeoutError)
        # Path is preserved for the dashboard's error message.
        assert exc_info.value.path == str(p)
