"""Guard for #2081: installer shell code calling a reporter helper that its
own scope never defines.

persist_bootstrap_cosign's success branch called ``ok "..."`` — ui.sh defines
info/warn/err/die and no ``ok()``, so under ``set -e`` the 127 killed every
fresh bootstrap install that had no system cosign, at pre-flight 1/16, right
after the cosign was successfully persisted. Hosts with a system cosign
early-return past the line, which is why every CI and dev install missed it.

The first version of this guard resolved definitions GLOBALLY across
``installer/**/*.sh`` — and installer/bootstrap.sh defines a private ``ok()``
reporter of its own (it is piped from curl and sources nothing), so ``ok``
counted as defined everywhere and the guard stayed green over the exact bug
it existed to pin (verified by reverting the #2082 fix under it). Definitions
are therefore resolved per scope, matching the real sourcing topology:

* ``bootstrap.sh`` is standalone — its calls resolve only against its own
  definitions, and its definitions satisfy nobody else.
* Every other installer shell source runs inside install.sh's process after
  the lib set (ui.sh included) is sourced — their calls resolve against the
  union of all non-bootstrap installer sources.

The check stays deliberately dumb: flag any command-position ``<word> "...``
line whose word looks like a reporter call (a known reporter name or the
short lowercase shape ok/okay/success/fail/note) that its scope does not
define.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "installer"
BOOTSTRAP = INSTALLER / "bootstrap.sh"

REPORTERS_DEFINED_IN_UI = {"info", "warn", "err", "die"}
# Lowercase bare words that read as a status reporter; extend when a new
# helper is added to ui.sh (and add it to REPORTERS_DEFINED_IN_UI).
REPORTER_SHAPED = {"ok", "okay", "success", "fail", "note"} | REPORTERS_DEFINED_IN_UI

FUNC_DEF = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{?")
CALL = re.compile(r'^\s*([a-z_][a-z0-9_]*)\s+"')


def shell_sources():
    files = sorted(INSTALLER.rglob("*.sh"))
    assert files, f"no installer shell sources under {INSTALLER}"
    return files


def defs_in(path: Path) -> set[str]:
    return {m.group(1) for line in path.read_text().splitlines() if (m := FUNC_DEF.match(line))}


def test_ui_defines_the_reporter_contract():
    defined = defs_in(INSTALLER / "lib" / "ui.sh")
    missing = REPORTERS_DEFINED_IN_UI - defined
    assert not missing, f"ui.sh no longer defines expected reporters: {sorted(missing)}"


def test_no_reporter_shaped_call_without_a_definition_in_scope():
    bootstrap_scope = defs_in(BOOTSTRAP)
    shared_scope: set[str] = set()
    for f in shell_sources():
        if f == BOOTSTRAP:
            continue
        shared_scope |= defs_in(f)

    offenders = []
    for f in shell_sources():
        scope = bootstrap_scope if f == BOOTSTRAP else shared_scope
        for n, line in enumerate(f.read_text().splitlines(), 1):
            m = CALL.match(line)
            if not m:
                continue
            word = m.group(1)
            if word in REPORTER_SHAPED and word not in scope:
                offenders.append(f"{f.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, (
        "reporter-shaped call to a helper its scope does not define "
        "(#2081 class — this dies with 127 under set -e at runtime):\n" + "\n".join(offenders)
    )


def test_the_guard_actually_catches_the_2081_shape():
    """Self-test: the exact #2081 line must be flagged when resolved against
    the non-bootstrap scope, even though bootstrap.sh defines ok() —
    the global-resolution mistake that made the first guard inert."""
    shared_scope: set[str] = set()
    for f in shell_sources():
        if f == BOOTSTRAP:
            continue
        shared_scope |= defs_in(f)
    assert "ok" not in shared_scope, (
        "a non-bootstrap installer source now defines ok(); if that is "
        "deliberate, add it to REPORTERS_DEFINED_IN_UI and update ui.sh — "
        "otherwise this guard just went blind to the #2081 class"
    )
    assert "ok" in defs_in(BOOTSTRAP), (
        "bootstrap.sh no longer defines its private ok(); update this "
        "self-test to another bootstrap-only reporter"
    )
