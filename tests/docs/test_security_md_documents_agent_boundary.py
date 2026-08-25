"""``SECURITY.md`` must state the bundled agent's trust boundary (ADR-0002).

hal0 1.0 ships a bundled agent with ``terminal.backend = local`` running as
``User=hal0`` — the same user ``hal0-api.service`` runs as. Same UID means the
agent can read the API process's ``/proc/<pid>/environ`` and therefore every
credential the API holds; the ``hal0`` account additionally owns the privileged
seams that author rootful podman slots. That is an accepted risk for 1.0
(ADR-0002 Option C), and an accepted risk is only accepted if it is written
down where an operator will read it.

Before this, ``SECURITY.md`` was a reporting-process page only: nothing in the
repo told an operator that giving the bundled agent untrusted content is
equivalent to giving it the box. This test pins the disclosure so a later tidy
of ``SECURITY.md`` cannot quietly delete it, and pins the honesty requirement
in both directions — the compensating controls have to be named *and* their
limits have to be named.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_MD = _REPO_ROOT / "SECURITY.md"

_SECTION = "## Bundled agent trust boundary"


def _boundary_section(text: str) -> str:
    """Slice out the trust-boundary section (up to the next ``## `` heading)."""
    start = text.index(_SECTION)
    rest = text[start:]
    nxt = rest.find("\n## ", 1)
    return rest if nxt == -1 else rest[:nxt]


def _text() -> str:
    assert _SECURITY_MD.exists(), f"missing {_SECURITY_MD}"
    return _SECURITY_MD.read_text(encoding="utf-8")


def test_security_md_has_a_bundled_agent_section() -> None:
    assert _SECTION in _text(), (
        f"{_SECURITY_MD.name} must carry a '{_SECTION}' section — the 1.0 posture is a "
        "documented accepted risk, and undocumented is not accepted"
    )


def test_section_names_the_shared_uid_and_the_proc_read() -> None:
    section = _boundary_section(_text())
    for token in ("hal0-api", "hal0-agent@", "/proc/", "terminal.backend"):
        assert token in section, f"trust-boundary section never mentions {token!r}"
    assert "ptrace_scope" in section, (
        "the section must say that ptrace_scope does not prevent the same-UID read — "
        "otherwise a reader assumes a hardening knob they already have covers this"
    )


def test_section_names_compensating_controls_and_their_limits() -> None:
    """Honest in both directions: controls named, limits named."""
    section = _boundary_section(_text())
    for control in ("NoNewPrivileges", "ProtectSystem", "podman"):
        assert control in section, f"compensating-control inventory omits {control!r}"
    assert "Does not buy" in section, (
        "the section lists controls without listing what they do not buy — that is the "
        "overstatement this disclosure exists to avoid"
    )


def test_section_declares_the_status_and_links_the_adr() -> None:
    section = _boundary_section(_text())
    assert "accepted" in section.lower(), "the section never states this is an accepted risk"
    assert "1.1" in section, "the section never says the agent-UID split is tracked for 1.1"
    assert "docs/adr/0002-agent-credential-isolation.md" in section, (
        "the section must link ADR-0002, which carries the evidence and the rejected alternatives"
    )


def test_section_points_at_the_doctor_row() -> None:
    section = _boundary_section(_text())
    assert "Agent UID split" in section, (
        "the doctor row and the docs must name each other — the row's detail string "
        "sends the operator here"
    )
