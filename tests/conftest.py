"""Shared pytest fixtures for hal0 tests."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Test collection must not import the application against the host's FHS
# config. In particular, /etc/hal0/hal0.toml may be root-only on a deployed
# machine. Set an isolated root before importing hal0.api below.
#
# #1490: unlike every other test's isolation root, this one is NOT a
# `tmp_path`-family fixture dir, so pytest's own `tmp_path_retention_*`
# settings never see it — it was leaking one directory per collection
# forever. It's cheap (import-time scaffolding only, not a fixture that
# writes model/registry payloads), but a leak is a leak; clean it up
# ourselves at interpreter exit.
_collection_hal0_home: str | None = None
if not os.environ.get("HAL0_HOME") and not os.access("/etc/hal0/hal0.toml", os.R_OK):
    _collection_hal0_home = tempfile.mkdtemp(prefix="hal0-pytest-")
    os.environ["HAL0_HOME"] = _collection_hal0_home
    atexit.register(shutil.rmtree, _collection_hal0_home, ignore_errors=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hal0.api import create_app  # noqa: E402

# Keep the isolated root only for application import/collection. Tests that
# assert default FHS paths must see HAL0_HOME unset; runtime fixtures provide
# their own temporary roots when isolation is required.
if _collection_hal0_home is not None:
    os.environ.pop("HAL0_HOME", None)

# [memory].enabled defaults to True in the schema, so the bulk of the suite
# — which exercises the memory MCP, the /api/memory/* routes, and the
# Hermes memory provider with memory PRESENT — gets it for free via the
# `app`/`client` fixtures' `tmp_hal0_home` isolation, no env var needed. The
# dedicated gate test (tests/api/test_memory_gate.py) writes memory.enabled
# = false explicitly per-test to cover the off path.

pytest_plugins = ()


# Verbs that mutate system-daemon state. Each one trips polkit on a real
# host: the test hangs on an "Authentication is required to manage system
# service or unit files" dialog until its subprocess timeout expires, and
# on a headless/CI box it silently fails instead. Read-only verbs
# (is-active, show, list-units, ...) are left alone — they need no
# authorization and several probes legitimately shell out to them.
_SYSTEMCTL_MUTATING_VERBS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "try-restart",
        "reload",
        "reload-or-restart",
        "enable",
        "disable",
        "mask",
        "unmask",
        "daemon-reload",
        "daemon-reexec",
        "set-property",
        "kill",
    }
)


# The privilege seam (installer/wrappers/hal0-systemctl, reached as
# `sudo -n /usr/lib/hal0/bin/hal0-systemctl <verb> <id>`) is the SUPPORTED way
# to reach a mutating verb off root — so the production code the guard below
# protects against now legitimately spells its argv as `sudo ...` rather than
# `systemctl ...`. From a test that is still a real escalation attempt against
# the real host, just one indirection further out, so it must be rejected on
# exactly the same terms. Unwrap `sudo [-n|-u X|...] <bin> ...` and judge the
# wrapped command instead of stopping at argv[0].
_SUDO_NAMES = frozenset({"sudo", "doas", "pkexec"})
_SEAM_BIN_NAMES = frozenset({"hal0-systemctl"})


def _strip_sudo(parts: list[str]) -> list[str]:
    """Drop a leading ``sudo``/``doas``/``pkexec`` and its option words."""
    if not parts or Path(parts[0]).name not in _SUDO_NAMES:
        return parts
    rest = parts[1:]
    while rest and rest[0].startswith("-"):
        # -u/--user take a value; the flags hal0 actually uses (-n) do not.
        takes_value = rest[0] in {"-u", "--user", "-g", "--group"}
        rest = rest[2:] if takes_value and len(rest) > 1 else rest[1:]
    return rest


def _reject_privileged_systemctl(argv: object) -> None:
    """Raise if ``argv`` is a systemctl invocation that would need polkit.

    Called from the autouse guard below on every ``subprocess`` entry
    point. Anything that mutates units must go through an injected fake;
    reaching the real system bus from a test is always a bug in the test
    (a missing mock), never intended behavior.

    Covers three shapes, all equivalent in effect:
      * ``systemctl stop <unit>``                     — bare escalation.
      * ``sudo -n .../hal0-systemctl stop-agent <id>`` — via the privilege seam.
      * ``sudo systemctl stop <unit>``                 — sudo'd bare systemctl.
    """
    if isinstance(argv, str):
        parts = argv.split()
    elif isinstance(argv, (list, tuple)):
        parts = [str(a) for a in argv]
    else:
        return
    if not parts:
        return

    inner = _strip_sudo(parts)
    if not inner:
        return
    program = Path(inner[0]).name

    if program in _SEAM_BIN_NAMES:
        # Seam verbs are `<systemctl-verb>` (slot family) or
        # `<systemctl-verb>-agent` (agent family); both mutate. The
        # non-systemctl file verbs (write-quadlet, remove-unit, ...) mutate
        # /etc as root, which a test must never do either.
        #
        # `help`, `check-dropin`, `check-quadlet` and `check-dnat` are the
        # exceptions: all four are pure — they read stdin/argv (and, for
        # check-dnat, LIST the nft ruleset and running containers), write
        # nothing, and never touch systemd or the firewall. Running the real
        # script IS the point for those (tests/installer's #1716 + #1740 +
        # #1814 suites exercise the root-side allow-lists against the shipped
        # bash), and none needs sudo or root.
        seam_verb = next((p for p in inner[1:] if not p.startswith("-")), "")
        if seam_verb and seam_verb not in {
            "help",
            "",
            "check-dropin",
            "check-quadlet",
            "check-dnat",
        }:
            raise AssertionError(
                f"test tried to run the hal0-systemctl privilege seam for real: {parts!r}. "
                "Inject a fake runner (or patch hal0.system.seam.agent_unit_argv) instead — "
                "this shells out to sudo on a developer machine."
            )
        return

    if program != "systemctl":
        return
    verbs = [p for p in inner[1:] if not p.startswith("-")]
    if verbs and verbs[0] in _SYSTEMCTL_MUTATING_VERBS:
        raise AssertionError(
            f"test tried to run privileged systemctl against the real system bus: {parts!r}. "
            "Inject a fake runner (or monkeypatch the probe) instead — this raises a polkit "
            "password prompt on a developer machine and hangs the suite."
        )


@pytest.fixture(autouse=True)
def _no_real_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard-fail any test that shells out to a mutating ``systemctl``.

    Regression guard: ``HermesDriver._stop_services`` was a staticmethod
    calling the module-global ``subprocess``, bypassing the driver's own
    ``runner`` injection point, so all twelve ``uninstall()`` tests ran
    ``systemctl stop`` + ``systemctl disable hal0-agent@hermes.service``
    on the host. That's invisible in the test output — it shows up as a
    slow suite and a stack of polkit dialogs. Fail loudly at the call
    site instead so the next such leak is a one-line traceback.

    Only ``Popen`` is patched: ``run``/``call``/``check_call``/
    ``check_output`` all resolve ``Popen`` from module globals at call
    time, so one choke point covers them — including callers that
    captured ``subprocess.run`` by reference at import time. Rebinding
    the functions themselves would also break identity assertions like
    ``InstallIO.run is subprocess.run``.
    """
    import subprocess as _sp

    real_popen = _sp.Popen

    class _GuardedPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, args: object, *pargs: object, **kwargs: object) -> None:
            _reject_privileged_systemctl(args)
            super().__init__(args, *pargs, **kwargs)

    monkeypatch.setattr(_sp, "Popen", _GuardedPopen)


@pytest.fixture(autouse=True)
def _no_static_slot_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence the lifespan's static slot-TOML seeding (flm/tts/rerank/
    utility/img/agent/brain — #1218) for the whole suite by default.

    That hook exists so `hal0 update` converges an existing box; every
    other test's contract (documented on ``app`` below) is an EMPTY
    config tree on TestClient boot — first_run flags, slot-capacity
    math, and install/apply model-pick logic all assume zero slots
    pre-exist. tests/api/test_startup_slot_seed.py overrides this
    fixture (same name) to exercise the real behavior.
    """
    import hal0.install.static_seeds as static_seeds_mod

    monkeypatch.setattr(static_seeds_mod, "seed_static_slots", lambda **_kw: [])


@pytest.fixture(autouse=True)
def _store_not_nfs_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``hal0.config.store.is_nfs_path`` to False for the whole suite.

    ML-3's NFS-relabel-omission fix (plan §23.3d) detects the REAL host's
    ``/proc/mounts`` — on a dev box that happens to NFS-mount
    ``/mnt/ai-models`` (a common hal0 deployment shape), that's a true
    positive: mount-rendering tests hardcoding ``/mnt/ai-models`` as their
    model-store literal would otherwise pass/fail based on the CI host's
    actual mount table, not the code under test. Force the deterministic
    "local filesystem" default suite-wide; ``tests/config/test_store.py``
    overrides this per-test to exercise the real NFS-detection branch.
    """
    monkeypatch.setattr("hal0.config.store.is_nfs_path", lambda _p: False)


@pytest.fixture(autouse=True)
def _auth_dev_open_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the KB-1/§1 auth middleware into dev-open for the whole suite.

    ``require_auth_enabled()`` derives its default from process env: it
    enforces when the bind host is non-loopback OR a key is configured.
    Some tests legitimately write those into ``os.environ`` (e.g.
    ``tests/install/test_answers.py`` asserts the answers-apply path sets
    ``HAL0_BIND_HOST=0.0.0.0``), and that value LEAKS to later tests in the
    same pytest process — flipping the middleware on and 401-ing every
    unrelated endpoint test that hits ``/v1`` or an ``/api`` admin route
    anonymously. Pinning ``HAL0_REQUIRE_AUTH=0`` here makes the ~700-test
    suite deterministically dev-open regardless of leakage; the auth tests
    that need enforcement opt in explicitly (their own ``monkeypatch.setenv``
    runs after this fixture and wins), and the posture-derivation tests in
    ``tests/api/test_auth_core.py`` ``delenv`` it first to exercise the
    bind/key-derived default.
    """
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")


@pytest.fixture(autouse=True)
def _isolate_hal0_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from host FHS state, including non-API unit tests."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


@pytest.fixture(scope="function")
def app(tmp_hal0_home: str) -> FastAPI:
    """Return a fresh FastAPI app instance, filesystem-isolated under tmp_hal0_home.

    Auto-applying tmp_hal0_home means every TestClient-driven test starts
    against an empty config tree — no host /etc/hal0/slots/*.toml leaks
    into upstream registration, no host /var/lib/hal0/registry leaks into
    the model list. Tests that need to populate config should write into
    ``Path(tmp_hal0_home) / "etc" / "hal0" / ...`` before constructing
    the client.
    """
    return create_app()


@pytest.fixture(scope="function")
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with lifespan executed (so app.state singletons exist)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def tmp_hal0_home(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set HAL0_HOME to a temporary directory for filesystem isolation.

    Also opts the systemd-override renderer into the HAL0_HOME branch so
    unit-template tests write under tmp_path instead of /etc/systemd/system.
    """
    home = str(tmp_path)
    monkeypatch.setenv("HAL0_HOME", home)
    monkeypatch.setenv("HAL0_OVERRIDE_DIR", "hal0_home")
    return home


@pytest.fixture(autouse=True)
def _clear_image_present_cache():
    """Reset slot_view's process-global image-presence TTL cache per test.

    The cache exists so the dashboard's 2.5 s poll doesn't re-run
    ``podman image inspect`` per slot per poll; across tests it would leak
    one test's patched ``image_present`` answer into the next.
    """
    from hal0 import slot_view

    slot_view._image_present_cache.clear()
    yield
    slot_view._image_present_cache.clear()
