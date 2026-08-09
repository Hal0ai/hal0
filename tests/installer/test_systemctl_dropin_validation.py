"""#1716 — root-side content allow-list for the two drop-in verbs.

``installer/wrappers/hal0-systemctl`` is reachable as root by the unprivileged
``hal0`` service account (packaging/sudoers/hal0-systemctl), so *stdin* is
attacker-controlled at a privilege boundary. Both ``write-gateway-dropin`` and
``write-hindsight-dropin`` used to copy that stdin verbatim into a root-owned
systemd fragment; combined with the wrapper's own ``daemon-reload`` +
``svc-restart`` verbs that is a direct path to root (``ExecStart=`` / ``User=``
override) from an ``hal0``-level compromise.

The fix is an allow-list parsed on the ROOT side of the boundary. These tests
exercise the *real* bash wrapper through its side-effect-free ``check-dropin``
verb (the same validator the write arms call), so they need no root, no sudo
and no provisioned box — the ``hal0-update`` wrapper suite's posture.

The legitimate bodies are imported from the actual call sites rather than
retyped, so a template change that the allow-list would reject fails here
instead of on a production host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hal0.agents.hermes_provision import _gateway_dropin_body
from hal0.memory.extraction_env import render_drop_in

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"


def _check(kind: str, body: str) -> subprocess.CompletedProcess[str]:
    """Run the real wrapper's validator over ``body``. rc 0 = accepted."""
    return subprocess.run(
        [str(WRAPPER), "check-dropin", kind],
        input=body,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


def _accepts(kind: str, body: str) -> None:
    proc = _check(kind, body)
    assert proc.returncode == 0, f"rejected: {proc.stderr}"
    # The write arms persist the VALIDATED reconstruction, not the raw stdin,
    # so what is echoed here is byte-for-byte what lands in /etc.
    assert proc.stdout == body


def _rejects(kind: str, body: str) -> str:
    proc = _check(kind, body)
    assert proc.returncode == 64, f"accepted (rc={proc.returncode}): {body!r}"
    assert "drop-in rejected" in proc.stderr
    return proc.stderr


# ── the wrapper still parses ───────────────────────────────────────────────


def test_wrapper_is_syntactically_valid() -> None:
    proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_both_write_arms_validate_before_writing() -> None:
    """The validator must be on the write path, not merely available: a
    ``check-dropin`` verb nothing calls would be security theatre."""
    text = WRAPPER.read_text()
    for verb in ("write-gateway-dropin", "write-hindsight-dropin"):
        arm = text.split(f"  {verb})", 1)[1].split("\n    ;;", 1)[0]
        assert "validate_dropin_body" in arm, f"{verb} does not validate its body"
        # …and it writes the validated reconstruction, never raw stdin.
        assert "DROPIN_BODY" in arm
        assert "cat > " not in arm


# ── the real payloads both call sites produce ──────────────────────────────


def test_live_hindsight_body_is_accepted_byte_for_byte() -> None:
    _accepts("hindsight", render_drop_in("chat"))


@pytest.mark.parametrize("slot", ["chat", "graph-extract", "slot_1", "Mixed-Case"])
@pytest.mark.parametrize("timeout_s", [300, 60, 1800])
def test_live_hindsight_body_variants_are_accepted(slot: str, timeout_s: int) -> None:
    _accepts("hindsight", render_drop_in(slot, timeout_s))


def test_live_gateway_body_is_accepted_byte_for_byte() -> None:
    _accepts("gateway", _gateway_dropin_body())


# ── the escalation payloads ────────────────────────────────────────────────

_MODEL = "Environment=HINDSIGHT_API_LLM_MODEL=hal0/chat\n"

ESCALATIONS = {
    "execstart-override": "[Service]\nExecStart=\nExecStart=/bin/sh -c 'id>/tmp/pwn'\n",
    "execstartpre": f"[Service]\n{_MODEL}ExecStartPre=/bin/sh -c 'id>/tmp/pwn'\n",
    "user-root": f"[Service]\n{_MODEL}User=root\n",
    "group-root": f"[Service]\n{_MODEL}Group=root\n",
    "extra-section": f"[Unit]\nDescription=x\n[Service]\n{_MODEL}",
    "second-service-section": f"[Service]\n{_MODEL}[Service]\nExecStart=/bin/sh\n",
    "install-section": f"[Service]\n{_MODEL}[Install]\nWantedBy=multi-user.target\n",
    "line-continuation": "[Service]\nEnvironment=HINDSIGHT_API_LLM_MODEL=hal0/chat \\\nExecStart=/bin/sh\n",
    "carriage-return-smuggling": "[Service]\r\nExecStart=/bin/sh\n",
    "cr-inside-directive": "[Service]\nEnvironment=HINDSIGHT_API_LLM_MODEL=hal0/chat\rUser=root\n",
    "environment-key-smuggling": "[Service]\nEnvironment=LD_PRELOAD=/tmp/evil.so\n",
    "environment-multi-assignment": (
        "[Service]\nEnvironment=HINDSIGHT_API_LLM_MODEL=hal0/chat LD_PRELOAD=/tmp/e.so\n"
    ),
    "environment-quoted-value": '[Service]\nEnvironment="HINDSIGHT_API_LLM_MODEL=hal0/chat"\n',
    "environment-file-injection": "[Service]\nEnvironmentFile=/tmp/attacker.env\n",
    "leading-whitespace-directive": f"[Service]\n{_MODEL}  User=root\n",
    "spaced-key": f"[Service]\n{_MODEL}User = root\n",
    "directive-before-section": f"{_MODEL}[Service]\n",
    "no-section": "ExecStart=/bin/sh\n",
    "semicolon-comment-then-directive": f"[Service]\n{_MODEL}; note\nUser=root\n",
    "tab-indented-directive": f"[Service]\n{_MODEL}\tUser=root\n",
    "empty-body": "",
    "section-only": "[Service]\n",
    "timeout-not-a-number": "[Service]\nEnvironment=HINDSIGHT_API_LLM_TIMEOUT=$(id)\n",
    "model-not-a-hal0-slot": "[Service]\nEnvironment=HINDSIGHT_API_LLM_MODEL=/bin/sh\n",
    "model-path-traversal": "[Service]\nEnvironment=HINDSIGHT_API_LLM_MODEL=hal0/../../etc\n",
}


@pytest.mark.parametrize("name", sorted(ESCALATIONS))
def test_hindsight_rejects_escalation_payloads(name: str) -> None:
    _rejects("hindsight", ESCALATIONS[name])


def test_gateway_rejects_arbitrary_environment_file() -> None:
    _rejects("gateway", "[Service]\nEnvironmentFile=/etc/shadow\n")


def test_gateway_rejects_traversal_out_of_the_vault() -> None:
    _rejects("gateway", "[Service]\nEnvironmentFile=-/var/lib/hal0/secrets/../../../tmp/evil\n")


def test_gateway_rejects_execstart() -> None:
    _rejects("gateway", "[Service]\nExecStart=/bin/sh -c 'id>/tmp/pwn'\n")


# ── the two kinds are separate allow-lists ─────────────────────────────────


def test_gateway_body_is_not_accepted_as_a_hindsight_dropin() -> None:
    _rejects("hindsight", _gateway_dropin_body())


def test_hindsight_body_is_not_accepted_as_a_gateway_dropin() -> None:
    _rejects("gateway", render_drop_in("chat"))


def test_unknown_kind_is_refused() -> None:
    proc = _check("nope", "[Service]\n")
    assert proc.returncode == 64
    assert "unknown drop-in kind" in proc.stderr


# ── bounds ─────────────────────────────────────────────────────────────────


def test_absurdly_long_line_is_rejected() -> None:
    _rejects(
        "hindsight", "[Service]\nEnvironment=HINDSIGHT_API_LLM_MODEL=hal0/" + "a" * 4096 + "\n"
    )


def test_absurdly_many_lines_are_rejected() -> None:
    _rejects("hindsight", "[Service]\n" + "# pad\n" * 5000 + _MODEL)


def test_comments_and_blank_lines_are_allowed() -> None:
    _accepts("hindsight", f"# managed by hal0\n#\n\n[Service]\n{_MODEL}")
