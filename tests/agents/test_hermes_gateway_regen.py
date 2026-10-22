"""Root-side gateway unit regeneration for ``hal0-update hermes-gateway`` (ADR-0034 d6).

The unit is rendered by hal0, never by Hermes: root must not execute the
hal0-owned Hermes release. These tests pin the render against the normalised
upstream v0.22.2 unit, the validation that refuses a unit that could run as
root, and the guard matrix that keeps a gateway's enabled/active state exactly
as it was.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_gateway_regen as regen
from hal0.agents import hermes_gateway_unit as gu

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hermes"
UID = 998  # the uid the v0.22.2 fixture was generated with


@pytest.fixture(autouse=True)
def _chown(monkeypatch: Any) -> list[tuple[str, int, int]]:
    """tests/agents/conftest.py fakes euid 0, so record the root:root chown."""
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(regen.os, "chown", lambda p, u, g: calls.append((str(p), u, g)))
    return calls


def _norm(text: str) -> str:
    # Upstream's own comparison (hermes_cli/gateway.py _normalize_service_definition).
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


# ── render ─────────────────────────────────────────────────────────────────────


def test_render_matches_the_normalised_upstream_v0_22_2_unit() -> None:
    """The fixture is `hermes gateway install --system --run-as-user hal0` at
    d923a5eb with the default config, on a release whose checkout has
    node_modules/.bin (upstream prepends it to PATH). Normalised to current/, it
    must equal hal0's render line for line, PATH included. The documented
    differences (watchdog, TimeoutStopSec from config, LD_LIBRARY_PATH, node
    dirs under ~/.hermes) only appear when the hal0 user's Hermes config or
    home asks for them, and the fixture has none.
    """
    upstream = gu.normalise_text((FIXTURES / "gateway-unit-v0.22.2.service").read_text())
    assert _norm(regen.render_unit(UID)) == _norm(upstream)
    path_line = next(ln for ln in upstream.splitlines() if ln.startswith('Environment="PATH='))
    assert path_line in regen.render_unit(UID).splitlines()


def test_render_uses_only_fixed_current_paths_and_the_given_uid() -> None:
    text = regen.render_unit(1234)
    assert "After=user@1234.service" in text.splitlines()
    assert "Wants=user@1234.service" in text.splitlines()
    assert "/releases/" not in text
    assert "/var/lib/hal0/venvs/" not in text
    assert '"/var/lib/hal0/hermes/current/venv/bin/python" "-I" "-c"' in text
    assert "sys.path.insert(0, '/var/lib/hal0/hermes/current/src')" in text
    assert 'Environment="HERMES_HOME=/var/lib/hal0/.hermes"' in text
    assert (
        'Environment="PATH=/var/lib/hal0/hermes/current/src/node_modules/.bin:'
        '/var/lib/hal0/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"'
    ) in text.splitlines()
    assert regen.validate_unit(text, 1234) == []


def test_the_render_is_detected_as_current_shape() -> None:
    assert not regen.unit_is_old_shape(regen.render_unit(UID))
    assert regen.unit_is_old_shape((FIXTURES / "gateway-unit-3ef6bbd2.service").read_text())


def test_stale_reason_accepts_only_hal0s_launcher() -> None:
    """#2894: anything but hal0's launcher on current/ is stale, upstream's shim first."""
    v022 = (FIXTURES / "gateway-unit-v0.22.2.service").read_text()
    assert regen.unit_stale_reason(regen.render_unit(UID)) is None
    assert regen.unit_stale_reason(gu.normalise_text(v022)) is None
    old = (FIXTURES / "gateway-unit-3ef6bbd2.service").read_text()
    assert "predates Hermes 0.22" in (regen.unit_stale_reason(old) or "")
    shim = (FIXTURES / "gateway-unit-upstream-shim.service").read_text()
    assert not regen.unit_is_old_shape(shim)
    assert "src/.hermes/bin/hermes" in (regen.unit_stale_reason(shim) or "")
    # A unit still naming releases/<id> is not on hal0's launcher either.
    assert "ExecStart=" in (regen.unit_stale_reason(v022) or "")
    # One stale Exec line is enough.
    one = regen.render_unit(UID).replace(
        "ExecStopPost=-", 'ExecStopPost=-"/var/lib/hal0/hermes/current/src/.hermes/bin/hermes" #', 1
    )
    assert "ExecStopPost=" in (regen.unit_stale_reason(one) or "")
    no_start = "\n".join(
        ln for ln in regen.render_unit(UID).splitlines() if not ln.startswith("ExecStart=")
    )
    assert "no ExecStart=" in (regen.unit_stale_reason(no_start) or "")


# ── validation ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda t: t.replace("User=hal0\n", ""), "User="),
        (lambda t: t.replace("User=hal0", "User=root"), "User="),
        (lambda t: t.replace("User=hal0\n", "User=hal0\nUser=root\n"), "User="),
        (lambda t: t.replace("Group=hal0", "Group=root"), "Group="),
        (lambda t: t.replace('ExecStart="', 'ExecStart=+"'), "ExecStart="),
        (lambda t: t.replace('ExecStart="', 'ExecStart=!"'), "ExecStart="),
        (
            lambda t: t.replace("ExecStart=", "ExecStartPre=/bin/sh -c id\nExecStart="),
            "ExecStartPre",
        ),
        (
            lambda t: t.replace(
                "/var/lib/hal0/hermes/current/", "/var/lib/hal0/hermes/releases/x/"
            ),
            "release path",
        ),
        (lambda t: t.replace("/var/lib/hal0/hermes/current/src", "/tmp/evil"), "/tmp/evil"),
        (lambda t: t.replace(f"After=user@{UID}.service\n", ""), "After=user@"),
    ],
)
def test_validation_refuses_a_unit_root_must_not_write(mutate: Any, why: str) -> None:
    problems = regen.validate_unit(mutate(regen.render_unit(UID)), UID)
    assert problems, "tampered unit passed validation"
    assert any(why in p for p in problems), problems


def test_validation_refuses_uid_zero() -> None:
    assert any("uid 0" in p for p in regen.validate_unit(regen.render_unit(0), 0))


def test_a_render_that_fails_validation_is_never_written(tmp_path: Path, monkeypatch: Any) -> None:
    unit = _old_unit(tmp_path)
    before = unit.read_text()
    monkeypatch.setattr(
        regen, "render_unit", lambda uid: "[Service]\nUser=root\nExecStart=/bin/sh\n"
    )
    systemctl = FakeSystemctl(enabled="enabled", active=True)
    with pytest.raises(regen.GatewayRegenError, match="refusing the rendered unit"):
        _regen(tmp_path, systemctl, linger=False)
    assert unit.read_text() == before
    assert systemctl.mutating == []


# ── guard matrix and state preservation ────────────────────────────────────────


class FakeSystemctl:
    """Answers is-enabled/is-active and records every mutating call."""

    def __init__(self, *, enabled: str, active: bool, fail: str | None = None) -> None:
        self.enabled = enabled
        self.active = active
        self.fail = fail
        self.calls: list[list[str]] = []

    @property
    def mutating(self) -> list[list[str]]:
        return [c for c in self.calls if c[1] not in ("is-enabled", "is-active")]

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0, self.enabled + "\n", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0 if self.active else 3, "", "")
        if self.fail and argv[1] == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", f"{self.fail} boom")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _old_unit(tmp_path: Path) -> Path:
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text(
        gu.normalise_text((FIXTURES / "gateway-unit-3ef6bbd2.service").read_text()),
        encoding="utf-8",
    )
    return unit


def _new_unit(tmp_path: Path) -> Path:
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text(regen.render_unit(UID), encoding="utf-8")
    return unit


def _regen(tmp_path: Path, systemctl: FakeSystemctl, *, linger: bool) -> regen.RegenResult:
    linger_dir = tmp_path / "linger"
    linger_dir.mkdir(exist_ok=True)
    if linger:
        (linger_dir / "hal0").touch()
    return regen.regenerate_gateway_unit(
        unit_path=tmp_path / "hermes-gateway.service",
        linger_dir=str(linger_dir),
        run=systemctl,
        uid_of=lambda name: UID,
        which=lambda name: f"/usr/bin/{name}",
    )


_MATRIX = list(itertools.product(("old", "new"), ("enabled", "disabled"), (True, False)))


@pytest.mark.parametrize(("shape", "enabled", "active"), _MATRIX)
def test_guard_matrix_with_linger_off(
    tmp_path: Path, shape: str, enabled: str, active: bool
) -> None:
    unit = _old_unit(tmp_path) if shape == "old" else _new_unit(tmp_path)
    before = unit.read_text()
    systemctl = FakeSystemctl(enabled=enabled, active=active)

    result = _regen(tmp_path, systemctl, linger=False)

    in_use = enabled == "enabled" or active
    if not in_use:
        # A disabled, stopped gateway (the test boxes) is never touched.
        assert result.action == "noop"
        assert unit.read_text() == before
        assert systemctl.mutating == []
        return
    assert result.action == "regenerated"
    expected: list[list[str]] = []
    if shape == "old":
        assert result.unit_rewritten
        assert _norm(unit.read_text()) == _norm(regen.render_unit(UID))
        expected.append(["systemctl", "daemon-reload"])
    else:
        assert not result.unit_rewritten
        assert unit.read_text() == before
    expected.append(["loginctl", "enable-linger", "hal0"])
    if active:
        expected.append(["systemctl", "restart", "hermes-gateway.service"])
    assert systemctl.mutating == expected
    # State preservation: never enable, disable, start or stop.
    for call in systemctl.mutating:
        assert call[1] not in ("enable", "disable", "start", "stop", "try-restart")


@pytest.mark.parametrize(
    ("enabled", "active"), [("enabled", True), ("enabled", False), ("disabled", True)]
)
def test_old_shape_with_linger_already_on_rewrites_without_loginctl(
    tmp_path: Path, enabled: str, active: bool
) -> None:
    _old_unit(tmp_path)
    systemctl = FakeSystemctl(enabled=enabled, active=active)
    result = _regen(tmp_path, systemctl, linger=True)
    assert result.unit_rewritten and not result.linger_enabled
    assert systemctl.mutating == [["systemctl", "daemon-reload"]] + (
        [["systemctl", "restart", "hermes-gateway.service"]] if active else []
    )


@pytest.mark.parametrize("linger_on", [True, False])
def test_an_upstream_shim_unit_is_rewritten_to_hal0s_launcher(
    tmp_path: Path, linger_on: bool
) -> None:
    """#2894: upstream's release-local shim has user@ ordering, so the old-shape
    test passed it; the heal must still rewrite it, validated, and turn linger on."""
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text((FIXTURES / "gateway-unit-upstream-shim.service").read_text())
    systemctl = FakeSystemctl(enabled="enabled", active=False)
    result = _regen(tmp_path, systemctl, linger=linger_on)
    assert result.action == "regenerated" and result.unit_rewritten
    assert unit.read_text() == regen.render_unit(UID)
    assert regen.unit_stale_reason(unit.read_text()) is None
    assert regen.validate_unit(unit.read_text(), UID) == []
    expected = [["systemctl", "daemon-reload"]]
    if not linger_on:
        expected.append(["loginctl", "enable-linger", "hal0"])
        assert result.linger_enabled
    # Stopped (activation stopped it): rewritten, never started.
    assert systemctl.mutating == expected


def test_a_current_unit_with_linger_on_is_a_noop(tmp_path: Path) -> None:
    unit = _new_unit(tmp_path)
    systemctl = FakeSystemctl(enabled="enabled", active=True)
    result = _regen(tmp_path, systemctl, linger=True)
    assert result.action == "noop"
    assert "current" in result.message
    assert systemctl.mutating == []
    assert unit.read_text() == regen.render_unit(UID)


def test_idempotent_second_run_does_nothing(tmp_path: Path) -> None:
    _old_unit(tmp_path)
    linger_dir = tmp_path / "linger"
    linger_dir.mkdir()

    class LingerOn(FakeSystemctl):
        def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            if argv[:2] == ["loginctl", "enable-linger"]:
                (linger_dir / argv[2]).touch()
            return super().__call__(argv, **kw)

    first = LingerOn(enabled="enabled", active=True)
    assert _regen(tmp_path, first, linger=False).action == "regenerated"
    second = LingerOn(enabled="enabled", active=True)
    result = _regen(tmp_path, second, linger=False)
    assert result.action == "noop"
    assert second.mutating == []


@pytest.mark.parametrize("state", ["masked", "masked-runtime"])
def test_a_masked_unit_is_left_alone(tmp_path: Path, state: str) -> None:
    unit = _old_unit(tmp_path)
    before = unit.read_text()
    systemctl = FakeSystemctl(enabled=state, active=False)
    assert _regen(tmp_path, systemctl, linger=False).action == "noop"
    assert unit.read_text() == before
    assert systemctl.mutating == []


def test_a_symlinked_unit_is_never_written_through(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text("[Service]\nUser=hal0\n")
    (tmp_path / "hermes-gateway.service").symlink_to(target)
    systemctl = FakeSystemctl(enabled="enabled", active=True)
    assert _regen(tmp_path, systemctl, linger=False).action == "noop"
    assert target.read_text() == "[Service]\nUser=hal0\n"
    assert systemctl.calls == []


def test_no_unit_is_a_noop(tmp_path: Path) -> None:
    systemctl = FakeSystemctl(enabled="enabled", active=True)
    assert _regen(tmp_path, systemctl, linger=False).action == "noop"
    assert systemctl.calls == []


def test_the_written_unit_is_root_owned_0644(tmp_path: Path, _chown: list[Any]) -> None:
    unit = _old_unit(tmp_path)
    unit.chmod(0o600)
    _regen(tmp_path, FakeSystemctl(enabled="enabled", active=False), linger=True)
    assert unit.stat().st_mode & 0o777 == 0o644
    assert [(u, g) for _, u, g in _chown] == [(0, 0)]
    assert not list(tmp_path.glob(".hermes-gateway.service.hal0-*"))


def test_a_failed_step_names_what_was_already_done(tmp_path: Path) -> None:
    _old_unit(tmp_path)
    systemctl = FakeSystemctl(enabled="enabled", active=True, fail="enable-linger")
    with pytest.raises(regen.GatewayRegenError) as exc:
        _regen(tmp_path, systemctl, linger=False)
    assert "enable-linger" in str(exc.value)
    assert "unit rewritten" in str(exc.value)
    # Never restarted after a failure.
    assert ["systemctl", "restart", "hermes-gateway.service"] not in systemctl.calls


def test_root_side_reads_nothing_from_the_release_tree(tmp_path: Path, monkeypatch: Any) -> None:
    """Root never opens a path under /var/lib/hal0/hermes or ~/.hermes."""
    _old_unit(tmp_path)
    opened: list[str] = []
    real_read = Path.read_text

    def spy(self: Path, *a: Any, **kw: Any) -> str:
        opened.append(str(self))
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    _regen(tmp_path, FakeSystemctl(enabled="enabled", active=True), linger=False)
    assert opened == [str(tmp_path / "hermes-gateway.service")]
