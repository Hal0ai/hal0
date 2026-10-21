"""``hal0 agent upgrade hermes`` goes through the release builder (ADR-0032 decision 6).

``--to`` takes only a full 40-hex commit listed in ``VETTED_HERMES_REFS``; a
version string, an ``==X.Y.Z`` spec, a tag or an unlisted commit is refused
before anything runs. A valid call drops to hal0 through ``_provision_hermes``
with ``repair=True`` and the ref, the same entry point every other
provisioning command uses, and the re-exec uses the running tree's ``hal0``
rather than whatever ``hal0`` is first on PATH (#2092).
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

import hal0.cli.agent_commands as ac
from hal0.agents import hermes_provision as hp

PIN = "4d04303818b61bf3aa09278af48356315515965b"  # the shipped pin, v0.22.4
PREV_PIN = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"  # v0.22.2, still vetted


@pytest.fixture
def provision_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake(**kw: Any) -> int:
        calls.append(kw)
        return 0

    monkeypatch.setattr(ac, "_provision_hermes", _fake)
    return calls


def _upgrade(*args: str) -> Any:
    return CliRunner().invoke(ac.app, ["upgrade", "hermes", *args])


def test_upgrade_without_to_rebuilds_the_pin(provision_calls: list[dict[str, Any]]) -> None:
    result = _upgrade()
    assert result.exit_code == 0, result.output
    assert provision_calls == [{"repair": True, "ref": None}]


def test_upgrade_to_a_vetted_sha_passes_the_ref(provision_calls: list[dict[str, Any]]) -> None:
    result = _upgrade("--to", PIN)
    assert result.exit_code == 0, result.output
    assert provision_calls == [{"repair": True, "ref": PIN}]


def test_upgrade_to_the_previous_pin_stays_allowed(provision_calls: list[dict[str, Any]]) -> None:
    # v0.22.2 stays in VETTED_HERMES_REFS after the v0.22.4 bump, so an operator
    # can still build it back with --to until the next `hal0 update`.
    result = _upgrade("--to", PREV_PIN)
    assert result.exit_code == 0, result.output
    assert provision_calls == [{"repair": True, "ref": PREV_PIN}]


@pytest.mark.parametrize(
    "to",
    [
        "0.19.0",
        "==0.19.0",
        "hermes-agent==0.19.0",
        "v2026.7.20",
        PIN[:12],
        "f" * 40,  # well-formed but not vetted
        PIN.upper(),
    ],
)
def test_upgrade_refuses_anything_but_a_vetted_sha(
    to: str, provision_calls: list[dict[str, Any]]
) -> None:
    result = _upgrade("--to", to)
    assert result.exit_code != 0
    assert provision_calls == [], "a refused --to must not reach provisioning"
    assert "VETTED_HERMES_REFS" in result.output


def test_upgrade_runtime_and_floor_are_gone() -> None:
    assert not hasattr(hp, "upgrade_hermes_runtime")
    assert not hasattr(hp, "HERMES_MIN_VERSION")


def test_root_upgrade_reexecs_the_running_hal0_with_the_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As root, ``--ref`` rides the privilege-dropped re-exec of the running tree."""
    seen: list[list[str]] = []
    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ac, "_hermes_root_prelude", lambda _env=None: None)
    monkeypatch.setattr(ac, "_running_hal0_bin", lambda: "/usr/lib/hal0/venv/bin/hal0")
    monkeypatch.setattr(ac, "_run_as_hal0", lambda argv, **_kw: seen.append(list(argv)) or 0)
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    rc = ac._provision_hermes(repair=True, ref=PIN)
    assert rc == 0
    assert seen == [
        ["/usr/lib/hal0/venv/bin/hal0", "agent", "bootstrap", "hermes", "--repair", "--ref", PIN]
    ]


def test_requirement_is_a_vetted_commit_pin() -> None:
    """The shipped requirement pins a reviewed commit, the only accepted form."""
    text = hp.HERMES_REQUIREMENTS.read_text(encoding="utf-8")
    reqs = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(reqs) == 1, f"expected a single hermes requirement, got: {reqs!r}"
    ref = hp.hermes_pinned_ref(hp._hermes_requirement_line(text))
    assert ref in hp.VETTED_HERMES_REFS
    assert hp.hermes_requirement_is_vetted(text)
    # A floor no longer counts: it cannot name a commit to check out.
    assert not hp.hermes_requirement_is_vetted("hermes-agent[web,mcp]>=0.16.0,<1")
