"""The Hermes release builder's vetting and downgrade rules (ADR-0032 decision 6).

``hal0 update component hermes --target`` (tests/components/test_hermes_arm.py)
takes only a full 40-hex commit listed in ``VETTED_HERMES_REFS``; the builder
refuses to run an older vetted release over a newer one's ``state.db``.
"""

from __future__ import annotations

import pytest

from hal0.agents import hermes_provision as hp

PIN = "4d04303818b61bf3aa09278af48356315515965b"  # the shipped pin, v0.22.4
PREV_PIN = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"  # v0.22.2, still vetted


def test_refuse_downgrade_orders_vetted_refs_by_version() -> None:
    from hal0.agents import hermes_release as hr

    assert hp.VETTED_HERMES_VERSIONS.keys() == set(hp.VETTED_HERMES_REFS)
    with pytest.raises(hr.ReleaseError, match="downgrade"):
        hr.refuse_downgrade(PREV_PIN, live=PIN)
    hr.refuse_downgrade(PIN, live=PREV_PIN)
    hr.refuse_downgrade(PIN, live=PIN)
    hr.refuse_downgrade(PREV_PIN, live=None)
    hr.refuse_downgrade(PREV_PIN, live="f" * 40)  # an unvetted live ref cannot be ordered


def test_upgrade_runtime_and_floor_are_gone() -> None:
    assert not hasattr(hp, "upgrade_hermes_runtime")
    assert not hasattr(hp, "HERMES_MIN_VERSION")


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


def test_ensure_release_refuses_a_downgrade_at_the_build_boundary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``update component hermes --target`` and every other builder pass through ensure_release."""
    from hal0.agents import hermes_release as hr

    monkeypatch.setattr(hr, "live_ref", lambda layout=None: PIN)
    monkeypatch.setattr(hr, "configured_extras", lambda *a, **k: ())
    req = tmp_path / "requirements.txt"
    req.write_text(f"hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@{PIN}\n")
    out = hr.ensure_release(
        caller="test",
        venv=tmp_path / "venv",
        requirements=req,
        ref=PREV_PIN,
        force=True,
        hermes_home=tmp_path / "home",
        units=hr.NoUnits(),
        drop_root=False,
    )
    assert out.status == "build_failed"
    assert out.error is not None and "downgrade" in out.error and "#2805" in out.error
