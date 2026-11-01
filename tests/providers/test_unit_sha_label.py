"""#3130 — the unit-identity stamp and the drift diff built on it."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.providers.base import RuntimeLaunchPlan
from hal0.providers.container import (
    UNIT_SHA_LABEL,
    _render_quadlet_from_plan,
    unit_sha_of_text,
    unit_text_sha,
)
from hal0.slots.drift import _unit_identity_diff, _users_match, load_would_restart

REPO = Path(__file__).resolve().parents[2]
SYSTEMCTL = REPO / "installer" / "wrappers" / "hal0-systemctl"
PODMAN_RO = REPO / "installer" / "wrappers" / "hal0-podman-ro"


def _plan(**kw: Any) -> RuntimeLaunchPlan:
    base: dict[str, Any] = {"image": "ghcr.io/x/y:1", "command": ["--port", "8080"], "port": 8080}
    base.update(kw)
    return RuntimeLaunchPlan(**base)


def test_label_hashes_the_rest_of_the_unit() -> None:
    text = _render_quadlet_from_plan("chat", _plan())
    sha = unit_sha_of_text(text)
    assert sha and re.fullmatch(r"[0-9a-f]{64}", sha)
    body = "\n".join(ln for ln in text.split("\n") if not ln.startswith(f"Label={UNIT_SHA_LABEL}="))
    assert sha == unit_text_sha(body)


def test_hash_is_stable_across_renders() -> None:
    a = _render_quadlet_from_plan("chat", _plan(env={"A": "1"}))
    b = _render_quadlet_from_plan("chat", _plan(env={"A": "1"}))
    assert a == b


@pytest.mark.parametrize(
    "change",
    [{"user": "996:989"}, {"env": {"A": "2"}}, {"group_add": [44]}, {"command": ["--x", "1"]}],
)
def test_hash_moves_with_identity_env_groups_and_exec(change: dict[str, Any]) -> None:
    base = unit_sha_of_text(_render_quadlet_from_plan("chat", _plan(env={"A": "1"})))
    assert (
        unit_sha_of_text(_render_quadlet_from_plan("chat", _plan(**{"env": {"A": "1"}, **change})))
        != base
    )


class _Prov:
    def __init__(self, running: Any, expected: Any) -> None:
        self._r, self._e = running, expected

    def running_identity(self, cfg: Any) -> Any:
        return self._r

    def expected_identity(self, cfg: Any, info: Any) -> Any:
        return self._e


def _diff(running: Any, expected: Any) -> Any:
    return _unit_identity_diff(_Prov(running, expected), {}, {})


def test_labelled_container_with_other_sha_drifts() -> None:
    d = _diff({"user": "", "unit_sha": "a" * 64}, {"user": "", "unit_sha": "b" * 64})
    assert d and d["key"] == "unit"


def test_labelled_container_with_same_sha_is_clean() -> None:
    assert _diff({"user": "", "unit_sha": "a" * 64}, {"user": "9:9", "unit_sha": "a" * 64}) is None


def test_unlabelled_container_is_not_drift_by_itself() -> None:
    # Every container on the first update has no label: must stay quiet.
    assert _diff({"user": "", "unit_sha": None}, {"user": "", "unit_sha": "b" * 64}) is None


def test_unlabelled_root_container_under_pinned_user_drifts() -> None:
    # The #3130 case: img running as root while the unit says User=996:989.
    d = _diff({"user": "", "unit_sha": None}, {"user": "996:989", "unit_sha": "b" * 64})
    assert d == {"key": "unit", "running": "user=root", "rendered": "user=996:989"}


def test_unlabelled_container_already_on_pinned_user_is_clean() -> None:
    assert (
        _diff({"user": "996:989", "unit_sha": None}, {"user": "996:989", "unit_sha": "b" * 64})
        is None
    )
    assert _users_match("996", "996:989")
    assert not _users_match("0", "996:989")


@pytest.mark.parametrize(
    "running,expected", [(None, {"user": "", "unit_sha": "b"}), ({"user": ""}, None)]
)
def test_unanswerable_side_is_not_drift(running: Any, expected: Any) -> None:
    assert _diff(running, expected) is None


def _quadlet_check(line: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SYSTEMCTL), "check-quadlet"],
        input=f"[Container]\nImage=x/y:1\n{line}\nExec=a\n",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def test_wrapper_accepts_only_the_unit_sha_label() -> None:
    ok = _quadlet_check(f"Label={UNIT_SHA_LABEL}={'a' * 64}")
    assert ok.returncode == 0, ok.stderr
    for bad in (
        "Label=io.hal0.unit-sha=zz",
        "Label=other=" + "a" * 64,
        f"Label={UNIT_SHA_LABEL}={'a' * 63}",
    ):
        assert _quadlet_check(bad).returncode == 64, bad


def test_real_render_passes_the_wrapper() -> None:
    text = _render_quadlet_from_plan("chat", _plan(env={"A": "1"}))
    proc = subprocess.run(
        [str(SYSTEMCTL), "check-quadlet"],
        input=text,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr


def test_podman_ro_wires_container_identity() -> None:
    src = PODMAN_RO.read_text()
    assert "container-identity)" in src and "io.hal0.unit-sha" in src


@pytest.mark.parametrize(
    ("drift", "restarts"),
    [
        (None, False),
        ({"drifted": False, "diffs": []}, False),
        ({"drifted": True, "diffs": []}, True),
        ({"drifted": True, "diffs": [{"key": "unit"}]}, False),
        ({"drifted": True, "diffs": [{"key": "--port"}]}, True),
        ({"drifted": True, "diffs": [{"key": "unit"}, {"key": "image"}]}, True),
        ({"drifted": True, "diffs": [{"key": "--port"}], "render_error": "x"}, False),
    ],
)
def test_only_non_unit_drift_makes_a_load_restart(
    drift: dict[str, Any] | None, restarts: bool
) -> None:
    assert load_would_restart(drift) is restarts
