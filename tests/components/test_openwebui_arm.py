"""OpenWebUI converge arm: pull-first repin with override marker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hal0.components import openwebui_arm
from hal0.openwebui.image_pin import OPENWEBUI_IMAGE_PIN

OLD = "sha256:" + "1" * 64
NEW = OPENWEBUI_IMAGE_PIN
UNIT = (
    "[Service]\n"
    f"ExecStartPre=podman pull ghcr.io/open-webui/open-webui@{OLD}\n"
    f"ExecStart=podman run ghcr.io/open-webui/open-webui@{OLD}\n"
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def _unit(tmp_path) -> Path:
    unit = tmp_path / "hal0-openwebui.service"
    unit.write_text(UNIT, encoding="utf-8")
    return unit


def _ok_runner():
    r = MagicMock()
    r.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return r


def test_no_unit_skips(tmp_path) -> None:
    res = openwebui_arm.converge_openwebui(unit_path=tmp_path / "missing.service")
    assert res["status"] == "skipped"


def test_already_pinned_converges(tmp_path) -> None:
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    res = openwebui_arm.converge_openwebui(unit_path=unit, runner=_ok_runner())
    assert res["status"] == "converged"


def test_drift_diagnose_only_reports_stale(tmp_path) -> None:
    res = openwebui_arm.converge_openwebui(
        unit_path=_unit(tmp_path), apply=False, runner=_ok_runner()
    )
    assert res["status"] == "stale"
    assert res["installed"] == OLD and res["pinned"] == NEW


def test_drift_pull_ok_repins_and_restarts(tmp_path) -> None:
    runner = _ok_runner()
    unit = _unit(tmp_path)
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )
    assert res["status"] == "upgraded"
    text = unit.read_text(encoding="utf-8")
    assert NEW in text and OLD not in text
    pulled = [c.args[0] for c in runner.call_args_list if "pull" in c.args[0]]
    assert pulled, "must pull before repinning"


def test_pull_failure_leaves_unit_untouched(tmp_path) -> None:
    runner = MagicMock()
    runner.return_value = MagicMock(returncode=1, stdout="", stderr="no route")
    unit = _unit(tmp_path)
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )
    assert res["status"] == "build_failed"
    assert OLD in unit.read_text(encoding="utf-8")


# ── env re-render / restart-only-on-change ─────────────────────────────────


def _restart_calls(runner: MagicMock) -> list:
    return [c for c in runner.call_args_list if any("restart" in arg for arg in c.args[0])]


def _installed_unit(tmp_path) -> Path:
    """Write the unit at the path reconcile_openwebui_env actually resolves
    (image_pin.installed_unit_path(), HAL0_HOME-aware) — unlike ``_unit()``
    above, which converge_openwebui tests override via ``unit_path=``."""
    from hal0.openwebui.image_pin import installed_unit_path

    unit = installed_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    return unit


def _write_capabilities(tmp_path, model: str) -> None:
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "capabilities.toml").write_text(
        f"""
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "{model}"
enabled = true
""",
        encoding="utf-8",
    )


def test_converge_first_pass_renders_env_and_restarts(tmp_path) -> None:
    """Image already pinned + capabilities newly written: the env's first
    render always differs from "file absent" — it must restart once."""
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    runner = _ok_runner()

    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )

    assert res["status"] == "converged"
    assert res["env_changed"] is True
    assert res["restarted"] is True
    assert len(_restart_calls(runner)) == 1
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    assert "RAG_EMBEDDING_MODEL=nomic-embed-text-v1.5" in env_path.read_text(encoding="utf-8")


def test_converge_second_pass_same_capabilities_does_not_restart(tmp_path) -> None:
    """Same live truth on the next converge → the rendered bytes are
    identical → no restart call at all."""
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    runner = _ok_runner()

    first = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )
    assert first["env_changed"] is True
    runner.reset_mock()

    second = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )
    assert "env_changed" not in second
    assert _restart_calls(runner) == []


def test_converge_capability_change_between_passes_restarts_again(tmp_path) -> None:
    """A capability change between two converge passes must restart on the
    SECOND pass too — env-driven restarts are not a one-shot."""
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    runner = _ok_runner()
    openwebui_arm.converge_openwebui(unit_path=unit, runner=runner, is_hal0_user=lambda: False)
    runner.reset_mock()

    _write_capabilities(tmp_path, "a-different-embed-model")
    second = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )
    assert second["env_changed"] is True
    assert len(_restart_calls(runner)) == 1


def test_diagnose_only_pass_never_renders_env(tmp_path) -> None:
    """apply=False (boot-time drift check) must not mutate openwebui.env —
    that's the whole point of a diagnose-only pass."""
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    runner = _ok_runner()

    res = openwebui_arm.converge_openwebui(
        unit_path=unit, apply=False, runner=runner, is_hal0_user=lambda: False
    )
    assert res["status"] == "converged"
    assert "env_changed" not in res
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    assert not env_path.exists()


def test_pull_repin_pass_renders_env_before_its_single_restart(tmp_path) -> None:
    """An image upgrade already restarts unconditionally — env must be
    rendered before that restart (one restart covers both), not trigger a
    second one."""
    unit = _unit(tmp_path)
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    runner = _ok_runner()

    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )

    assert res["status"] == "upgraded"
    assert res["env_changed"] is True
    assert len(_restart_calls(runner)) == 1
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    assert "RAG_EMBEDDING_MODEL=nomic-embed-text-v1.5" in env_path.read_text(encoding="utf-8")


def test_reconcile_openwebui_env_skips_when_unit_not_installed(tmp_path) -> None:
    res = openwebui_arm.reconcile_openwebui_env(runner=_ok_runner(), is_hal0_user=lambda: False)
    assert res["status"] == "skipped"
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    assert not env_path.exists()


def test_reconcile_openwebui_env_restarts_only_when_bytes_change(tmp_path) -> None:
    _installed_unit(tmp_path)
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    runner = _ok_runner()

    first = openwebui_arm.reconcile_openwebui_env(runner=runner, is_hal0_user=lambda: False)
    assert first["status"] == "converged"
    assert first["env_changed"] is True
    assert len(_restart_calls(runner)) == 1

    runner.reset_mock()
    second = openwebui_arm.reconcile_openwebui_env(runner=runner, is_hal0_user=lambda: False)
    assert second["status"] == "unchanged"
    assert second["env_changed"] is False
    assert _restart_calls(runner) == []


class _FakeSystemd:
    """A runner that models just enough systemd: ``restart`` starts an
    inactive unit, ``try-restart`` only restarts one that is already active."""

    def __init__(self, *, active: bool) -> None:
        self.active = active
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.argvs.append(list(argv))
        if any(a in {"restart", "svc-restart"} for a in argv):
            self.active = True
        return MagicMock(returncode=0, stdout="", stderr="")


def test_reconcile_never_starts_an_inactive_unit(tmp_path) -> None:
    """An operator who skipped or stopped Open WebUI must not get it back from
    an image install or any other reconcile: the env is re-rendered (a later
    manual start reads it) but the unit is only try-restarted."""
    _installed_unit(tmp_path)
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    systemd = _FakeSystemd(active=False)

    res = openwebui_arm.reconcile_openwebui_env(runner=systemd, is_hal0_user=lambda: False)

    assert res["status"] == "converged"
    assert res["env_changed"] is True
    assert systemd.active is False
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    assert "RAG_EMBEDDING_MODEL=nomic-embed-text-v1.5" in env_path.read_text(encoding="utf-8")


def test_reconcile_restarts_an_active_unit_when_env_changed(tmp_path) -> None:
    _installed_unit(tmp_path)
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    systemd = _FakeSystemd(active=True)

    res = openwebui_arm.reconcile_openwebui_env(runner=systemd, is_hal0_user=lambda: False)

    assert res == {"status": "converged", "env_changed": True, "restarted": True}
    assert systemd.argvs == [["systemctl", "try-restart", "hal0-openwebui.service"]]


def test_reconcile_try_restarts_through_the_seam_on_a_provisioned_box(tmp_path) -> None:
    from hal0.system.seam import SEAM_BIN

    _installed_unit(tmp_path)
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    systemd = _FakeSystemd(active=False)

    openwebui_arm.reconcile_openwebui_env(runner=systemd, is_hal0_user=lambda: True)

    assert systemd.argvs == [["sudo", "-n", SEAM_BIN, "svc-try-restart", "openwebui"]]
    assert systemd.active is False


def test_converge_env_only_pass_never_starts_an_inactive_unit(tmp_path) -> None:
    """The boot-time / ``hal0 update`` pass with the image already on the pin
    is an env-only reconcile too — same rule."""
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    _write_capabilities(tmp_path, "nomic-embed-text-v1.5")
    systemd = _FakeSystemd(active=False)

    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=systemd, is_hal0_user=lambda: False
    )

    assert res["env_changed"] is True
    assert systemd.active is False


def test_converge_image_repin_still_restarts_unconditionally(tmp_path) -> None:
    """An image upgrade is the operator asking for the new image: ``restart``
    stays (the reviewer's C1 ruling keeps it)."""
    unit = _unit(tmp_path)
    systemd = _FakeSystemd(active=False)

    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=systemd, is_hal0_user=lambda: False
    )

    assert res["status"] == "upgraded"
    assert ["systemctl", "restart", "hal0-openwebui.service"] in systemd.argvs
    assert systemd.active is True


def test_reconcile_leaves_operator_search_config_alone_and_does_not_restart(tmp_path) -> None:
    """#2256: no search provider ships, so the web-search block has nothing to
    claim. An operator's self-hosted SearXNG stays in the file and — because
    the render changes no bytes — the companion is not restarted."""
    from hal0.config.env import write_env_atomic
    from hal0.openwebui.env_writer import _env_header, default_openwebui_env

    _installed_unit(tmp_path)
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    operator = {
        "ENABLE_WEB_SEARCH": "True",
        "WEB_SEARCH_ENGINE": "searxng",
        "SEARXNG_QUERY_URL": "http://searx.lan:8080/search?q=<query>",
    }
    write_env_atomic(env_path, {**default_openwebui_env(), **operator}, header=_env_header(set()))
    runner = _ok_runner()

    res = openwebui_arm.reconcile_openwebui_env(runner=runner, is_hal0_user=lambda: False)

    assert res["status"] == "unchanged"
    assert res["env_changed"] is False
    assert _restart_calls(runner) == []
    text = env_path.read_text(encoding="utf-8")
    for key, value in operator.items():
        assert f"{key}={value}" in text


def test_reconcile_with_a_line_systemd_would_split_is_fail_soft(tmp_path) -> None:
    """A CR inside a hand-written key would give systemd a second bind-host
    assignment; the render is refused, the file stays as it was, and the
    reconcile reports build_failed rather than raising or restarting."""
    _installed_unit(tmp_path)
    env_path = tmp_path / "etc" / "hal0" / "openwebui.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"HAL0_OWUI_BIND_HOST=127.0.0.1\nX\rHAL0_OWUI_BIND_HOST=0.0.0.0\n"
    env_path.write_bytes(raw)
    runner = _ok_runner()

    res = openwebui_arm.reconcile_openwebui_env(runner=runner, is_hal0_user=lambda: False)

    assert res["status"] == "build_failed"
    assert "cannot be empty or contain" in res["error"]
    assert env_path.read_bytes() == raw
    assert _restart_calls(runner) == []


def test_reconcile_openwebui_env_resolver_failure_is_fail_soft(tmp_path) -> None:
    _installed_unit(tmp_path)
    runner = _ok_runner()
    with patch(
        "hal0.openwebui.wiring.resolve_dynamic_env_overrides",
        side_effect=RuntimeError("boom"),
    ):
        res = openwebui_arm.reconcile_openwebui_env(runner=runner, is_hal0_user=lambda: False)
    assert res["status"] == "build_failed"
    assert "boom" in res["error"]
    assert _restart_calls(runner) == []


def test_target_digest_writes_override_marker(tmp_path) -> None:
    other = "sha256:" + "2" * 64
    unit = _unit(tmp_path)
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False, target_digest=other
    )
    assert res["status"] == "upgraded"
    assert openwebui_arm.read_pin_override() == other
    # Subsequent pin-converge respects the override and reports it.
    res2 = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False
    )
    assert res2["status"] == "override"


def test_target_digest_matching_installed_persists_override(tmp_path) -> None:
    # Installed digest is already the operator's --target — distinct from
    # the release pin (NEW), so this can't be mistaken for the plain
    # already-converged-to-the-release-pin case.
    other = "sha256:" + "2" * 64
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, other), encoding="utf-8")
    runner = _ok_runner()
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False, target_digest=other
    )
    assert res["status"] == "override"
    assert openwebui_arm.read_pin_override() == other
    runner.assert_not_called()
    # A subsequent un-targeted converge holds the box at the override
    # (NEW would otherwise pull it back to the release pin).
    res2 = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False
    )
    assert res2["status"] == "override"


def test_clear_override(tmp_path) -> None:
    other = "sha256:" + "2" * 64
    unit = _unit(tmp_path)
    openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False, target_digest=other
    )
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False, clear_override=True
    )
    assert openwebui_arm.read_pin_override() is None
    assert res["status"] == "upgraded"  # back to the release pin
