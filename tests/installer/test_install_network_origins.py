"""Installer re-runs keep persisted browser origins (#2382).

``install.sh`` rewrites the marker-delimited ``hal0-network`` block of
``/etc/hal0/api.env`` on every run, ``HAL0_ALLOWED_ORIGINS`` included. A
reverse-proxy origin that lived only there (hand-added, or derived from an
install-time ``HAL0_PUBLIC_URL``) was gone after the next re-run. Now:

* an install-time ``HAL0_PUBLIC_URL`` is persisted once into hal0.toml
  ``[network].public_url`` (never over a set value);
* the persisted ``public_url`` and ``[network].extra_origins`` stay in
  hal0.toml, which the block rewrite never touches, and hal0-api merges them
  in at runtime; and
* the block never carries a copy of the persisted ``public_url``, so
  clearing it (``hal0 network public-url clear``) revokes its origin on the
  next request instead of at the next installer run.

Driven by extracting the real network section of install.sh (from the
``hal0-network-derive`` marker to the end of the api.env refresh) and running
it under ``set -euo pipefail`` with the installer's own ``IFS=$'\\n\\t'``,
against a tmp ``/etc/hal0``, with a ``hostname`` shim and a venv-python shim
that runs this checkout's ``hal0``.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "installer" / "install.sh"

PROXY_URL = "https://hal0.example.com"
PROXY_ORIGIN = "https://hal0.example.com"
EXTRA = "http://box.lan:8080"


def _extract_network_section() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("# BEGIN hal0-network-derive")
    refresh = text.index('info "refreshed network vars in ${API_ENV} (0600)"', start)
    end = text.index("\nfi\n", refresh) + len("\nfi\n")
    return text[start:end]


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_installer_network_step(
    tmp_path: Path, env_extra: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_exec(
        bin_dir / "hostname",
        'if [[ "${1:-}" == "-I" ]]; then echo "192.0.2.20 "; else echo testbox; fi',
    )
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    # The installer calls "${VENV_DIR}/bin/python"; point it at this checkout.
    _write_exec(venv_bin / "python", f'PYTHONPATH="{REPO / "src"}" exec "{sys.executable}" "$@"')

    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "IFS=$'\\n\\t'\n"
        'info() { echo "INFO: $*"; }\n'
        'warn() { echo "WARN: $*"; }\n'
        f'ETC_DIR="{etc}"\n'
        f'HAL0_TOML="{etc / "hal0.toml"}"\n'
        f'VENV_DIR="{tmp_path / "venv"}"\n'
        'API_BIND_HOST="0.0.0.0"\n'
        "HAL0_PORT=8080\n"
        'HAL0_UI_DIST_VAL="/opt/hal0/ui/dist"\n'
        'API_ENV="${ETC_DIR}/api.env"\n'
        f"{_extract_network_section()}",
        encoding="utf-8",
    )
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HAL0_HOSTNAME": "testbox",
        **env_extra,
    }
    proc = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def _allowed_origins(api_env: Path) -> list[str]:
    for line in api_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("HAL0_ALLOWED_ORIGINS="):
            return line.split("=", 1)[1].split(",")
    raise AssertionError(f"no HAL0_ALLOWED_ORIGINS in {api_env}")


def _network(toml_path: Path) -> dict[str, object]:
    return tomllib.loads(toml_path.read_text(encoding="utf-8")).get("network", {})


@pytest.fixture
def box(tmp_path: Path) -> Path:
    """A box the installer has already seeded hal0.toml on (no [network])."""
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True)
    (etc / "hal0.toml").write_text("[meta]\nschema_version = 1\n", encoding="utf-8")
    (etc / "hal0.toml").chmod(0o644)
    return tmp_path


def _runtime_origins(
    box: Path, env_list: list[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[str, ...]:
    """What hal0-api allows: the block's env list plus hal0.toml [network]."""
    from hal0.api.agents import _auth

    monkeypatch.setenv("HAL0_HOME", str(box))
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", ",".join(env_list))
    return _auth.allowed_origins()


def test_rerun_without_env_keeps_public_url_origin(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install with HAL0_PUBLIC_URL, re-run without it: the origin survives."""
    etc = box / "etc" / "hal0"
    first = _run_installer_network_step(box, {"HAL0_PUBLIC_URL": PROXY_URL + "/"})
    assert "persisted HAL0_PUBLIC_URL" in first.stdout
    assert _network(etc / "hal0.toml") == {
        "public_url": PROXY_URL,
        "extra_origins": [],
        "comfyui_listen": "lan",
    }
    assert (etc / "hal0.toml").stat().st_mode & 0o777 == 0o644  # mode kept across the write
    # Persisted, so it is not copied into the block: hal0-api adds it at runtime.
    assert _allowed_origins(etc / "api.env").count(PROXY_ORIGIN) == 0

    # Operator adds a hand-written line outside the block and an extra origin.
    with (etc / "api.env").open("a", encoding="utf-8") as fh:
        fh.write("HF_TOKEN=keep-me\n")
    toml_text = (etc / "hal0.toml").read_text(encoding="utf-8")
    (etc / "hal0.toml").write_text(
        toml_text.replace("extra_origins = []", f'extra_origins = ["{EXTRA}"]'), encoding="utf-8"
    )

    _run_installer_network_step(box, {})  # the re-run: no HAL0_PUBLIC_URL
    env_list = _allowed_origins(etc / "api.env")
    assert env_list.count("http://192.0.2.20:8080") == 1
    assert "HF_TOKEN=keep-me" in (etc / "api.env").read_text(encoding="utf-8")
    assert _network(etc / "hal0.toml") == {
        "public_url": PROXY_URL,
        "extra_origins": [EXTRA],
        "comfyui_listen": "lan",
    }
    origins = _runtime_origins(box, env_list, monkeypatch)
    assert origins.count(PROXY_ORIGIN) == 1
    assert origins.count(EXTRA) == 1


def test_clearing_public_url_revokes_it_without_an_installer_run(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hal0 network public-url clear`` must really revoke the origin: no copy
    of it may outlive the clear in the managed block (#2382 review)."""
    etc = box / "etc" / "hal0"
    _run_installer_network_step(box, {"HAL0_PUBLIC_URL": PROXY_URL})
    _run_installer_network_step(box, {})
    env_list = _allowed_origins(etc / "api.env")
    assert _runtime_origins(box, env_list, monkeypatch).count(PROXY_ORIGIN) == 1

    # The clear: [network].public_url removed from hal0.toml, no installer run.
    (etc / "hal0.toml").write_text("[meta]\nschema_version = 1\n", encoding="utf-8")
    assert _runtime_origins(box, env_list, monkeypatch).count(PROXY_ORIGIN) == 0


def test_bare_host_install_time_public_url_is_persisted(box: Path) -> None:
    """The documented bare-host form (``chat.example.com`` means https) is
    persisted like a full URL, so it survives the next re-run too."""
    etc = box / "etc" / "hal0"
    proc = _run_installer_network_step(box, {"HAL0_PUBLIC_URL": "Chat.Example.com"})
    assert "persisted HAL0_PUBLIC_URL" in proc.stdout
    assert _network(etc / "hal0.toml")["public_url"] == "https://chat.example.com"


def test_rerun_keeps_extra_origin_allowed_at_runtime(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a re-run rewrites the managed block, hal0-api still allows the
    [network] origins: they come from hal0.toml, not from the block."""
    etc = box / "etc" / "hal0"
    (etc / "hal0.toml").write_text(
        f'[meta]\nschema_version = 1\n\n[network]\nextra_origins = ["{EXTRA}"]\n',
        encoding="utf-8",
    )
    _run_installer_network_step(box, {})
    _run_installer_network_step(box, {})
    env_list = _allowed_origins(etc / "api.env")
    assert env_list.count(EXTRA) == 0  # never written into the managed block

    origins = _runtime_origins(box, env_list, monkeypatch)
    assert origins.count(EXTRA) == 1
    assert origins[: len(env_list)] == tuple(env_list)


def test_install_time_public_url_never_overwrites_a_set_value(box: Path) -> None:
    etc = box / "etc" / "hal0"
    (etc / "hal0.toml").write_text(
        '[meta]\nschema_version = 1\n\n[network]\npublic_url = "https://kept.example.com"\n',
        encoding="utf-8",
    )
    proc = _run_installer_network_step(box, {"HAL0_PUBLIC_URL": PROXY_URL})
    assert "left alone" in proc.stdout
    assert _network(etc / "hal0.toml")["public_url"] == "https://kept.example.com"
    origins = _allowed_origins(etc / "api.env")
    # The explicit install-time value still reaches this run's block.
    assert origins.count(PROXY_ORIGIN) == 1


def test_invalid_install_time_public_url_is_not_persisted(box: Path) -> None:
    etc = box / "etc" / "hal0"
    before = (etc / "hal0.toml").read_text(encoding="utf-8")
    proc = _run_installer_network_step(box, {"HAL0_PUBLIC_URL": "ftp://hal0.example.com"})
    assert "not persisted" in proc.stdout
    assert (etc / "hal0.toml").read_text(encoding="utf-8") == before
    # Nor written into api.env: an invalid value never widens the allowlist.
    assert not any(o.startswith("ftp:") for o in _allowed_origins(etc / "api.env"))


def test_no_env_and_no_network_section_is_unchanged(box: Path) -> None:
    """Upgrade path: a box with no [network] and no HAL0_PUBLIC_URL gets the
    same block as before and its hal0.toml is not rewritten."""
    etc = box / "etc" / "hal0"
    before = (etc / "hal0.toml").read_text(encoding="utf-8")
    proc = _run_installer_network_step(box, {})
    assert "HAL0_PUBLIC_URL" not in proc.stdout
    assert (etc / "hal0.toml").read_text(encoding="utf-8") == before
    origins = _allowed_origins(etc / "api.env")
    assert "http://testbox:8080" in origins
    assert not any(o.startswith("https://") for o in origins)
