"""Shared test fakes for the hermes_provision config-set redesign.

``apply_hermes_config_cli`` mirrors ``hermes config set`` / ``config migrate``
against a real ``$HERMES_HOME/config.yaml`` (including hermes's value coercion),
so phase + pipeline tests can assert on the resulting file exactly as the live
CLI would leave it — without a real hermes binary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _coerce(value: str) -> Any:
    """Mirror ``hermes config set`` value coercion (verified on 0.17)."""
    if value in ("true", "false"):
        return value == "true"
    try:
        return int(value)
    except ValueError:
        return value


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    cur = data
    parts = dotted.split(".")
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def apply_hermes_config_cli(argv: list[str], env: dict[str, str] | None) -> bool:
    """Apply a ``hermes config set/migrate`` argv to ``$HERMES_HOME/config.yaml``.

    Returns True if it handled the argv (a config verb), False otherwise so a
    caller can fall through to other interception or a real subprocess.
    """
    import yaml

    home = (env or {}).get("HERMES_HOME")
    cfg = Path(home) / "config.yaml" if home else None
    verb = argv[1:3]
    if verb == ["config", "migrate"]:
        if cfg is not None and not cfg.exists():
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("{}\n")
        return True
    if verb == ["config", "set"] and cfg is not None:
        data = (yaml.safe_load(cfg.read_text()) if cfg.exists() else None) or {}
        _set_dotted(data, argv[3], _coerce(argv[4]))
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(yaml.safe_dump(data, sort_keys=False))
        return True
    return False


def fake_hermes_run(record: list[list[str]] | None = None):
    """A stand-in for ``subprocess.run`` that applies hermes config verbs."""

    def run(argv: Any, *_a: Any, env: Any = None, **_kw: Any) -> Any:
        argv = list(argv)
        if record is not None:
            record.append(argv)
        apply_hermes_config_cli(argv, env)
        return _Completed()

    return run


# ── install_hermes hermetic harness ──────────────────────────────────────────
#
# Shared by the rewritten test_hermes_provision*.py suite: point every module
# path constant at ``tmp_path`` and build an ``InstallIO`` whose seams are
# deterministic + record their mutating calls, so a two-run convergence check
# is byte-exact and hermetic.


def sandbox_hermes_paths(hp, tmp_path, monkeypatch):
    """Redirect every host path constant under ``tmp_path``; return (home, venv).

    The path constants stay module-level by design (tests redirect them); only
    behavioural IO lives in ``InstallIO``.
    """
    from pathlib import Path

    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True, exist_ok=True)
    venv = var_lib / "venvs" / "hermes"
    hermes_home = var_lib / "agents" / "hermes"

    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", tmp_path / "usr" / "bin" / "hal0-hermes")
    monkeypatch.setattr(hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "bin" / "hermes")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "etc" / "hal0" / "overrides.yaml")
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path / "etc" / "hal0")
    monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", tmp_path / "etc" / "hal0" / "agent-skills")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "usr" / "share" / "hal0" / "skills")
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", tmp_path / "secrets" / "hermes.env")
    monkeypatch.setattr(hp, "AGENT_ALLOWLIST_PATH", tmp_path / "etc" / "hal0" / "agents.toml")
    monkeypatch.setattr(
        hp, "INSTALL_SEED_PATH", tmp_path / "etc" / "hal0" / "agents" / "hermes.toml"
    )
    monkeypatch.setattr(hp, "DRIVER_ENV_PATH", tmp_path / "etc" / "hal0" / "agents" / "hermes.env")
    dropin_dir = tmp_path / "etc" / "systemd" / "system" / "hermes-gateway.service.d"
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_FILE", dropin_dir / "10-hal0-secrets.conf")

    # Wrapper source must exist for the install step; drop a stub if absent.
    wrapper_src = hp.REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hermes"
    if not wrapper_src.exists():
        wrapper_src.parent.mkdir(parents=True, exist_ok=True)
        wrapper_src.write_text("#!/bin/sh\nexit 0\n")
        wrapper_src.chmod(0o755)
    return Path(hermes_home), Path(venv)


def default_fake_slots() -> list[dict[str, Any]]:
    """Ready chat/agent/utility slots + an embed slot that must never alias."""
    return [
        {
            "name": "chat",
            "type": "llm",
            "state": "ready",
            "model_id": "qwen3-test",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 32768,
        },
        {
            "name": "agent",
            "type": "llm",
            "state": "ready",
            "model_id": "qwen3-coder-test",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 16384,
        },
        {
            "name": "utility",
            "type": "llm",
            "state": "ready",
            "model_id": "qwen3-utility-test",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 8192,
        },
        {
            "name": "embed",
            "type": "embedding",
            "state": "ready",
            "model_id": "bge-test",
            "backend_url": "http://127.0.0.1:8002/v1",
        },
    ]


def install_io(hp, *, record: list[list[str]] | None = None, slots=None):
    """A deterministic :class:`hp.InstallIO` for hermetic ``install_hermes`` runs.

    When ``record`` is passed, every ``run`` argv the pipeline issues is appended
    to it (the "recorded fakes" the convergence test asserts on).
    """
    import subprocess

    slot_list = default_fake_slots() if slots is None else slots

    def _install_venv(v, _req, **_kw):
        (v / "bin").mkdir(parents=True, exist_ok=True)
        for name in ("hermes", "python"):
            (v / "bin" / name).write_text("#!/bin/sh\nexit 0\n")
            (v / "bin" / name).chmod(0o755)

    def _memory(method, params, **_kw):
        tool = (params or {}).get("name", "")
        if tool == "memory_search":
            return {"ok": True, "result": {"items": []}}
        if tool == "memory_add":
            return {"ok": True, "result": {"id": "memid-stable"}}
        if tool == "memory_delete":
            return {"ok": True, "result": {"deleted": 0}}
        return {"ok": True, "result": {}}

    real_run = subprocess.run

    class _C:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(argv, *a, **kw):
        argv = list(argv)
        if record is not None:
            record.append(argv)
        if argv[:2] == ["systemctl", "daemon-reload"]:
            return _C()
        if apply_hermes_config_cli(argv, kw.get("env")):
            return _C()
        return real_run(argv, *a, **kw)

    return hp.InstallIO(
        http_get=lambda *a, **k: 200,
        fetch_slots=lambda: slot_list,
        fetch_model_contexts=lambda: {},
        probe_mcp_server=lambda *a, **k: {
            "ok": True,
            "tools": ["t1", "t2", "t3", "t4", "t5"],
            "error": None,
        },
        mcp_memory_call=_memory,
        install_venv=_install_venv,
        read_env_probe=lambda: {
            "env_report": {"cpu": {"strix_halo": True}},
            "gpu_target_version": {"gfx": "1151"},
            "npu_status": {"present": True},
            "ai_models": {"present": False},
        },
        run=_run,
    )
