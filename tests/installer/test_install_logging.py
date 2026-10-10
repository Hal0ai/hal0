"""installer/lib/logging.sh — tee'd install log path selection.

Same technique as test_seam_verification.py: source the file and invoke a
function directly. The full `exec > >(tee ...)` redirect (hal0_install_log_init)
is exercised through a real subshell so the tee side effect is observable
without touching this test process's own stdout.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOGGING_SH = REPO / "installer" / "lib" / "logging.sh"


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


def _unwritable_even_for_root(tmp_path: Path) -> Path:
    """A "directory" path whose mkdir fails even as root: a regular file sits
    where the directory should be. (A made-up absolute path like
    /nonexistent-dir is creatable by root, so a root test run never reached
    the fallback.)"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    return blocker


class TestLogPath:
    def test_root_gets_a_var_log_hal0_path(self) -> None:
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}  # pretend to be root
hal0_install_log_path
"""
        proc = _run(script)
        path = proc.stdout.strip()
        assert path.startswith("/var/log/hal0/install-"), proc.stdout
        assert path.endswith(".log")

    def test_non_root_gets_a_tmp_fallback_path(self) -> None:
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 1000; }}  # pretend to be non-root
hal0_install_log_path
"""
        proc = _run(script)
        path = proc.stdout.strip()
        assert path.startswith("/tmp/hal0-install-"), proc.stdout


class TestLogInit:
    def test_init_creates_the_log_and_captures_subsequent_output(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{fake_log_dir}"; }}
hal0_install_log_init
echo "path=$HAL0_INSTALL_LOG"
echo "hello from the installer"
warn_line() {{ echo "a warning" >&2; }}
warn_line
"""
        proc = _run(script)
        assert "rc=" not in proc.stdout  # no crash marker expected
        log_path_line = next(line for line in proc.stdout.splitlines() if line.startswith("path="))
        log_path = log_path_line.removeprefix("path=")
        assert Path(log_path).is_file()
        captured = Path(log_path).read_text()
        assert "hello from the installer" in captured
        assert "a warning" in captured
        # Still visible on the terminal too — tee, not redirect.
        assert "hello from the installer" in proc.stdout
        assert "a warning" in proc.stderr

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{fake_log_dir}"; }}
hal0_install_log_init
first="$HAL0_INSTALL_LOG"
hal0_install_log_init
second="$HAL0_INSTALL_LOG"
[[ "$first" == "$second" ]] && echo "same"
"""
        proc = _run(script)
        assert "same" in proc.stdout, proc.stdout

    def test_an_unwritable_primary_path_falls_back_to_tmp(self, tmp_path: Path) -> None:
        """The FHS path (root-owned /var/log/hal0) can be unwritable even as
        root — a read-only /var, an unusual mount policy. Fall back to /tmp
        rather than aborting the install over forensics."""
        blocker = _unwritable_even_for_root(tmp_path)
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{blocker}"; }}
hal0_install_log_init
echo "rc=$?"
echo "log=$HAL0_INSTALL_LOG"
echo "still running"
"""
        proc = _run(script)
        assert "still running" in proc.stdout, proc.stdout
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        fallback_path = log_line.removeprefix("log=")
        assert fallback_path.startswith("/tmp/hal0-install-"), proc.stdout
        assert Path(fallback_path).is_file()
        Path(fallback_path).unlink(missing_ok=True)


class TestLogMode:
    """#2361: the log tees installer output unredacted, so it must be
    owner-only (0600) on both the primary and the /tmp fallback path, created
    that way (umask 077) rather than chmod'ed after a world-readable birth."""

    def test_primary_log_is_owner_only(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        script = f"""
umask 022
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{fake_log_dir}"; }}
hal0_install_log_init
echo "log=$HAL0_INSTALL_LOG"
echo "umask=$(umask)"
"""
        proc = _run(script)
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        log_path = Path(log_line.removeprefix("log="))
        assert log_path.is_file(), proc.stdout
        assert (log_path.stat().st_mode & 0o777) == 0o600
        # The tighter umask is scoped to the log's creation; the rest of the
        # install keeps the caller's umask (install.sh relies on 022).
        assert "umask=0022" in proc.stdout, proc.stdout

    def test_tmp_fallback_log_is_owner_only(self, tmp_path: Path) -> None:
        blocker = _unwritable_even_for_root(tmp_path)
        script = f"""
umask 022
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{blocker}"; }}
hal0_install_log_init
echo "log=$HAL0_INSTALL_LOG"
"""
        proc = _run(script)
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        fallback_path = Path(log_line.removeprefix("log="))
        try:
            assert str(fallback_path).startswith("/tmp/hal0-install-"), proc.stdout
            assert (fallback_path.stat().st_mode & 0o777) == 0o600
        finally:
            fallback_path.unlink(missing_ok=True)

    def test_a_preexisting_log_is_never_reused(self, tmp_path: Path) -> None:
        """#2407: the log is always a brand-new file created 0600, never an
        existing one reopened (and so never one needing a later chmod)."""
        fake_log_dir = tmp_path / "var-log-hal0"
        fake_log_dir.mkdir()
        existing = fake_log_dir / "install-test.log"
        existing.write_text("earlier run\n")
        existing.chmod(0o644)
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s\\n' "{existing}"; }}
hal0_install_log_init
echo "log=$HAL0_INSTALL_LOG"
echo "new run"
"""
        proc = _run(script)
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        log_path = Path(log_line.removeprefix("log="))
        assert log_path != existing
        assert log_path.parent == fake_log_dir
        assert (log_path.stat().st_mode & 0o777) == 0o600
        assert "new run" in log_path.read_text()
        assert existing.read_text() == "earlier run\n"


class TestTmpFallbackIsUnpredictable:
    """#2407: the /tmp log used to be ``/tmp/hal0-install-<ts>.log`` opened
    with a plain ``>`` redirect, which follows a symlink a local user planted
    at that guessable name. It is now created by ``hal0_excl_open`` (O_EXCL, random
    name), so a planted symlink is never opened and its target is untouched.

    ``date`` is shadowed so the "old-style" name is known in advance and
    unique to this test run."""

    @staticmethod
    def _plant(tmp_path: Path) -> tuple[str, Path, Path]:
        stamp = f"pytest{uuid.uuid4().hex[:12]}"
        victim = tmp_path / "victim"
        victim.write_text("precious\n")
        victim.chmod(0o644)
        planted = Path(f"/tmp/hal0-install-{stamp}.log")
        planted.unlink(missing_ok=True)
        planted.symlink_to(victim)
        return stamp, victim, planted

    @staticmethod
    def _init(stamp: str, uid: int, primary: Path | None) -> subprocess.CompletedProcess[str]:
        override = (
            f"hal0_install_log_path() {{ printf '%s/install-test.log\\n' \"{primary}\"; }}"
            if primary is not None
            else ""
        )
        script = f"""
IFS=$'\\n\\t'
umask 022
source "{LOGGING_SH}"
id() {{ echo {uid}; }}
date() {{ echo "{stamp}"; }}
{override}
hal0_install_log_init
echo "rc=$?"
echo "log=$HAL0_INSTALL_LOG"
echo "line written after init"
"""
        return _run(script)

    @staticmethod
    def _log(proc: subprocess.CompletedProcess[str]) -> Path:
        line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        return Path(line.removeprefix("log="))

    def _check(self, proc: subprocess.CompletedProcess[str], victim: Path, planted: Path) -> None:
        log = self._log(proc)
        try:
            assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
            # The symlink's target was neither truncated nor appended to.
            assert victim.read_text() == "precious\n"
            assert (victim.stat().st_mode & 0o777) == 0o644
            # The log is a fresh regular file at a random name, not the plant.
            assert log != planted
            assert re.fullmatch(r"/tmp/hal0-install-[0-9a-f]{12}\.log", str(log)), log
            assert log.is_file() and not log.is_symlink()
            assert (log.stat().st_mode & 0o777) == 0o600
            assert "line written after init" in log.read_text()
        finally:
            if str(log).startswith("/tmp/hal0-install-") and log != planted:
                log.unlink(missing_ok=True)

    def test_root_fallback_does_not_follow_a_planted_symlink(self, tmp_path: Path) -> None:
        stamp, victim, planted = self._plant(tmp_path)
        try:
            proc = self._init(stamp, 0, _unwritable_even_for_root(tmp_path))
            self._check(proc, victim, planted)
        finally:
            planted.unlink(missing_ok=True)

    def test_non_root_log_does_not_follow_a_planted_symlink(self, tmp_path: Path) -> None:
        stamp, victim, planted = self._plant(tmp_path)
        try:
            proc = self._init(stamp, 1000, None)
            self._check(proc, victim, planted)
        finally:
            planted.unlink(missing_ok=True)

    def test_a_writable_primary_is_still_preferred(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        proc = self._init("unused", 0, fake_log_dir)
        log = self._log(proc)
        assert log.parent == fake_log_dir, proc.stdout
        assert re.fullmatch(r"install-test-[0-9a-f]{12}\.log", log.name), log


class TestRootLogIsUnpredictable:
    """#2407 review: /var/log/hal0 is hal0:hal0 0755 (src/hal0/install/
    perms.py), so on a root re-run the hal0 uid can plant a symlink at the
    timestamped ``install-<ts>.log`` name, and protected_symlinks does not
    apply (the dir is neither sticky nor world-writable). The root log gets a
    random suffix and is created with O_EXCL, so the plant is never opened.
    """

    def test_root_log_does_not_follow_a_symlink_planted_in_the_log_dir(
        self, tmp_path: Path
    ) -> None:
        stamp = "20990101-000000"
        log_dir = tmp_path / "var-log-hal0"
        log_dir.mkdir()
        victim = tmp_path / "victim"
        victim.write_text("precious\n")
        victim.chmod(0o644)
        planted = log_dir / f"install-{stamp}.log"
        planted.symlink_to(victim)
        # The pre-fix root name shape, install-<ts>.log, in a test dir
        # standing in for /var/log/hal0.
        script = f"""
IFS=$'\\n\\t'
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{
    printf '{log_dir}/install-%s.log\\n' "$(date -u +%Y%m%d-%H%M%S)"
}}
date() {{ echo "{stamp}"; }}
hal0_install_log_init
echo "log=$HAL0_INSTALL_LOG"
echo "line written after init"
"""
        proc = _run(script)
        assert victim.read_text() == "precious\n", proc.stdout + proc.stderr
        assert (victim.stat().st_mode & 0o777) == 0o644
        assert planted.is_symlink()
        line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        log = Path(line.removeprefix("log="))
        assert log.parent == log_dir
        assert re.fullmatch(rf"install-{stamp}-[0-9a-f]{{12}}\.log", log.name), log
        assert log.is_file() and not log.is_symlink()
        assert (log.stat().st_mode & 0o777) == 0o600
        assert "line written after init" in log.read_text()

    def test_real_root_template_carries_a_random_part(self) -> None:
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
date() {{ echo 20990101-000000; }}
hal0_install_log_path
"""
        proc = _run(script)
        assert proc.stdout.strip() == "/var/log/hal0/install-20990101-000000-XXXXXX.log"


class TestExclOpenRejectsNonRegularTargets:
    """bash noclobber refuses an existing regular file or a symlink to one,
    but still opens an existing symlink to a FIFO or a character device.
    hal0_excl_open must notice (fd not a regular file / not the name's own
    inode), close it, and move on to another random name.

    ``od`` is shadowed so the first random name is known: the plant sits
    there, and later calls yield a fresh name."""

    @staticmethod
    def _script(tmp_path: Path, plant_target: str, pre: str = "", post: str = "") -> str:
        log_dir = tmp_path / "logdir"
        log_dir.mkdir()
        counter = tmp_path / "od-calls"
        return f"""
IFS=$'\\n\\t'
source "{LOGGING_SH}"
od() {{
    local n=0
    [[ -f "{counter}" ]] && n="$(cat "{counter}")"
    echo $((n + 1)) >"{counter}"
    if [[ $n -eq 0 ]]; then echo " aa bb cc dd ee ff"; else echo " 01 23 45 67 89 0$n"; fi
}}
ln -s "{plant_target}" "{log_dir}/install-test-aabbccddeeff.log"
{pre}
hal0_excl_open "{log_dir}/install-test-" .log
echo "rc=$?"
echo "path=$HAL0_EXCL_PATH"
[[ -f "/dev/fd/$HAL0_EXCL_FD" ]] && echo "fd-regular"
{post}
"""

    def _check(self, proc: subprocess.CompletedProcess[str], tmp_path: Path) -> None:
        out = proc.stdout
        assert "rc=0" in out, out + proc.stderr
        path_line = next(line for line in out.splitlines() if line.startswith("path="))
        path = Path(path_line.removeprefix("path="))
        assert path.name != "install-test-aabbccddeeff.log", out
        assert path.parent == tmp_path / "logdir"
        assert path.is_file() and not path.is_symlink()
        assert (path.stat().st_mode & 0o777) == 0o600
        assert "fd-regular" in out

    def test_a_symlink_to_a_fifo_is_not_used(self, tmp_path: Path) -> None:
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        drained = tmp_path / "drained"
        # A reader on the FIFO so the open does not block; whatever reaches
        # it would be data the installer leaked into someone else's pipe.
        pre = f'cat "{fifo}" >"{drained}" & reader=$!'
        post = 'kill "$reader" 2>/dev/null; wait "$reader" 2>/dev/null || true'
        proc = subprocess.run(
            ["bash", "-c", self._script(tmp_path, str(fifo), pre, post)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO),
            timeout=30,
        )
        self._check(proc, tmp_path)

    def test_a_symlink_to_a_char_device_is_not_used(self, tmp_path: Path) -> None:
        proc = _run(self._script(tmp_path, "/dev/null"))
        self._check(proc, tmp_path)
