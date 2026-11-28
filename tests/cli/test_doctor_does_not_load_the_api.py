"""The doctor never loads the API package (#4062).

Importing any ``hal0.api`` submodule runs the package's ``__init__``, which
builds the whole application: a memory provider, the MCP mounts, chat-template
seeding and an attempt to record the memory layout. A diagnostic must not do
that. The helpers the doctor needs live outside the package
(:mod:`hal0.config.env_guard`, :mod:`hal0.redaction`).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_CLI = Path(__file__).resolve().parents[2] / "src" / "hal0" / "cli"
_DOCTOR_MODULES = ("doctor_all.py", "doctor_bundle.py")


@pytest.mark.parametrize("name", _DOCTOR_MODULES)
def test_doctor_module_has_no_import_from_the_api_package(name: str) -> None:
    tree = ast.parse((_CLI / name).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[:2] == [
            "hal0",
            "api",
        ]:
            found.append(f"{name}:{node.lineno} from {node.module}")
        if isinstance(node, ast.Import):
            found += [
                f"{name}:{node.lineno} import {a.name}"
                for a in node.names
                if a.name.split(".")[:2] == ["hal0", "api"]
            ]
    assert found == []


def test_the_helpers_the_doctor_uses_do_not_load_the_api_package() -> None:
    """Run in a fresh interpreter: this process has usually loaded hal0.api already."""
    code = (
        "import sys\n"
        "import hal0.cli.doctor_all, hal0.cli.doctor_bundle\n"
        "from hal0.config.env_guard import is_process_control_env, is_proxy_env, tls_trust_violation\n"
        "from hal0.redaction import MASK, is_sensitive_key, redact_config\n"
        "assert is_proxy_env('HTTPS_PROXY') and is_process_control_env('LD_PRELOAD')\n"
        "assert redact_config({'api_key': 'x'})['api_key']['value'] == MASK\n"
        "print('hal0.api' in sys.modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "False"


def test_running_every_doctor_check_does_not_load_the_api_package(tmp_path: Path) -> None:
    """The import checks above see only what the doctor modules import at the
    top. A lazy ``import hal0.api...`` inside a function two calls away, a
    ``from hal0 import api`` or an ``importlib`` call would pass them. So run
    the whole check list in a fresh interpreter, against an empty home with no
    API to talk to, and look at what got loaded."""
    code = (
        "import sys\n"
        "import hal0.cli.doctor_all as doctor\n"
        "checks = doctor.build_all_checks()\n"
        "assert len(checks) > 20, len(checks)\n"
        "loaded = sorted(m for m in sys.modules if m == 'hal0.api' or m.startswith('hal0.api.'))\n"
        "print(loaded)\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HAL0_HOME": str(tmp_path / "hal0"),
        # Nothing listens here: every check that asks the API gets a refusal.
        "HAL0_API_URL": "http://127.0.0.1:9",
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "[]"


def test_the_api_modules_still_offer_the_same_names() -> None:
    from hal0.api import _env_store, _redact
    from hal0.config import env_guard

    assert _env_store.tls_trust_violation is env_guard.tls_trust_violation
    assert _env_store.is_process_control_env is env_guard.is_process_control_env
    assert _env_store.TLS_TRUST_ENV_NAMES is env_guard.TLS_TRUST_ENV_NAMES
    import hal0.redaction as redaction

    assert _redact.redact_config is redaction.redact_config
    assert _redact.redact_value is redaction.redact_value
