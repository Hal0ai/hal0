"""rerender_slot_units — the update-time slot-unit re-render sweep.

Slot units bake the launch argv at load time, so a hal0 update changes the
code that WOULD render but not the file that DID (field finding, CT105:
`systemctl restart` after an update re-ran the stale ExecStart). The sweep
rewrites existing units through current code + one daemon-reload and never
starts/enables/restarts anything.

Contract under test:
  - a stale on-disk unit is rewritten to the fresh render,
  - an up-to-date unit is left untouched (no gratuitous write),
  - a slot with NO unit file is skipped (never rendered → nothing stale),
  - exactly one daemon-reload per sweep, and none when nothing changed,
  - a per-slot failure (unresolvable profile) skips that slot, not the sweep.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import ProfileConfig
from hal0.providers import container as container_mod
from hal0.slots.manager import SlotManager
from hal0.updater import updater as updater_mod
from hal0.updater.updater import (
    detect_drifted_slots,
    post_commit_slot_sweep,
    rerender_slot_units,
    restart_drifted_slots,
)

_PROFILE = ProfileConfig(
    image="ghcr.io/test/toolbox:v2",
    flags="-fa on -b 1024",
    mtp=False,
    device_class="gpu",
    backend="vulkan",
)


@pytest.fixture
def rerender_env(tmp_hal0_home: str, tmp_path, monkeypatch):
    """Sandbox the systemd dir, container runtime, profile lookup, and
    systemctl calls; return the fake unit dir + recorded systemctl argv."""
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    monkeypatch.setattr(container_mod, "_SYSTEMD_SYSTEM_DIR", unit_dir)
    monkeypatch.setenv("HAL0_CONTAINER_RUNTIME", "/usr/bin/podman")
    monkeypatch.setattr(container_mod, "_resolve_profile", lambda name: _PROFILE)

    calls: list[tuple[str, ...]] = []

    def _fake_run(self, *args, check=True, **kwargs):
        calls.append(args)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(container_mod.ContainerProvider, "_run", _fake_run)
    return unit_dir, calls


def _unit_path(unit_dir, name: str):
    return unit_dir / f"hal0-slot@{name}.service"


async def _mk_slot(name: str, port: int) -> None:
    await SlotManager().create(
        name,
        {
            "name": name,
            "port": port,
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "vulkan",
            "provider": "llama-server",
            "runtime": "container",
            "enabled": True,
            "group": "custom",
            "model": {"default": "some-model"},
        },
    )


async def test_stale_unit_rewritten_and_one_daemon_reload(rerender_env) -> None:
    unit_dir, calls = rerender_env
    await _mk_slot("a", 8095)
    _unit_path(unit_dir, "a").write_text("# stale pre-update unit\n")

    assert rerender_slot_units() == 1

    text = _unit_path(unit_dir, "a").read_text()
    assert "some-model" in text and "-fa on" in text, "fresh render expected"
    assert calls.count(("systemctl", "daemon-reload")) == 1
    # Never bounce serving: no enable/start/restart in the sweep.
    assert not [c for c in calls if c[0] == "systemctl" and c[1] in ("enable", "start", "restart")]


async def test_up_to_date_unit_untouched(rerender_env) -> None:
    unit_dir, calls = rerender_env
    await _mk_slot("a", 8095)
    _unit_path(unit_dir, "a").write_text("stale")
    assert rerender_slot_units() == 1
    before = _unit_path(unit_dir, "a").read_text()
    calls.clear()

    # Second sweep: byte-identical render → no write, no daemon-reload.
    assert rerender_slot_units() == 0
    assert _unit_path(unit_dir, "a").read_text() == before
    assert ("systemctl", "daemon-reload") not in calls


async def test_slot_without_unit_file_skipped(rerender_env) -> None:
    unit_dir, calls = rerender_env
    await _mk_slot("never-loaded", 8096)

    assert rerender_slot_units() == 0
    assert not _unit_path(unit_dir, "never-loaded").exists()
    assert ("systemctl", "daemon-reload") not in calls


async def test_per_slot_failure_does_not_wedge_sweep(rerender_env, monkeypatch) -> None:
    unit_dir, _calls = rerender_env
    await _mk_slot("bad", 8097)
    await _mk_slot("good", 8098)
    _unit_path(unit_dir, "bad").write_text("stale")
    _unit_path(unit_dir, "good").write_text("stale")

    # Break only the 'bad' slot's plan build; 'good' renders normally.
    real_spec = container_mod.ContainerProvider.container_spec

    def _spec(self, cfg, mi):
        if cfg.get("name") == "bad":
            raise RuntimeError("boom")
        return real_spec(self, cfg, mi)

    monkeypatch.setattr(container_mod.ContainerProvider, "container_spec", _spec)

    assert rerender_slot_units() == 1  # good rewritten, bad skipped
    assert _unit_path(unit_dir, "bad").read_text() == "stale"
    assert "some-model" in _unit_path(unit_dir, "good").read_text()


async def test_updater_module_reexports(rerender_env) -> None:
    # install.sh + Step 8c import it from the updater module.
    assert callable(updater_mod.rerender_slot_units)


# ── #1111: post-update drift detection + opt-in restart ─────────────────────
#
# The reconcile seam from #1103 (rerender_unit_sync) already answers "is this
# slot's on-disk unit stale vs. a fresh render?" — a slot is "drifted" exactly
# when that comparison differs. detect_drifted_slots is the read-only sibling
# (never writes); restart_drifted_slots is the ONLY thing that ever bounces a
# slot as part of hal0 update, and only for names the caller already confirmed
# drifted (the opt-in ``hal0 update --restart-slots`` path).


def _restart_calls(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [c for c in calls if c[0] == "systemctl" and c[1] == "restart"]


async def test_detect_drifted_slots_finds_stale_unit(rerender_env) -> None:
    """A stale on-disk unit is reported drifted — and left untouched (read-only)."""
    unit_dir, _calls = rerender_env
    await _mk_slot("a", 8095)
    stale_text = "# stale pre-update unit\n"
    _unit_path(unit_dir, "a").write_text(stale_text)

    assert detect_drifted_slots() == ["a"]
    # Read-only: unlike rerender_slot_units, nothing was written.
    assert _unit_path(unit_dir, "a").read_text() == stale_text


async def test_detect_drifted_slots_empty_once_rewritten(rerender_env) -> None:
    """After the sweep rewrites a slot, it no longer reports as drifted."""
    unit_dir, _calls = rerender_env
    await _mk_slot("a", 8095)
    _unit_path(unit_dir, "a").write_text("stale")

    assert detect_drifted_slots() == ["a"]
    assert rerender_slot_units() == 1
    assert detect_drifted_slots() == []


async def test_detect_drifted_slots_skips_slot_with_no_unit(rerender_env) -> None:
    """A slot that has never been loaded (no unit file) is never 'drifted'."""
    await _mk_slot("never-loaded", 8096)
    assert detect_drifted_slots() == []


async def test_restart_drifted_slots_only_touches_named_slots(rerender_env) -> None:
    """restart_drifted_slots bounces exactly the names it's given — not every slot."""
    unit_dir, calls = rerender_env
    await _mk_slot("a", 8095)
    await _mk_slot("b", 8096)
    _unit_path(unit_dir, "a").write_text("stale")
    _unit_path(unit_dir, "b").write_text("stale")

    restarted = restart_drifted_slots(["a"])

    assert restarted == ["a"]
    assert _restart_calls(calls) == [("systemctl", "restart", "hal0-slot@a.service")]


async def test_post_commit_slot_sweep_default_never_restarts(rerender_env) -> None:
    """restart=False (the plain ``hal0 update`` default): report drift, bounce nothing."""
    unit_dir, calls = rerender_env
    await _mk_slot("a", 8095)
    _unit_path(unit_dir, "a").write_text("stale")

    result = post_commit_slot_sweep(restart=False)

    assert result == {"rewritten": 1, "drifted": ["a"], "restarted": []}
    assert _restart_calls(calls) == []


async def test_post_commit_slot_sweep_restarts_when_opted_in(rerender_env) -> None:
    """restart=True (``hal0 update --restart-slots``): bounce the drifted slot."""
    unit_dir, calls = rerender_env
    await _mk_slot("a", 8095)
    _unit_path(unit_dir, "a").write_text("stale")

    result = post_commit_slot_sweep(restart=True)

    assert result == {"rewritten": 1, "drifted": ["a"], "restarted": ["a"]}
    assert _restart_calls(calls) == [("systemctl", "restart", "hal0-slot@a.service")]


async def test_post_commit_slot_sweep_restarts_only_drifted_of_several(rerender_env) -> None:
    """With two slots, only the one that was actually stale gets bounced."""
    unit_dir, calls = rerender_env
    await _mk_slot("a", 8095)
    await _mk_slot("b", 8096)
    _unit_path(unit_dir, "a").write_text("stale")
    # Seed "b" as already current so only "a" is drifted going into the sweep.
    rerender_slot_units()
    _unit_path(unit_dir, "a").write_text("stale again")
    calls.clear()

    result = post_commit_slot_sweep(restart=True)

    assert result["drifted"] == ["a"]
    assert result["restarted"] == ["a"]
    assert _restart_calls(calls) == [("systemctl", "restart", "hal0-slot@a.service")]
