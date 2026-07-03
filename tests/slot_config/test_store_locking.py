"""Cross-process lost-update regression for SlotConfigStore (SC-10).

capabilities.toml is a read-modify-write file touched by both hal0-api and
the CLI. Without a cross-process lock two concurrent selection writes
interleave read -> modify -> write and clobber each other's change (the
classic lost update). :meth:`SlotConfigStore.apply_and_commit` closes that
gap by holding one advisory ``flock`` on the capabilities.toml sibling
``.lock`` for the whole read+compute+write span.

These tests use REAL OS processes (``multiprocessing`` with the ``fork``
context) because advisory ``flock`` is honoured per open-file-description
across processes — threads in one interpreter would not exercise the
cross-process semantics the finding is about.
"""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import (
    CapabilitySelection,
    capabilities_toml_path,
    load_capabilities_config,
)
from hal0.slot_config import SlotConfigStore, SlotSelection


def _seed_caps(caps_path: Path) -> None:
    """Seed a v2 capabilities.toml carrying two children (embed + stt)."""
    caps_path.parent.mkdir(parents=True, exist_ok=True)
    caps_path.write_text(
        "\n".join(
            [
                "schema_version = 2",
                "[selections.embed.embed]",
                'device = "cpu"',
                'provider = "llama-server"',
                'model = "embed-old"',
                "enabled = false",
                "[selections.voice.stt]",
                'device = "cpu"',
                'provider = "moonshine"',
                'model = "stt-old"',
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _worker(
    slot: str,
    child: str,
    slot_name: str,
    model: str,
    barrier: Any,
    commit_delay: float,
) -> None:
    """Run one ``apply_and_commit`` for a single child in its own process.

    ``commit_delay`` inserts a pause between the store's authoritative read
    (``apply``) and its write (``commit``) by wrapping the store's own
    ``commit`` — the seam that, absent a lock, lets the other worker's full
    RMW land in the gap and get clobbered. With the lock the delay is spent
    holding the lock, so the other worker blocks and re-reads.
    """
    store = SlotConfigStore()
    if commit_delay:
        real_commit = store.commit

        def _slow_commit(cs: Any) -> None:
            time.sleep(commit_delay)
            real_commit(cs)

        store.commit = _slow_commit  # type: ignore[method-assign]

    selection = SlotSelection(
        slot=slot,
        child=child,
        slot_name=slot_name,
        selection=CapabilitySelection(
            device="cpu", provider="llama-server", model=model, enabled=True
        ),
    )
    barrier.wait()
    store.apply_and_commit(selection)


def test_concurrent_apply_and_commit_keeps_both_updates(tmp_hal0_home: str) -> None:
    """Two processes updating DIFFERENT children must both survive.

    Worker A (embed) sleeps between its read and write; worker B (stt) does
    a fast RMW. Without the lock A's stale write clobbers B's stt change.
    The advisory lock forces the loser to re-read, so both land.
    """
    caps_path = capabilities_toml_path()
    _seed_caps(caps_path)

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)

    worker_a = ctx.Process(
        target=_worker,
        args=("embed", "embed", "embed", "embed-NEW", barrier, 0.5),
    )
    worker_b = ctx.Process(
        target=_worker,
        args=("voice", "stt", "stt", "stt-NEW", barrier, 0.0),
    )
    worker_a.start()
    worker_b.start()
    worker_a.join(timeout=15)
    worker_b.join(timeout=15)
    assert worker_a.exitcode == 0, "worker A crashed"
    assert worker_b.exitcode == 0, "worker B crashed"

    cfg = load_capabilities_config(caps_path)
    embed = cfg.selections["embed"]["embed"]
    stt = cfg.selections["voice"]["stt"]
    assert embed.model == "embed-NEW", "embed update lost"
    assert stt.model == "stt-NEW", "stt update lost (lost-update regression)"


def _cli_migrate_worker(barrier: Any, commit_delay: float) -> None:
    """Emulate the CLI migrate save path (load -> mutate -> save) under lock."""
    from hal0.capabilities.config import save_capabilities_config
    from hal0.config.locking import file_lock

    barrier.wait()
    with file_lock(capabilities_toml_path()):
        cfg = load_capabilities_config()
        cfg.selections.setdefault("embed", {})["embed"] = CapabilitySelection(
            device="cpu", provider="llama-server", model="embed-CLI", enabled=True
        )
        if commit_delay:
            time.sleep(commit_delay)
        save_capabilities_config(cfg)


def test_cli_migrate_vs_store_apply_no_lost_update(tmp_hal0_home: str) -> None:
    """CLI-vs-API interleave: the migrate save path and store apply_and_commit
    both hold the same lock, so neither drops the other's change."""
    caps_path = capabilities_toml_path()
    _seed_caps(caps_path)

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)

    cli = ctx.Process(target=_cli_migrate_worker, args=(barrier, 0.5))
    api = ctx.Process(
        target=_worker,
        args=("voice", "stt", "stt", "stt-NEW", barrier, 0.0),
    )
    cli.start()
    api.start()
    cli.join(timeout=15)
    api.join(timeout=15)
    assert cli.exitcode == 0 and api.exitcode == 0

    cfg = load_capabilities_config(caps_path)
    assert cfg.selections["embed"]["embed"].model == "embed-CLI", "CLI change lost"
    assert cfg.selections["voice"]["stt"].model == "stt-NEW", "API change lost"


if __name__ == "__main__":  # pragma: no cover - manual repro helper
    pytest.main([__file__, "-v"])
