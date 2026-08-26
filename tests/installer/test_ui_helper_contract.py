"""Guard for #2081: installer shell code calling a reporter helper that ui.sh
never defined.

persist_bootstrap_cosign's success branch called ``ok "..."`` — no ``ok()``
exists anywhere in the installer (ui.sh defines info/warn/err/die), so under
``set -e`` the 127 killed every fresh bootstrap install that had no system
cosign, at pre-flight 1/16, right after the cosign was successfully persisted.
Hosts with a system cosign early-return past the line, which is why every CI
and dev install missed it.

The check is deliberately dumb: collect every function name defined across the
installer's shell sources, then flag any command-position ``<word> "...`` line
whose word looks like a reporter call (matches a known reporter name or the
short lowercase shape ok/okay/success/fail/note) but is neither defined nor a
real command the scripts legitimately use.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "installer"

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


def test_ui_defines_the_reporter_contract():
    ui = (INSTALLER / "lib" / "ui.sh").read_text()
    defined = {m.group(1) for line in ui.splitlines() if (m := FUNC_DEF.match(line))}
    missing = REPORTERS_DEFINED_IN_UI - defined
    assert not missing, f"ui.sh no longer defines expected reporters: {sorted(missing)}"


def test_no_reporter_shaped_call_without_a_definition():
    defined = set()
    for f in shell_sources():
        for line in f.read_text().splitlines():
            if m := FUNC_DEF.match(line):
                defined.add(m.group(1))
    defined |= REPORTERS_DEFINED_IN_UI

    offenders = []
    for f in shell_sources():
        for n, line in enumerate(f.read_text().splitlines(), 1):
            m = CALL.match(line)
            if not m:
                continue
            word = m.group(1)
            if word in REPORTER_SHAPED and word not in defined:
                offenders.append(f"{f.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, (
        "reporter-shaped call to a helper no installer source defines "
        "(#2081 class — this dies with 127 under set -e at runtime):\n"
        + "\n".join(offenders)
    )
