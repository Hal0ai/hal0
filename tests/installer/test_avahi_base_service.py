"""H13: the installer must write the base avahi announcement it promises.

``services/mdns.py``'s ``status()`` docstring and the Services page's
Discovery card caption both said "the installer writes
/etc/avahi/services/hal0.service when avahi is present" — nothing in
install.sh ever did, so ``base_advertised`` (``GET /api/services/mdns``)
was permanently ``False`` and the caption always rendered its fallback
"no base file" wording, regardless of the real box state.

``write_avahi_base_service()`` in install.sh closes that gap. Driven here
by extracting the function body and running it against a tmp directory —
the same technique ``test_avahi_hostname.py`` uses for
``sync_avahi_hostname`` (real avahi/systemd aren't available in CI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def _extract_func() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("write_avahi_base_service()")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _drive(tmp_path: Path, *, port: str = "8080") -> Path:
    """Run ``write_avahi_base_service <services_dir> <port>``; return the dir."""
    services_dir = tmp_path / "avahi-services"
    services_dir.mkdir(exist_ok=True)
    script = tmp_path / "drive.sh"
    script.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\n{_extract_func()}\n"
        f'write_avahi_base_service "{services_dir}" "{port}"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return services_dir


def test_writes_base_service_file(tmp_path: Path) -> None:
    services_dir = _drive(tmp_path, port="8080")
    written = (services_dir / "hal0.service").read_text(encoding="utf-8")
    assert "<port>8080</port>" in written
    assert 'name replace-wildcards="yes">hal0 on %h<' in written
    assert "_http._tcp" in written
    # Never touches the addon-file surface (services/mdns.py owns that).
    assert not list(services_dir.glob("hal0-addon-*.service"))


def test_never_leaves_a_stray_tmp_file(tmp_path: Path) -> None:
    services_dir = _drive(tmp_path)
    assert [p.name for p in services_dir.iterdir()] == ["hal0.service"]


def test_custom_port_reflected(tmp_path: Path) -> None:
    services_dir = _drive(tmp_path, port="9090")
    written = (services_dir / "hal0.service").read_text(encoding="utf-8")
    assert "<port>9090</port>" in written


def test_rerun_converges_on_a_new_port(tmp_path: Path) -> None:
    """Re-running the installer with a different HAL0_PORT must update the
    file in place, not leave a stale port from a first install."""
    services_dir = _drive(tmp_path, port="8080")
    _drive(tmp_path, port="9090")
    written = (services_dir / "hal0.service").read_text(encoding="utf-8")
    assert "<port>9090</port>" in written
    assert "8080" not in written


def test_unwritable_dir_degrades_without_raising(tmp_path: Path) -> None:
    """A read-only services dir (permission issue, RO mount) must not abort
    the install — same fail-soft posture as sync_avahi_hostname."""
    services_dir = tmp_path / "ro-avahi-services"
    services_dir.mkdir()
    services_dir.chmod(0o500)
    try:
        script = tmp_path / "drive.sh"
        script.write_text(
            f"#!/usr/bin/env bash\nset -euo pipefail\n{_extract_func()}\n"
            f'write_avahi_base_service "{services_dir}" "8080"\n',
            encoding="utf-8",
        )
        proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert not (services_dir / "hal0.service").exists()
    finally:
        services_dir.chmod(0o700)
