"""avahi must announce the HAL0_HOSTNAME choice, not the machine hostname.

#2060: the custom-hostname plumbing (``HAL0_HOSTNAME`` env / answer-file
``network.hostname``) already reaches api.env, mDNS URL building and the
WS-origin allowlist — but avahi announces the *machine* hostname from
``/etc/hostname``, so ``HAL0_HOSTNAME=jarvis`` produced ``jarvis.local``
URLs that never resolved on the LAN. ``sync_avahi_hostname`` in install.sh
closes that last hop by pinning ``host-name=<name>`` under ``[server]`` in
``avahi-daemon.conf`` (mDNS-scoped — the box itself is never renamed) and
restarting avahi-daemon.

Driven here by extracting the function body and running it against a tmp
conf path (``HAL0_AVAHI_CONF``) with fake ``hostname`` / ``systemctl`` /
``avahi-daemon`` shims on PATH — the same technique
``test_api_restart_on_upgrade.py`` uses (real avahi/systemd aren't
available in CI).
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"

MACHINE = "testbox"


def _extract_func() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("sync_avahi_hostname()")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _drive(tmp_path: Path, hostname_arg: str, *, avahi_bin: bool = True) -> str:
    """Run sync_avahi_hostname "<hostname_arg>"; return systemctl call log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "systemctl.calls"
    _write_exec(bin_dir / "hostname", f'echo "{MACHINE}"')
    _write_exec(bin_dir / "systemctl", f'echo "$@" >> "{calls}"\nexit 0')
    fake_avahi = bin_dir / "avahi-daemon"
    if avahi_bin:
        _write_exec(fake_avahi, "exit 0")
    elif fake_avahi.exists():
        fake_avahi.unlink()

    warn_log = tmp_path / "warn.calls"
    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'info() {{ :; }}\nwarn() {{ echo "$@" >> "{warn_log}"; }}\n'
        f"export HAL0_AVAHI_CONF={tmp_path / 'avahi-daemon.conf'}\n"
        f"{_extract_func()}\n"
        f'sync_avahi_hostname "{hostname_arg}"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def _conf(tmp_path: Path) -> Path:
    return tmp_path / "avahi-daemon.conf"


def test_custom_hostname_pins_avahi_host_name(tmp_path: Path) -> None:
    """The core fix: HAL0_HOSTNAME=jarvis must land as host-name=jarvis."""
    calls = _drive(tmp_path, "jarvis")
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "[server]" in text
    assert "host-name=jarvis\n" in text
    assert "try-restart avahi-daemon.service" in calls


def test_existing_conf_settings_survive(tmp_path: Path) -> None:
    """Edit is surgical: other sections/keys and commented examples stay."""
    _conf(tmp_path).write_text(
        "[server]\nuse-ipv4=yes\n#host-name=example\n\n[wide-area]\nenable-wide-area=yes\n",
        encoding="utf-8",
    )
    _drive(tmp_path, "jarvis")
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "use-ipv4=yes" in text
    assert "#host-name=example" in text
    assert "enable-wide-area=yes" in text
    assert text.count("\nhost-name=jarvis\n") == 1
    # inserted under [server], not appended into [wide-area]
    assert text.index("host-name=jarvis") < text.index("[wide-area]")


def test_rerun_with_new_name_updates(tmp_path: Path) -> None:
    """Upgrade path: a re-run with a different HAL0_HOSTNAME converges."""
    _drive(tmp_path, "jarvis")
    _drive(tmp_path, "hal9000")
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "host-name=hal9000\n" in text
    assert "jarvis" not in text
    assert text.count("host-name=") == 1


def test_rerun_same_name_is_a_noop(tmp_path: Path) -> None:
    """Idempotent: an unchanged conf must not bounce the daemon again."""
    _drive(tmp_path, "jarvis")
    (tmp_path / "systemctl.calls").write_text("", encoding="utf-8")
    calls = _drive(tmp_path, "jarvis")
    assert calls == ""


def test_no_override_withdraws_managed_line(tmp_path: Path) -> None:
    """A re-run without HAL0_HOSTNAME reverts to the machine hostname."""
    _drive(tmp_path, "jarvis")
    calls = _drive(tmp_path, MACHINE)
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "host-name=" not in text.replace("#host-name", "")
    assert "try-restart avahi-daemon.service" in calls


def test_operator_own_host_name_is_left_alone_without_override(tmp_path: Path) -> None:
    """Only the marker-managed line is ever withdrawn — not hand edits."""
    _conf(tmp_path).write_text("[server]\nhost-name=operator-choice\n", encoding="utf-8")
    calls = _drive(tmp_path, MACHINE)
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "host-name=operator-choice" in text
    assert calls == ""


def test_pinning_over_operator_host_name_replaces_it_with_a_warning(
    tmp_path: Path,
) -> None:
    """Two host-name lines would be invalid conf, so the pin must replace an
    operator's own active host-name= — but it has to say so out loud, because
    a later no-override run withdraws only the managed pair (the operator's
    earlier value is not restored)."""
    _conf(tmp_path).write_text("[server]\nhost-name=operator-choice\n", encoding="utf-8")
    _drive(tmp_path, "jarvis")
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "host-name=jarvis\n" in text
    assert "operator-choice" not in text
    warns = (tmp_path / "warn.calls").read_text(encoding="utf-8")
    assert "replacing an existing host-name=" in warns


def test_machine_hostname_or_dotted_name_writes_nothing(tmp_path: Path) -> None:
    """No override / a reverse-proxy FQDN is not avahi's business."""
    _drive(tmp_path, MACHINE)
    assert not _conf(tmp_path).exists()
    _drive(tmp_path, "chat.example.com")
    assert not _conf(tmp_path).exists()


def test_no_avahi_on_the_box_is_a_silent_noop(tmp_path: Path) -> None:
    """Fail-soft like services/mdns.py: no conf, no binary → do nothing."""
    calls = _drive(tmp_path, "jarvis", avahi_bin=False)
    assert not _conf(tmp_path).exists()
    assert calls == ""


def test_dot_local_suffix_is_stripped(tmp_path: Path) -> None:
    """A `jarvis.local` choice announces the bare label, like mdns.py."""
    _drive(tmp_path, "jarvis.local")
    text = _conf(tmp_path).read_text(encoding="utf-8")
    assert "host-name=jarvis\n" in text


def test_install_sh_calls_the_sync_after_network_derivation(tmp_path: Path) -> None:
    """The function must actually be wired into the prod install flow."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    call_at = text.index('sync_avahi_hostname "')
    assert text.index("NETWORK_ENV_LINES=") < call_at
