"""RATIFIED 2026-07-18 (deliverable 4) — convergent podman AppArmor preflight.

Detect the unconfined-LXC apparmor profile-load failure from the podman SMOKE
FAILURE (halo150 R4), write the containers.conf fix once, retry — all against
recorded fakes, no real podman or /etc writes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hal0.agents import containers_apparmor as ca


def _proc(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


_APPARMOR_STDERR = (
    "Error: default OCI runtime spec: install profile containers-default apparmor: exit status 243"
)


def _recorder(sequence: list[subprocess.CompletedProcess]):
    calls: list[list[str]] = []
    it = iter(sequence)

    def _run(argv, **_kw):
        calls.append(list(argv))
        return next(it)

    return _run, calls


def test_smoke_passes_leaves_config_untouched(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, calls = _recorder([_proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "ok"
    assert not result.wrote_config
    assert not conf.exists()
    assert len(calls) == 1  # only the initial smoke


def test_apparmor_failure_writes_config_and_retries(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "fixed"
    assert result.wrote_config
    assert conf.exists()
    assert 'apparmor_profile = "unconfined"' in conf.read_text()
    assert "[containers]" in conf.read_text()
    assert len(calls) == 2  # smoke, then retry after the write


def test_unrelated_failure_is_not_touched(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, _calls = _recorder([_proc(125, "Error: unable to pull image: not found")])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "unrelated"
    assert not result.wrote_config
    assert not conf.exists()


def test_idempotent_when_already_unconfined(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers]\napparmor_profile = "unconfined"\n')
    run, _calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    # Config already carried the fix → no rewrite, but retry still attempted.
    assert result.outcome == "already"
    assert not result.wrote_config


def test_second_run_after_fix_is_ok(tmp_path: Path) -> None:
    # Convergence: after the box is fixed, a rerun re-smokes, passes, no-ops.
    conf = tmp_path / "containers.conf"
    run1, _ = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    ca.ensure_podman_apparmor_usable(run=run1, conf_path=conf)
    run2, calls2 = _recorder([_proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run2, conf_path=conf)
    assert result.outcome == "ok"
    assert len(calls2) == 1


def test_write_preserves_existing_containers_section(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers]\nlog_driver = "journald"\n[network]\ndns = ["1.1.1.1"]\n')
    ca._write_apparmor_unconfined(conf)
    text = conf.read_text()
    assert 'log_driver = "journald"' in text
    assert 'apparmor_profile = "unconfined"' in text
    assert "[network]" in text
    assert ca._apparmor_already_unconfined(conf)


def test_no_podman_reports_cleanly(tmp_path: Path) -> None:
    def _run(argv, **_kw):
        raise FileNotFoundError("podman")

    result = ca.ensure_podman_apparmor_usable(run=_run, conf_path=tmp_path / "c.conf")
    assert result.outcome == "no_podman"


# ── Codex #1728 P1 follow-ups ────────────────────────────────────────────────


def test_apparmor_parser_access_denied_shape_is_also_recognized(tmp_path: Path) -> None:
    # The raw `apparmor_parser` failure text (no "install profile
    # containers-default" wrapper) is the OTHER real-world signature shape —
    # installer/lib/preflight.sh's `_is_apparmor_profile_load_failure`
    # recognizes it too; this module's own detection must stay in lockstep or
    # the shell gate remediates while this helper reports "unrelated" and
    # never writes the fix.
    conf = tmp_path / "containers.conf"
    stderr = "apparmor_parser: Warning ... apparmor_parser: profile load failed: Access denied"
    run, calls = _recorder([_proc(243, stderr), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "fixed"
    assert result.wrote_config
    assert len(calls) == 2


def test_default_smoke_argv_honours_smoke_image_env(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_CONTAINER_SMOKE_IMAGE", raising=False)
    assert ca._default_smoke_argv() == ("podman", "run", "--rm", "quay.io/podman/hello")
    monkeypatch.setenv("HAL0_CONTAINER_SMOKE_IMAGE", "registry.local/mirror/hello")
    assert ca._default_smoke_argv() == ("podman", "run", "--rm", "registry.local/mirror/hello")


def test_write_preserves_commented_section_header(tmp_path: Path) -> None:
    # A header with a trailing inline comment (`[containers] # ...`) used to
    # fail the naive `stripped.endswith("]")` check, so the writer never
    # recognized the existing [containers] table and appended a SECOND one —
    # invalid TOML ("Cannot declare ('containers',) twice").
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers] # operator settings\nlog_driver = "journald"\n')
    ca._write_apparmor_unconfined(conf)
    text = conf.read_text()
    assert text.count("[containers]") == 1  # no duplicate table header appended
    assert "# operator settings" in text
    assert 'log_driver = "journald"' in text
    assert 'apparmor_profile = "unconfined"' in text
    # Must still be valid, single-table TOML.
    import tomllib

    parsed = tomllib.loads(text)
    assert parsed["containers"]["apparmor_profile"] == "unconfined"
    assert parsed["containers"]["log_driver"] == "journald"


# ── #3234: unprivileged-LXC guest `flags=(unconfined)` profiles ──────────────
#
# On an UNPRIVILEGED Ubuntu 24.04 LXC the guest's own apparmor profiles for
# podman/crun/buildah/runc/rootlesskit/slirp4netns are `flags=(unconfined)`
# userns-gated stubs that deny every podman socket(). The fix symlinks them
# under <apparmor.d>/disable/ and unloads them with `apparmor_parser -R`.

_SOCKET_DENIED_STDERR = "Error: netavark: failed to create socket: socket: permission denied"
_PROFILE_NAMES = ("podman", "crun", "buildah", "runc", "rootlesskit", "slirp4netns")
_UNCONFINED_PROFILE = "abi <abi/4.0>,\ninclude <tunables/global>\nprofile {n} /usr/bin/{n} flags=(unconfined) {{\n  userns,\n}}\n"
_ENFORCE_PROFILE = (
    "abi <abi/4.0>,\ninclude <tunables/global>\nprofile {n} /usr/bin/{n} {{\n  userns,\n}}\n"
)


def _seed_profiles(apparmor_d: Path, names=_PROFILE_NAMES, *, enforce=()) -> None:
    apparmor_d.mkdir(parents=True, exist_ok=True)
    for n in names:
        tpl = _ENFORCE_PROFILE if n in enforce else _UNCONFINED_PROFILE
        (apparmor_d / n).write_text(tpl.format(n=n))


def _is_parser_remove(argv) -> bool:
    return list(argv)[:2] == ["apparmor_parser", "-R"]


def test_socket_denied_disables_all_six_unconfined_profiles_and_retries(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    seq = [_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * len(_PROFILE_NAMES) + [_proc(0)]
    run, calls = _recorder(seq)

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf, apparmor_d=aa)

    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is True
    assert not result.wrote_config
    assert not conf.exists()  # #1563 path must not fire for this signature
    for n in _PROFILE_NAMES:
        link = aa / "disable" / n
        assert link.is_symlink()
        assert Path(os.readlink(link)) == aa / n
        assert n in (result.detail or "")
    parser_calls = [c for c in calls if _is_parser_remove(c)]
    assert parser_calls == [["apparmor_parser", "-R", str(aa / n)] for n in _PROFILE_NAMES]
    # smoke, six parser calls, then exactly one retry smoke (last call)
    assert len(calls) == 1 + len(_PROFILE_NAMES) + 1
    assert calls[0][:2] == ["podman", "run"]
    assert calls[-1][:2] == ["podman", "run"]


def test_socket_denied_retry_still_failing_reports_not_fixed(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _calls = _recorder(
        [_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(126, _SOCKET_DENIED_STDERR)]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is False
    assert (aa / "disable" / "podman").is_symlink()


def test_socket_denied_signature_is_case_insensitive(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _calls = _recorder([_proc(126, "Socket: Permission Denied"), _proc(0), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is True


def test_profile_without_unconfined_flag_is_not_disabled(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, enforce=("crun",))
    others = [n for n in _PROFILE_NAMES if n != "crun"]
    run, calls = _recorder(
        [_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * len(others) + [_proc(0)]
    )

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "guest_profiles_disabled"
    assert not (aa / "disable" / "crun").exists()
    assert not (aa / "disable" / "crun").is_symlink()
    for n in others:
        assert (aa / "disable" / n).is_symlink()
    assert [c for c in calls if _is_parser_remove(c)] == [
        ["apparmor_parser", "-R", str(aa / n)] for n in others
    ]
    assert "crun" not in (result.detail or "")


def test_socket_denied_without_profile_files_does_nothing(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "unrelated"
    assert "no unconfined guest" in (result.detail or "").lower()
    assert not (aa / "disable").exists()
    assert len(calls) == 1  # no parser call, no retry


def test_socket_denied_with_missing_apparmor_d_does_nothing(tmp_path: Path) -> None:
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])
    result = ca.ensure_podman_apparmor_usable(
        run=run, conf_path=tmp_path / "c.conf", apparmor_d=tmp_path / "nope"
    )
    assert result.outcome == "unrelated"
    assert not (tmp_path / "nope").exists()
    assert len(calls) == 1


def test_1563_signature_keeps_precedence_over_socket_denied(tmp_path: Path) -> None:
    # Regression guard: the #1563 branch still writes containers.conf and never
    # touches the guest profiles — even when the apparmor dir has candidates
    # and the output ALSO mentions a socket denial.
    conf = tmp_path / "containers.conf"
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    both = _APPARMOR_STDERR + "\nsocket: permission denied"
    run, calls = _recorder([_proc(243, both), _proc(0)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf, apparmor_d=aa)

    assert result.outcome == "fixed"
    assert result.wrote_config
    assert 'apparmor_profile = "unconfined"' in conf.read_text()
    assert not (aa / "disable").exists()
    assert not any(_is_parser_remove(c) for c in calls)


def test_1563_plain_path_unchanged_with_apparmor_d_given(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    run, _calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf, apparmor_d=aa)
    assert result.outcome == "fixed"
    assert not (aa / "disable").exists()


def test_unrelated_failure_leaves_guest_profiles_alone(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    run, calls = _recorder([_proc(125, "Error: write /var/lib/containers: Disk quota exceeded")])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "unrelated"
    assert not (aa / "disable").exists()
    assert len(calls) == 1


def test_disable_is_idempotent_and_still_retries(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    n = len(_PROFILE_NAMES)

    run1, _ = _recorder(
        [_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * n + [_proc(126, _SOCKET_DENIED_STDERR)]
    )
    first = ca.ensure_podman_apparmor_usable(run=run1, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert first.fixed is False
    snapshot = sorted(p.name for p in (aa / "disable").iterdir())
    targets = {p.name: os.readlink(p) for p in (aa / "disable").iterdir()}

    run2, calls2 = _recorder([_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * n + [_proc(0)])
    second = ca.ensure_podman_apparmor_usable(
        run=run2, conf_path=tmp_path / "c.conf", apparmor_d=aa
    )

    assert second.outcome == "guest_profiles_disabled"
    assert second.fixed is True
    assert sorted(p.name for p in (aa / "disable").iterdir()) == snapshot  # no new files
    assert {p.name: os.readlink(p) for p in (aa / "disable").iterdir()} == targets
    assert calls2[0][:2] == ["podman", "run"]
    assert calls2[-1][:2] == ["podman", "run"]  # still re-smoked


def test_disable_replaces_stale_link_and_regular_file(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    (aa / "disable").mkdir()
    (aa / "disable" / "podman").symlink_to(tmp_path / "elsewhere")  # stale link
    (aa / "disable" / "crun").write_text("junk")  # regular file in the way
    run, _ = _recorder([_proc(0), _proc(0)])

    handled = ca.disable_guest_unconfined_profiles(aa, run)

    assert handled == ["podman", "crun"]
    assert Path(os.readlink(aa / "disable" / "podman")) == aa / "podman"
    assert Path(os.readlink(aa / "disable" / "crun")) == aa / "crun"


def test_parser_nonzero_exit_is_not_fatal(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    run, calls = _recorder([_proc(1, "apparmor_parser: not loaded"), _proc(0)])

    handled = ca.disable_guest_unconfined_profiles(aa, run)

    assert handled == ["podman", "crun"]  # failed unload still counts as handled
    assert (aa / "disable" / "podman").is_symlink()
    assert (aa / "disable" / "crun").is_symlink()
    assert len(calls) == 2


def test_parser_missing_is_not_fatal(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))

    def _run(argv, **_kw):
        raise FileNotFoundError("apparmor_parser")

    handled = ca.disable_guest_unconfined_profiles(aa, _run)
    assert handled == ["podman"]
    assert (aa / "disable" / "podman").is_symlink()


def test_guest_unconfined_profiles_present_filters_and_preserves_order(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("slirp4netns", "podman", "runc"), enforce=("runc",))
    (aa / "unrelated").write_text("profile unrelated flags=(unconfined) {}\n")  # not in the list
    assert ca.guest_unconfined_profiles_present(aa) == ["podman", "slirp4netns"]
    assert ca.guest_unconfined_profiles_present(tmp_path / "missing") == []


def test_unconfined_flag_must_be_exact_token(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text("profile podman /usr/bin/podman flags=(complain) {}\n")
    assert ca.guest_unconfined_profiles_present(aa) == []


def test_is_unconfined_profile_network_denial() -> None:
    f = ca._is_unconfined_profile_network_denial
    assert f(_proc(126, _SOCKET_DENIED_STDERR))
    assert f(_proc(126, stdout="x SOCKET: PERMISSION DENIED y"))
    assert not f(_proc(126, "Error: Disk quota exceeded"))
    # #1563 shape wins even if it also says socket: permission denied
    assert not f(_proc(243, _APPARMOR_STDERR + " socket: permission denied"))
    assert not f(_proc(0, _SOCKET_DENIED_STDERR))


def test_apparmor_d_root_honours_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HAL0_APPARMOR_D", raising=False)
    assert ca.apparmor_d_root() == Path("/etc/apparmor.d")
    monkeypatch.setenv("HAL0_APPARMOR_D", str(tmp_path / "aa"))
    assert ca.apparmor_d_root() == tmp_path / "aa"


def test_env_seam_is_used_when_apparmor_d_not_passed(monkeypatch, tmp_path: Path) -> None:
    aa = tmp_path / "aa"
    _seed_profiles(aa, ("podman",))
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))
    run, _ = _recorder([_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf")
    assert result.outcome == "guest_profiles_disabled"
    assert (aa / "disable" / "podman").is_symlink()


def test_main_prints_summary_for_guest_profiles_disabled(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    def _fake(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled",
            detail="podman, crun",
            fixed=True,
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake)
    monkeypatch.setenv("HAL0_APPARMOR_CONF", str(tmp_path / "c.conf"))
    assert ca._main() == 0
    out = capsys.readouterr().out
    assert (
        "apparmor remediation applied: disabled guest profiles podman, crun — retry passed" in out
    )

    def _fake_bad(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled", detail="podman", fixed=False
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake_bad)
    assert ca._main() == 0  # exit semantics unchanged; the bash gate re-smokes itself
    assert "retry still failing" in capsys.readouterr().out
