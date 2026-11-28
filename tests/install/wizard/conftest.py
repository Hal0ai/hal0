"""Shared fixtures for the wizard tests."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import select
import struct
import termios
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from hal0.install.wizard.catalog import Catalog, load_catalog
from hal0.install.wizard.facts import Disk, Facts, load_facts

LADDER_ROWS: list[dict[str, object]] = [
    {
        "role": "starter",
        "model": "small-1",
        "label": "Small",
        "lane": "cpu",
        "download_gb": 2.0,
        "min_gpu_pool_gb": None,
        "min_ram_gb": 8,
        "needs_account": False,
    },
    {
        "role": "starter",
        "model": "big-1",
        "label": "Big",
        "lane": "rocm",
        "download_gb": 17.0,
        "min_gpu_pool_gb": 24,
        "min_ram_gb": None,
        "needs_account": True,
    },
]


@pytest.fixture
def catalog_path() -> Path:
    return Path(__file__).resolve().parents[3] / "installer/manifests/setup-catalog.json"


@pytest.fixture
def catalog(catalog_path: Path) -> Catalog:
    return load_catalog(catalog_path)


@pytest.fixture
def ladder_models_path(tmp_path: Path) -> Path:
    path = tmp_path / "setup-models.json"
    path.write_text(json.dumps({"version": 1, "model_ladder": LADDER_ROWS}))
    return path


@pytest.fixture
def ladder_catalog(catalog_path: Path, ladder_models_path: Path) -> Catalog:
    return load_catalog(catalog_path, ladder_models_path)


def _facts(**changes: object) -> Facts:
    base = load_facts(None, disks=[Disk("/", 100.0, 500.0)], ram_gb=32.0)
    return replace(base, podman_runs=True, memory_python=True, npu_device=False, **changes)


@pytest.fixture
def usable_facts() -> Facts:
    return _facts()


@pytest.fixture
def two_disk_facts() -> Facts:
    return _facts(disks=(Disk("/", 100.0, 500.0), Disk("/data", 1500.0, 2000.0)))


@pytest.fixture
def consent_facts() -> Facts:
    return _facts(gpu_outcome="cpu_only_needs_consent", gpu_reason="no_device")


@pytest.fixture
def stop_facts() -> Facts:
    return _facts(gpu_outcome="stop", gpu_reason="broken_group")


@pytest.fixture
def no_python_facts(usable_facts: Facts) -> Facts:
    return replace(usable_facts, memory_python=False)


@pytest.fixture
def npu_facts(usable_facts: Facts) -> Facts:
    return replace(usable_facts, npu_device=True, npu_flm_build=True)


@pytest.fixture
def lxc_unprivileged_no_npu_facts(usable_facts: Facts) -> Facts:
    return replace(usable_facts, container="lxc-unprivileged", npu_device=False)


@pytest.fixture
def small_pool_facts(usable_facts: Facts) -> Facts:
    return replace(usable_facts, gpu_pool_gb=8.0)


@pytest.fixture
def big_pool_facts(usable_facts: Facts) -> Facts:
    return replace(usable_facts, gpu_pool_gb=96.0)


@pytest.fixture
def unknown_pool_facts(usable_facts: Facts) -> Facts:
    return replace(usable_facts, gpu_pool_gb=None)


@pytest.fixture
def pty_pair() -> Iterator[tuple[int, str]]:
    """A pseudo-terminal: the master descriptor and the slave's path.

    The slave descriptor stays open for the whole test so that closing the
    master is the only thing that ends the terminal.
    """
    master, slave = pty.openpty()
    try:
        yield master, os.ttyname(slave)
    finally:
        for fd in (master, slave):
            with contextlib.suppress(OSError):
                os.close(fd)


def read_all(master: int, quiet: float = 0.2) -> bytes:
    """Everything the terminal has written, stopping once it has been quiet."""
    out = b""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], quiet)
        if not ready:
            break
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


def set_winsize(master: int, rows: int, cols: int) -> None:
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
