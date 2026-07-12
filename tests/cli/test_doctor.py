"""Tests for the ``hal0 doctor`` CLI subcommand.

The default ``hal0 doctor`` call shells out to ``installer/lib/preflight.sh``,
so we exercise it with the script set to a known shape via the
``HAL0_PREFLIGHT_SH`` env override. ``capfd`` captures the subprocess's
file-descriptor-level output (CliRunner's StringIO can't see past
subprocess.run).

``hal0 doctor toolbox-pull`` is exercised via an ``httpx.MockTransport``
that stubs the ghcr.io token-exchange + manifest-HEAD flow.

Skipped on non-Linux platforms — the real preflight script depends on
``systemctl`` / ``df`` / ``ss`` which only mean something on Linux.
"""

from __future__ import annotations

import json as jsonlib
import os
import socket
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import typer

import hal0
from hal0.cli.doctor_commands import _locate_preflight, doctor, toolbox_pull

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="preflight.sh is Linux-only")


def _make_stub(tmp_path: Path, body: str) -> Path:
    """Write `body` as an executable bash stub and return its path."""
    stub = tmp_path / "preflight.sh"
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


class _FakeCtx:
    """Minimal stand-in for `typer.Context` — only `invoked_subcommand` matters."""

    invoked_subcommand: str | None = None


def _fake_ctx() -> _FakeCtx:
    """Build a context that represents "no sub-command invoked"."""
    return _FakeCtx()


def _exit_code(exc: pytest.ExceptionInfo[typer.Exit] | typer.Exit) -> int:
    """Pull the exit code off a typer.Exit (raised by the doctor command)."""
    err = exc.value if isinstance(exc, pytest.ExceptionInfo) else exc
    code = err.exit_code
    return int(code) if code is not None else 0


def test_doctor_success_propagates_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A passing preflight script exits 0 and stdout is non-empty."""
    stub = _make_stub(tmp_path, "printf 'all good\\n'\nexit 0\n")
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(stub))

    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_fake_ctx(), verify=False, plain=False, ports=None)

    assert _exit_code(exc) == 0
    captured = capfd.readouterr()
    assert "all good" in captured.out


def test_doctor_failure_propagates_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A failing preflight script (rc=1) surfaces as a non-zero hal0 doctor exit."""
    stub = _make_stub(tmp_path, "printf 'disk: only 7 GB free\\n'\nexit 1\n")
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(stub))

    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_fake_ctx(), verify=False, plain=False, ports=None)

    assert _exit_code(exc) == 1
    captured = capfd.readouterr()
    assert "disk" in captured.out


def test_doctor_missing_script_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the script can't be found we exit 2 with a helpful message."""
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", "/definitely/not/a/real/path.sh")

    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_fake_ctx(), verify=False, plain=False, ports=None)

    assert _exit_code(exc) == 2


def test_doctor_forwards_plain_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """``--plain`` sets HAL0_PLAIN=1 in the child shell."""
    stub = _make_stub(
        tmp_path,
        'printf "HAL0_PLAIN=%s\\n" "${HAL0_PLAIN:-unset}"\nexit 0\n',
    )
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(stub))

    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_fake_ctx(), verify=False, plain=True, ports=None)

    assert _exit_code(exc) == 0
    captured = capfd.readouterr()
    assert "HAL0_PLAIN=1" in captured.out


def test_doctor_forwards_ports_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """``--ports "1 2 3"`` propagates as HAL0_DOCTOR_PORTS."""
    stub = _make_stub(
        tmp_path,
        'printf "PORTS=%s\\n" "${HAL0_DOCTOR_PORTS:-unset}"\nexit 0\n',
    )
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(stub))

    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_fake_ctx(), verify=False, plain=False, ports="9090 9091")

    assert _exit_code(exc) == 0
    captured = capfd.readouterr()
    assert "PORTS=9090 9091" in captured.out


def test_locate_preflight_finds_repo_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In an editable install the locator finds installer/lib/preflight.sh."""
    monkeypatch.delenv("HAL0_PREFLIGHT_SH", raising=False)
    found = _locate_preflight()
    assert found is not None, "expected to locate installer/lib/preflight.sh"
    assert found.name == "preflight.sh"
    assert os.access(found, os.R_OK)


def test_locate_preflight_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAL0_PREFLIGHT_SH wins over the package-relative lookup."""
    custom = tmp_path / "custom.sh"
    custom.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(custom))

    found = _locate_preflight()
    assert found == custom


def test_locate_preflight_missing_override_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bogus HAL0_PREFLIGHT_SH path resolves to None, not a falsy default."""
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", "/no/such/file.sh")
    assert _locate_preflight() is None


def _patch_non_editable_hal0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``hal0.__file__`` at a site-packages-shaped path with no repo
    neighbours, simulating install.sh's default non-editable wheel install
    (``pip install <wheel>`` into a shared venv, not ``pip install -e``).
    """
    fake_pkg = tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "hal0" / "__init__.py"
    fake_pkg.parent.mkdir(parents=True)
    fake_pkg.write_text("")
    monkeypatch.setattr(hal0, "__file__", str(fake_pkg))


def test_locate_preflight_fhs_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL0_FHS_ROOT resolves ``<root>/current/installer/lib/preflight.sh``.

    Reproduces the reported bug: a non-editable FHS install (real wheel in
    the shared venv) where the editable ``parents[2]`` probe can't find a
    repo checkout, so the locator must fall back to the FHS release tree.
    """
    monkeypatch.delenv("HAL0_PREFLIGHT_SH", raising=False)
    monkeypatch.delenv("HAL0_PREFIX", raising=False)
    _patch_non_editable_hal0(tmp_path, monkeypatch)

    fhs_root = tmp_path / "fhs"
    script = fhs_root / "current" / "installer" / "lib" / "preflight.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("HAL0_FHS_ROOT", str(fhs_root))

    assert _locate_preflight() == script


def test_locate_preflight_prefix_env_no_current_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAL0_PREFIX resolves ``<prefix>/installer/lib/preflight.sh`` directly.

    Dev-prefix installs (``installer/install.sh --dev``) have no ``current``
    symlink, unlike the prod FHS layout.
    """
    monkeypatch.delenv("HAL0_PREFLIGHT_SH", raising=False)
    monkeypatch.delenv("HAL0_FHS_ROOT", raising=False)
    _patch_non_editable_hal0(tmp_path, monkeypatch)

    prefix = tmp_path / "prefix"
    script = prefix / "installer" / "lib" / "preflight.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("HAL0_PREFIX", str(prefix))

    assert _locate_preflight() == script


def test_locate_preflight_default_fhs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no override env vars set, falls back to the compiled-in default
    (``/usr/lib/hal0/current`` via :func:`hal0.config.paths.usr_lib`, which
    is itself ``HAL0_HOME``-aware — exercised here via ``HAL0_HOME`` so the
    test doesn't need real root-owned ``/usr/lib/hal0``).
    """
    monkeypatch.delenv("HAL0_PREFLIGHT_SH", raising=False)
    monkeypatch.delenv("HAL0_FHS_ROOT", raising=False)
    monkeypatch.delenv("HAL0_PREFIX", raising=False)
    _patch_non_editable_hal0(tmp_path, monkeypatch)

    home = tmp_path / "home"
    script = home / "usr-lib" / "hal0" / "current" / "installer" / "lib" / "preflight.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("HAL0_HOME", str(home))

    assert _locate_preflight() == script


def test_locate_preflight_env_override_wins_over_fhs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAL0_PREFLIGHT_SH still wins even when an FHS candidate also resolves."""
    custom = tmp_path / "custom.sh"
    custom.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(custom))

    fhs_root = tmp_path / "fhs"
    other = fhs_root / "current" / "installer" / "lib" / "preflight.sh"
    other.parent.mkdir(parents=True)
    other.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("HAL0_FHS_ROOT", str(fhs_root))

    assert _locate_preflight() == custom


def test_locate_preflight_editable_still_wins_over_fhs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The editable-checkout probe is tried before the FHS fallbacks.

    Regression guard: this test suite runs from a real repo checkout, so
    ``hal0.__file__`` genuinely resolves to it — even with FHS env vars set
    (pointing at a script that doesn't exist), the editable probe must still
    win rather than the locator returning None.
    """
    monkeypatch.delenv("HAL0_PREFLIGHT_SH", raising=False)
    monkeypatch.setenv("HAL0_FHS_ROOT", str(tmp_path / "no-such-fhs-root"))
    monkeypatch.setenv("HAL0_PREFIX", str(tmp_path / "no-such-prefix"))

    found = _locate_preflight()
    assert found is not None
    assert found.name == "preflight.sh"
    assert "installer" in found.parts


def test_doctor_sets_ports_soft_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`hal0 doctor` doesn't fail solely because hal0's own ports are bound.

    The stub mirrors the contract ``preflight_ports`` in
    ``installer/lib/preflight.sh`` implements: a bound port is a hard
    failure by default, but ``HAL0_DOCTOR_PORTS_SOFT=1`` (which the ``doctor``
    command sets unconditionally — it's the post-install self-check, not
    the pre-install gate) downgrades it to a warning.
    """
    stub = _make_stub(
        tmp_path,
        (
            'if [[ "${HAL0_DOCTOR_PORTS_SOFT:-0}" == "1" ]]; then\n'
            '  printf "port 8080: already in use (expected — hal0 is running)\\n"\n'
            "  exit 0\n"
            "else\n"
            '  printf "port 8080: already in use\\n"\n'
            "  exit 1\n"
            "fi\n"
        ),
    )
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(stub))

    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_fake_ctx(), verify=False, plain=False, ports=None)

    assert _exit_code(exc) == 0
    captured = capfd.readouterr()
    assert "expected — hal0 is running" in captured.out


def test_preflight_ports_soft_mode_downgrades_to_warning() -> None:
    """Direct contract test against the real script (not a stub).

    Binds a real listening TCP socket so ``preflight_ports`` observes a
    genuine ``ss -ltn`` hit, then asserts: hard mode (install.sh's
    pre-install gate, the default) still fails on a real collision, while
    ``HAL0_DOCTOR_PORTS_SOFT=1`` (set by `hal0 doctor`) does not.
    """
    preflight_sh = Path(__file__).resolve().parents[2] / "installer" / "lib" / "preflight.sh"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        script = f"source {preflight_sh!s}\npreflight_ports {port}\n"

        hard = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert hard.returncode != 0, hard.stdout + hard.stderr

        soft_env = {**os.environ, "HAL0_DOCTOR_PORTS_SOFT": "1"}
        soft = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=soft_env)
        assert soft.returncode == 0, soft.stdout + soft.stderr
    finally:
        sock.close()


# ── hal0 doctor toolbox-pull ──────────────────────────────────────────────────


def _write_manifest(tmp_path: Path, body: dict) -> Path:
    """Drop a minimal manifest.json file under tmp_path and return its path."""
    path = tmp_path / "manifest.json"
    path.write_text(jsonlib.dumps(body))
    return path


def _install_mock_httpx(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Replace ``httpx.Client`` in doctor_commands with one backed by ``handler``.

    We patch the symbol the module bound at import time, not the global
    httpx namespace, so unrelated httpx users in other tests aren't
    affected.
    """
    from hal0.cli import doctor_commands as mod

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client  # capture before monkeypatch swaps it

    def _client_factory(*_args: object, **kwargs: object) -> httpx.Client:
        # Drop caller-supplied transport/follow_redirects — we own them.
        kwargs.pop("transport", None)
        kwargs.pop("follow_redirects", None)
        return real_client_cls(transport=transport, follow_redirects=True, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(mod.httpx, "Client", _client_factory)


def test_toolbox_pull_reports_ok_when_all_images_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: every probe HEAD returns the pinned digest → exit 0."""
    pinned = "sha256:" + "a" * 64
    manifest_path = _write_manifest(
        tmp_path,
        {
            "_schema": "hal0.manifest.v1",
            "version": "1.0.0",
            "toolbox_images": {
                "vulkan": {
                    "tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1",
                    "digest": pinned,
                },
            },
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "anon-bearer"})
        if request.url.path.endswith("/manifests/v1"):
            assert request.method == "HEAD"
            assert request.headers["Authorization"] == "Bearer anon-bearer"
            return httpx.Response(200, headers={"Docker-Content-Digest": pinned})
        raise AssertionError(f"unexpected URL: {request.url}")

    _install_mock_httpx(monkeypatch, handler)

    with pytest.raises(typer.Exit) as exc:
        toolbox_pull(json_output=True, manifest_path=manifest_path)
    assert _exit_code(exc) == 0
    out = capsys.readouterr().out
    rows = jsonlib.loads(out)
    assert rows[0]["ok"] is True
    assert rows[0]["digest"] == pinned
    assert rows[0]["matches_pin"] is True


def test_toolbox_pull_surfaces_digest_drift_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Digest drift is reported in ``matches_pin`` but does NOT exit non-zero."""
    pinned = "sha256:" + "a" * 64
    actual = "sha256:" + "b" * 64
    manifest_path = _write_manifest(
        tmp_path,
        {
            "_schema": "hal0.manifest.v1",
            "toolbox_images": {
                "vulkan": {
                    "tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1",
                    "digest": pinned,
                },
            },
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "anon"})
        return httpx.Response(200, headers={"Docker-Content-Digest": actual})

    _install_mock_httpx(monkeypatch, handler)

    with pytest.raises(typer.Exit) as exc:
        toolbox_pull(json_output=True, manifest_path=manifest_path)
    assert _exit_code(exc) == 0, "digest drift must not flip the exit code"
    rows = jsonlib.loads(capsys.readouterr().out)
    assert rows[0]["ok"] is True
    assert rows[0]["digest"] == actual
    assert rows[0]["matches_pin"] is False


def test_toolbox_pull_exits_nonzero_when_image_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 404 on the manifest URL surfaces as ok=False and exit code 1."""
    manifest_path = _write_manifest(
        tmp_path,
        {
            "_schema": "hal0.manifest.v1",
            "toolbox_images": {
                "vulkan": {
                    "tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1",
                    "digest": None,
                },
            },
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "anon"})
        return httpx.Response(404, text="not found")

    _install_mock_httpx(monkeypatch, handler)

    with pytest.raises(typer.Exit) as exc:
        toolbox_pull(json_output=True, manifest_path=manifest_path)
    assert _exit_code(exc) == 1
    rows = jsonlib.loads(capsys.readouterr().out)
    assert rows[0]["ok"] is False
    assert "404" in (rows[0]["error"] or "")


def test_toolbox_pull_exits_2_when_manifest_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No toolbox_images block → exit 2 (preserved diagnostic semantics)."""
    manifest_path = _write_manifest(
        tmp_path,
        {"_schema": "hal0.manifest.v1", "toolbox_images": {}},
    )

    # No network should be touched in this path; install a handler that
    # blows up loudly if it is.
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network should not be hit when manifest is empty")

    _install_mock_httpx(monkeypatch, handler)

    with pytest.raises(typer.Exit) as exc:
        toolbox_pull(json_output=False, manifest_path=manifest_path)
    assert _exit_code(exc) == 2


def test_toolbox_pull_skips_non_ghcr_refs_with_clear_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-ghcr.io tag is reported as failed without any network calls."""
    manifest_path = _write_manifest(
        tmp_path,
        {
            "_schema": "hal0.manifest.v1",
            "toolbox_images": {
                "vulkan": {
                    "tag": "docker.io/hal0ai/hal0-toolbox-vulkan:v1",
                    "digest": None,
                },
            },
        },
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("non-ghcr tag should not trigger a network call")

    _install_mock_httpx(monkeypatch, handler)

    with pytest.raises(typer.Exit) as exc:
        toolbox_pull(json_output=True, manifest_path=manifest_path)
    assert _exit_code(exc) == 1
    rows = jsonlib.loads(capsys.readouterr().out)
    assert rows[0]["ok"] is False
    assert "ghcr.io" in (rows[0]["error"] or "")


def test_toolbox_pull_invoked_subcommand_skips_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """When a sub-command is invoked, the callback must NOT exec preflight."""
    stub = _make_stub(tmp_path, "printf 'should-not-run\\n'\nexit 99\n")
    monkeypatch.setenv("HAL0_PREFLIGHT_SH", str(stub))

    class _CtxWithSubcommand:
        invoked_subcommand = "toolbox-pull"

    # Doctor callback should return None silently (no typer.Exit) when a
    # sub-command is present.
    result = doctor(ctx=_CtxWithSubcommand(), verify=False, plain=False, ports=None)  # type: ignore[arg-type]
    assert result is None
    assert "should-not-run" not in capfd.readouterr().out
