"""Tests for the AIE column-allocation probe + TTL cache.

Covers:
  - JSON parse of a real-ish ``xrt-smi examine -r aie-partitions`` payload.
  - Fail-soft: non-zero exit, timeout, empty stdout, malformed JSON,
    missing keys, no container runtime → ``None``.
  - TTL cache: first call probes, second within TTL does not re-exec,
    re-exec after the monotonic clock advances past the TTL.
  - ``invalidate_columns_cache`` clears one entry / all entries.
"""

from __future__ import annotations

import asyncio

import pytest

import hal0.providers.npu_columns as npu_columns

# A real-ish single-partition xrt-smi payload: one partition owning all 8
# columns (start_col=0, num_cols=8) with one hw context.
_XRT_JSON_FULL = """
{
  "devices": [
    {
      "aie_partitions": {
        "partitions": [
          {
            "start_col": 0,
            "num_cols": 8,
            "partition_index": 0,
            "hw_contexts": [{"pid": 1234}]
          }
        ]
      }
    }
  ]
}
"""


# ── fake subprocess plumbing ─────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, *, stdout: bytes = b"", returncode: int = 0, hang: bool = False):
        self._stdout = stdout
        self.returncode = returncode
        self._hang = hang

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)  # outlives the wait_for timeout
        return self._stdout, b""


def _patch_exec(monkeypatch, proc: _FakeProc, counter: list[int] | None = None):
    """Patch asyncio.create_subprocess_exec to return *proc*."""

    async def _fake_exec(*args, **kwargs):
        if counter is not None:
            counter.append(1)
        return proc

    monkeypatch.setattr(npu_columns.asyncio, "create_subprocess_exec", _fake_exec)
    # Pretend podman exists so read_aie_columns proceeds to exec.
    monkeypatch.setattr(npu_columns, "_container_runtime", lambda: "/usr/bin/podman")


@pytest.fixture(autouse=True)
def _clear_cache():
    npu_columns.invalidate_columns_cache()
    yield
    npu_columns.invalidate_columns_cache()


# ── parse tests ──────────────────────────────────────────────────────────────


def test_parse_full_partition():
    out = npu_columns._parse_aie_partitions(_XRT_JSON_FULL)
    assert out is not None
    assert out["total"] == 8
    assert out["partitions"] == [{"start_col": 0, "num_cols": 8, "contexts": 1}]


def test_parse_empty_string_none():
    assert npu_columns._parse_aie_partitions("") is None
    assert npu_columns._parse_aie_partitions("   ") is None


def test_parse_malformed_json_none():
    assert npu_columns._parse_aie_partitions("{not json") is None


def test_parse_missing_keys_none():
    assert npu_columns._parse_aie_partitions('{"devices": []}') is None
    assert npu_columns._parse_aie_partitions('{"devices": [{}]}') is None
    # partition missing num_cols
    bad = '{"devices":[{"aie_partitions":{"partitions":[{"start_col":0}]}}]}'
    assert npu_columns._parse_aie_partitions(bad) is None


# ── read_aie_columns fail-soft tests ─────────────────────────────────────────


def test_read_success(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=_XRT_JSON_FULL.encode(), returncode=0))
    out = asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu"))
    assert out is not None
    assert out["partitions"][0]["start_col"] == 0
    assert out["partitions"][0]["num_cols"] == 8


def test_read_uses_tempfile_shell_not_dev_stdout(monkeypatch):
    """Regression: the live xrt-smi build rejects ``-o /dev/stdout`` ("output
    file already exists", exit 1), and with ``--force`` it interleaves its
    human-readable console report onto stdout alongside the JSON — so the JSON
    must be written to a private temp file inside the container and read back.
    The probe therefore runs through ``sh -c`` with ``--force`` and a temp file,
    never ``-o /dev/stdout``.
    """
    captured: list[tuple] = []

    async def _fake_exec(*args, **kwargs):
        captured.append(args)
        return _FakeProc(stdout=_XRT_JSON_FULL.encode(), returncode=0)

    monkeypatch.setattr(npu_columns.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(npu_columns, "_container_runtime", lambda: "/usr/bin/podman")

    out = asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu"))
    assert out is not None  # clean JSON (cat of the temp file) still parses
    assert captured, "exec was not invoked"
    argv = captured[0]
    assert argv[:5] == ("/usr/bin/podman", "exec", "hal0-slot-npu", "sh", "-c")
    script = argv[5]
    assert "--force" in script
    assert "/dev/stdout" not in script
    assert "cat " in script  # JSON read back from the temp file
    assert npu_columns._XRT_SMI_BIN in script


def test_read_nonzero_returncode_none(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"garbage", returncode=1))
    assert asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu")) is None


def test_read_timeout_none(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(hang=True))
    # Shrink the timeout so the test is fast.
    monkeypatch.setattr(npu_columns, "_EXEC_TIMEOUT_S", 0.05)
    assert asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu")) is None


def test_read_empty_stdout_none(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"", returncode=0))
    assert asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu")) is None


def test_read_malformed_json_none(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"{broken", returncode=0))
    assert asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu")) is None


def test_read_no_runtime_none(monkeypatch):
    monkeypatch.setattr(npu_columns, "_container_runtime", lambda: None)
    assert asyncio.run(npu_columns.read_aie_columns("hal0-slot-npu")) is None


# ── cache tests ──────────────────────────────────────────────────────────────


def test_cache_probes_once_within_ttl(monkeypatch):
    counter: list[int] = []
    _patch_exec(
        monkeypatch,
        _FakeProc(stdout=_XRT_JSON_FULL.encode(), returncode=0),
        counter=counter,
    )
    # Freeze the clock.
    monkeypatch.setattr(npu_columns, "_now", lambda: 100.0)

    a = asyncio.run(npu_columns.cached_aie_columns("hal0-slot-npu"))
    b = asyncio.run(npu_columns.cached_aie_columns("hal0-slot-npu"))
    assert a == b
    assert a is not None
    assert len(counter) == 1  # exec ran exactly once


def test_cache_reprobes_after_ttl(monkeypatch):
    counter: list[int] = []
    _patch_exec(
        monkeypatch,
        _FakeProc(stdout=_XRT_JSON_FULL.encode(), returncode=0),
        counter=counter,
    )
    clock = {"t": 100.0}
    monkeypatch.setattr(npu_columns, "_now", lambda: clock["t"])

    asyncio.run(npu_columns.cached_aie_columns("hal0-slot-npu"))
    assert len(counter) == 1

    # Advance past the TTL → re-probe.
    clock["t"] = 100.0 + npu_columns._COL_CACHE_TTL_S + 1.0
    asyncio.run(npu_columns.cached_aie_columns("hal0-slot-npu"))
    assert len(counter) == 2


def test_invalidate_clears_entry(monkeypatch):
    counter: list[int] = []
    _patch_exec(
        monkeypatch,
        _FakeProc(stdout=_XRT_JSON_FULL.encode(), returncode=0),
        counter=counter,
    )
    monkeypatch.setattr(npu_columns, "_now", lambda: 100.0)

    asyncio.run(npu_columns.cached_aie_columns("hal0-slot-npu"))
    assert len(counter) == 1

    npu_columns.invalidate_columns_cache("hal0-slot-npu")
    asyncio.run(npu_columns.cached_aie_columns("hal0-slot-npu"))
    assert len(counter) == 2  # re-probed after invalidation


def test_invalidate_all(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=_XRT_JSON_FULL.encode(), returncode=0))
    monkeypatch.setattr(npu_columns, "_now", lambda: 100.0)

    asyncio.run(npu_columns.cached_aie_columns("hal0-slot-a"))
    asyncio.run(npu_columns.cached_aie_columns("hal0-slot-b"))
    assert len(npu_columns._COL_CACHE) == 2

    npu_columns.invalidate_columns_cache()
    assert npu_columns._COL_CACHE == {}
