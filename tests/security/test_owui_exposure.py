"""Issues #1515 / #1514: the second web UI nobody was told about.

`hal0-openwebui.service` publishes a full chat UI — model access, and
persistent conversation storage in `/var/lib/hal0/openwebui` — on
`0.0.0.0:3001` with `WEBUI_AUTH=False`. Three separate things made that a
posture gap rather than a documented trade-off:

1. **The bind was hardcoded.** `-p 0.0.0.0:3001:8080` in the unit, with no
   variable in it. `hal0.install.network`'s docstring states the project's
   own rule — *"One `HAL0_BIND_HOST` drives BOTH…"* — and an operator who
   set `HAL0_BIND_HOST=127.0.0.1` to keep hal0 off the LAN got exactly what
   they asked for on :8080 and a wide-open chat UI on :3001 anyway.
2. **The documented mitigation had no caller.** `env_writer`'s module
   docstring tells operators to "pass `WEBUI_AUTH=True` +
   `WEBUI_AUTH_TRUSTED_EMAIL_HEADER=<name>` via the `overrides` parameter at
   install / setting time" — but nothing passes a non-empty `overrides`:
   not `install.sh`, not `install_openwebui()`, and there is no CLI flag or
   settings route. The only remaining path, hand-editing
   `/etc/hal0/openwebui.env`, was erased on the next `install.sh` run
   (#1514), so the instruction was not merely inconvenient, it was
   self-defeating.
3. **The security guide never mentioned the port.** `docs/operate/auth.mdx`
   documents `hal0-api` on `0.0.0.0:8080` and stops there, so an operator
   who follows the hardening guide to completion still leaves the chat UI
   open.

What this file pins: the bind is operator-controlled and derives from the
*same* choice as the API's; the trusted-header posture is reachable from a
shipped interface; and the docs name the port. The default bind is
deliberately unchanged (`0.0.0.0`, matching `network.DEFAULT_BIND_HOST`) —
this is a "stop ignoring the operator's choice" fix, not a silent
behaviour flip that would strand every existing LAN user.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hal0.install.network import DEFAULT_BIND_HOST
from hal0.openwebui.env_writer import default_openwebui_env

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT = REPO_ROOT / "packaging" / "systemd" / "hal0-openwebui.service"
AUTH_DOC = REPO_ROOT / "docs" / "operate" / "auth.mdx"
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"

BIND_VAR = "HAL0_OWUI_BIND_HOST"


# ── 1. the publish address is a variable, defaulted in the unit ──────────────


def test_unit_publishes_through_a_variable_not_a_hardcoded_wildcard() -> None:
    """The #1515 repro line: `-p 0.0.0.0:3001:8080`."""
    text = UNIT.read_text(encoding="utf-8")
    assert "-p 0.0.0.0:3001:8080" not in text
    assert f"-p ${{{BIND_VAR}}}:3001:8080" in text


def test_unit_carries_an_inline_default_for_the_bind_variable() -> None:
    """systemd has no `${VAR:-default}` in ExecStart: an unset variable
    expands to empty and podman would see `-p :3001:8080`. The unit must
    carry its own `Environment=` default, declared BEFORE the
    EnvironmentFile that may override it."""
    text = UNIT.read_text(encoding="utf-8")
    default_line = f"Environment={BIND_VAR}={DEFAULT_BIND_HOST}"
    assert default_line in text
    # The unit already sourced openwebui.env before #1515; the default just
    # has to land ahead of it. Matched on the directive line itself so a
    # mention in a comment cannot satisfy the ordering assertion.
    env_file = next(line for line in text.splitlines() if line.startswith("EnvironmentFile="))
    assert env_file.endswith("/etc/hal0/openwebui.env")
    assert text.index(default_line) < text.index(env_file), (
        "systemd applies Environment=/EnvironmentFile= in order, last wins; "
        "the inline default must come first or it would clobber the "
        "operator's file value"
    )


def test_unit_sources_exactly_one_environment_file() -> None:
    """A second EnvironmentFile is how the api.env secrets would sneak in."""
    text = UNIT.read_text(encoding="utf-8")
    directives = [ln for ln in text.splitlines() if ln.startswith("EnvironmentFile=")]
    assert len(directives) == 1, directives


def test_unit_does_not_source_the_secret_bearing_api_env() -> None:
    """The bind choice lives in `openwebui.env`, NOT `api.env`. Sourcing
    api.env here would be the cheapest way to reach `HAL0_BIND_HOST` and
    would push every provider token in that file into the podman process
    environment — re-spreading the secrets #1466 just contained."""
    text = UNIT.read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/hal0/api.env" not in text
    assert "/etc/hal0/api.env" not in text


# ── 2. one bind choice drives both surfaces ─────────────────────────────────


def test_bind_var_defaults_to_the_shared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)
    assert default_openwebui_env()[BIND_VAR] == DEFAULT_BIND_HOST


def test_bind_var_follows_the_api_bind_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The heart of #1515: an operator who binds the API to loopback meant
    it for the whole box, and got a public chat UI regardless."""
    monkeypatch.setenv("HAL0_BIND_HOST", "127.0.0.1")
    assert default_openwebui_env()[BIND_VAR] == "127.0.0.1"


def test_bind_var_ignores_a_blank_bind_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `HAL0_BIND_HOST=` must not expand to `-p :3001:8080`."""
    monkeypatch.setenv("HAL0_BIND_HOST", "   ")
    assert default_openwebui_env()[BIND_VAR] == DEFAULT_BIND_HOST


# ── 3. the trusted-header posture is reachable ──────────────────────────────


def test_trusted_email_header_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged default posture: no login page, auth is upstream's job."""
    monkeypatch.delenv("HAL0_OWUI_TRUSTED_EMAIL_HEADER", raising=False)
    env = default_openwebui_env()
    assert env["WEBUI_AUTH"] == "False"
    assert "WEBUI_AUTH_TRUSTED_EMAIL_HEADER" not in env


def test_trusted_email_header_flips_auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming the header is the whole opt-in — `WEBUI_AUTH=True` without a
    header would be a login page with no identity source behind it, and
    `WEBUI_AUTH=False` with a header would ignore the header. One knob."""
    monkeypatch.setenv("HAL0_OWUI_TRUSTED_EMAIL_HEADER", "X-Forwarded-Email")
    env = default_openwebui_env()
    assert env["WEBUI_AUTH"] == "True"
    assert env["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] == "X-Forwarded-Email"


def test_blank_trusted_email_header_is_not_an_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_OWUI_TRUSTED_EMAIL_HEADER", "  ")
    env = default_openwebui_env()
    assert env["WEBUI_AUTH"] == "False"
    assert "WEBUI_AUTH_TRUSTED_EMAIL_HEADER" not in env


def test_installer_threads_both_knobs_through_to_the_writer() -> None:
    """A knob the installer cannot set is the defect, not the fix."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "HAL0_OWUI_TRUSTED_EMAIL_HEADER" in text
    assert "HAL0_BIND_HOST=" in text


# ── 4. the docstring stops pointing at a parameter nobody passes ────────────


def test_env_writer_docstring_names_a_reachable_path() -> None:
    from hal0.openwebui import env_writer

    doc = env_writer.__doc__ or ""
    assert "via the `overrides` parameter" not in doc, (
        "the docstring's instruction must name an interface that exists"
    )
    assert "HAL0_OWUI_TRUSTED_EMAIL_HEADER" in doc


def test_env_writer_docstring_does_not_claim_a_settings_route() -> None:
    """`env_writer` says it is "called by the installer and (on settings
    changes) by the Settings API route". There is no such route."""
    from hal0.openwebui import env_writer

    assert "Settings API route" not in (env_writer.__doc__ or "")


# ── 5. the security guide names the port ────────────────────────────────────


def test_auth_doc_documents_the_openwebui_port() -> None:
    text = AUTH_DOC.read_text(encoding="utf-8")
    assert "3001" in text, "the hardening guide never mentions the second UI"
    assert re.search(r"open[- ]?webui", text, re.I), "the port needs a name attached"


def test_auth_doc_names_the_bind_and_header_knobs() -> None:
    """A callout that says "this is open" without saying what to do about it
    sends the operator back to the code."""
    text = AUTH_DOC.read_text(encoding="utf-8")
    assert BIND_VAR in text
    assert "HAL0_OWUI_TRUSTED_EMAIL_HEADER" in text
