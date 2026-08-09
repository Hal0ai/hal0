"""``hal0 doctor all`` — one read-only evidence pass over the box (§21.4).

The individual ``doctor`` subcommands each audit one surface (``perms``,
``models``, ``migrations``, ``profiles``) and ``doctor verify`` renders the
live-API report card. ``doctor all`` composes the *read-only* evidence into a
single roll-up so an operator (or a bug report) gets the whole picture in one
command, with ``--json`` for machine consumers.

It re-uses the tested :class:`hal0.cli.doctor_verify.Check` row type and the
verify report-card classifiers (API, runners, DNS, capabilities, memory,
OpenWebUI, Hermes), then adds the broader health rows the retrofit calls for:
auth posture, model-store integrity, pending migrations, a stale-dashboard
``HAL0_UI_DIST`` override (#1589), bound slot ports, the ``hal0.target``
boot-enable anchor (r5-sync-assessment §6.1), and the privileged sudo seams
every slot op + self-update runs through (#1465).

Strictly read-only — there is no ``--fix`` here; the per-surface subcommands
own repair. Exit codes:

* 0 — everything clean (advisory ``warn`` rows may still print)
* 1 — at least one non-critical ``fail`` (actionable finding)
* 2 — a critical row failed (API unreachable / zero healthy runners)
"""

from __future__ import annotations

import json as jsonlib
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli.doctor_verify import (
    _FAIL,
    _PASS,
    _WARN,
    Check,
    build_checks,
    gather_payloads,
)
from hal0.config import paths
from hal0.system.seam_check import REMEDIATION, SeamStatus, probe_seams

console = Console()


# ── extra read-only classifiers (pure — take parsed JSON, return a Check) ──────


def check_auth_posture(auth: dict[str, Any] | None) -> Check:
    """Auth exposure posture from ``GET /api/auth/status`` (advisory).

    ``auth_required`` + ``has_admin_key`` describe whether the box gates
    access. The only misconfiguration we flag is "auth required but no admin
    key configured" (nobody can log in); an intentionally open dev install
    passes with a note.
    """
    if auth is None:
        return Check("auth", "Auth posture", _WARN, "auth status unreachable")
    required = bool(auth.get("auth_required"))
    has_key = bool(auth.get("has_admin_key"))
    if not required:
        return Check("auth", "Auth posture", _PASS, "open (auth not required — dev/loopback)")
    if not has_key:
        return Check(
            "auth",
            "Auth posture",
            _WARN,
            "auth required but no admin key set — set HAL0_ADMIN_KEY so an operator can log in",
        )
    return Check("auth", "Auth posture", _PASS, "auth required, admin key configured")


#: Secret-bearing files whose mode ``doctor`` refuses to let drift open.
_SECRET_FILES: tuple[Callable[[], Path], ...] = (
    lambda: paths.api_env(),
    lambda: paths.openwebui_env(),
)


def check_secret_file_modes() -> Check:
    """Secret-bearing config files must not be group- or world-readable.

    An independent backstop for #1466, deliberately NOT derived from
    ``install/perms.py``'s table: that table is what got it wrong — it pinned
    ``api.env`` at 0644 behind a ``FIXME(phase4)`` while the file held live
    provider tokens and, after a rotation, the box's admin key. A check
    generated from the same table would have agreed with the bug. This one
    asserts the property directly, so a fifth writer, a re-widened PermRow, or
    a hand ``chmod`` all surface here.

    Critical: the finding is "every local account can read your API keys".
    A missing file is clean — plenty of boxes have no ``openwebui.env``.
    """
    offenders: list[str] = []
    for resolve in _SECRET_FILES:
        path = resolve()
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            continue  # absent or unreadable — nothing to assert
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            offenders.append(f"{path.name} is {mode:o} ({path})")
    if offenders:
        return Check(
            "secret-modes",
            "Secret file modes",
            _FAIL,
            "world/group-readable secret file(s): "
            + "; ".join(offenders)
            + f" — expected {paths.API_ENV_MODE:o}; fix with `hal0 doctor perms --fix`",
            critical=True,
        )
    return Check("secret-modes", "Secret file modes", _PASS, "secret files are owner-only")


def check_voice_stt_weights() -> Check:
    """Moonshine STT weights preflight — the same rule slot spawn enforces.

    The moonshine ONNX bundle is operator-staged (multi-file, not registry-
    pulled), so a fresh box can configure the stt slot and only find out the
    weights are missing when the container 500s. This row runs
    :func:`hal0.providers.moonshine.check_moonshine_weights` against the
    profile-baked ``--model_path`` so the gap is named here first. An empty
    ``--model_path`` is the in-container HuggingFace auto-download path —
    legitimate but slow on first start, so it warns rather than fails.
    """
    import shlex

    from hal0.errors import Hal0Error
    from hal0.profiles import ProfileCatalog
    from hal0.providers.moonshine import check_moonshine_weights

    try:
        profile = ProfileCatalog().resolve("moonshine")
    except Exception:
        return Check(
            "stt-weights",
            "Moonshine weights",
            _PASS,
            "moonshine profile absent — nothing to preflight",
        )
    tokens = shlex.split(profile.resolved_flags or "")
    model_path = ""
    for i, tok in enumerate(tokens[:-1]):
        if tok == "--model_path":
            model_path = tokens[i + 1]
    if not model_path:
        return Check(
            "stt-weights",
            "Moonshine weights",
            _WARN,
            "no --model_path in the moonshine profile — first slot start will "
            "auto-download from HuggingFace inside the container (slow, needs egress)",
        )
    try:
        check_moonshine_weights(model_path)
    except Hal0Error as exc:
        return Check("stt-weights", "Moonshine weights", _FAIL, str(exc))
    return Check(
        "stt-weights",
        "Moonshine weights",
        _PASS,
        f"staged bundle present at {model_path}",
    )


def check_model_store(
    models: Any,
    *,
    exists: Any = None,
) -> Check:
    """Registry entries whose file is gone → a non-critical fail.

    Mirrors ``doctor models`` step 1 (registry → file existence) but as a
    single roll-up row. ``exists`` is an injectable seam (defaults to
    ``Path.exists``) so the classifier is testable without touching disk.
    """
    _exists = exists if exists is not None else (lambda p: Path(p).exists())
    if models is None:
        return Check("models", "Model store", _WARN, "models endpoint unreachable")
    rows = models.get("models", models) if isinstance(models, dict) else models
    if not isinstance(rows, list):
        return Check("models", "Model store", _WARN, "unexpected models payload")
    local = [m for m in rows if isinstance(m, dict) and m.get("path")]
    dangling = [m for m in local if not _exists(str(m["path"]))]
    if dangling:
        names = ", ".join(str(m.get("id") or m.get("path")) for m in dangling[:3])
        more = f" (+{len(dangling) - 3} more)" if len(dangling) > 3 else ""
        return Check(
            "models",
            "Model store",
            _FAIL,
            f"{len(dangling)} registry entr(y/ies) point at missing files: {names}{more} "
            "— run `hal0 doctor models` to triage",
        )
    return Check("models", "Model store", _PASS, f"{len(local)} registered file(s) present")


def check_migrations(pending: tuple[int, int] | None) -> Check:
    """Pending v0.1→v0.2 model-layout migration (advisory warn).

    ``pending`` is the ``(create, overwrite)`` tuple from
    :func:`hal0.cli.doctor_commands.pending_layout_migration`, or ``None`` when
    the planner could not be consulted (degrades to a skipped/pass note).
    """
    if pending is None:
        return Check(
            "migrations", "Migrations", _PASS, "layout migration planner unavailable — skipped"
        )
    create, overwrite = pending
    if not create and not overwrite:
        return Check("migrations", "Migrations", _PASS, "model layout current")
    detail = f"{create} link(s) to create"
    if overwrite:
        detail += f", {overwrite} to overwrite"
    return Check(
        "migrations",
        "Migrations",
        _WARN,
        f"model-layout migration pending: {detail} — run `hal0 migrate model-layout --apply`",
    )


def _read_env_value(
    env_path: Path, key: str, *, read_text: Callable[[Path], str] | None = None
) -> str | None:
    """Best-effort ``KEY=value`` read from an ``EnvironmentFile``-style file.

    Mirrors the line convention :mod:`hal0.api._env_store` writes (a plain
    or double-quoted ``KEY=value``, one per line — see ``_line_targets_key``).
    A commented-out line (``# KEY=value``) does not count as set. Returns
    ``None`` when the file, or the key inside it, is absent — never raises.
    """
    _read = read_text if read_text is not None else (lambda p: p.read_text(encoding="utf-8"))
    try:
        text = _read(env_path)
    except OSError:
        return None
    prefix = f"{key}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :]
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            return value
    return None


def check_ui_dist(
    *, api_env_path: Path | None = None, current_ui_dist: Path | None = None
) -> Check:
    """Compare the effective dashboard bundle against the current release.

    ``HAL0_UI_DIST`` in ``api.env`` is installer-owned wiring, not operator
    config — on an FHS box its only correct value is ``<current>/ui/dist``
    (see :func:`hal0.config.paths.usr_lib`). The installer only *writes* that
    line when ``api.env`` doesn't exist yet (fresh install); on every
    subsequent run it preserves whatever sits outside the marker-delimited
    network block, by design, so an operator edit elsewhere in the file
    survives. That same preservation means a box that ever had
    ``HAL0_UI_DIST`` pointed at an older layout (e.g. a pre-FHS git-deploy
    tree) silently stops receiving UI updates on every future upgrade: the
    updater stages a fresh bundle under the new release, and the dashboard
    keeps serving the stale one against the new API (#1589).

    No override at all is the common, healthy case (the FastAPI mount falls
    through to ``<current>/ui/dist`` on its own — see
    ``hal0.api._mount_dashboard``) and passes silently.
    """
    api_env_path = api_env_path if api_env_path is not None else paths.api_env()
    expected = current_ui_dist if current_ui_dist is not None else (paths.usr_lib() / "ui" / "dist")

    override = _read_env_value(api_env_path, "HAL0_UI_DIST")
    if not override:
        return Check(
            "ui-dist",
            "Dashboard bundle",
            _PASS,
            "no HAL0_UI_DIST override in api.env — tracks the current release bundle",
        )

    override_path = Path(override)
    try:
        matches = override_path.resolve() == expected.resolve()
    except OSError:
        matches = override_path == expected
    if matches:
        return Check(
            "ui-dist",
            "Dashboard bundle",
            _PASS,
            f"HAL0_UI_DIST={override} matches the current release bundle",
        )
    return Check(
        "ui-dist",
        "Dashboard bundle",
        _WARN,
        f"HAL0_UI_DIST={override} in api.env does not resolve inside {expected} (the current "
        "release bundle) — the dashboard may be serving a stale UI against the new API. Remove "
        f"the override (or point it at {expected}) in api.env, then restart hal0-api.",
    )


def _hal0_target_enabled_probe() -> bool | None:
    """Real ``systemctl is-enabled --quiet hal0.target`` probe.

    Returns ``True``/``False`` for a definitive answer, ``None`` when the
    question can't be asked at all (no ``systemctl`` on PATH, e.g. a
    container/CI box) — the caller degrades that to an advisory warn rather
    than a fail. Kept as a free function (not inlined) so tests can swap it
    out via the ``is_enabled`` seam without touching subprocess.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["systemctl", "is-enabled", "--quiet", "hal0.target"],
            check=False,
            timeout=5,
        )
    except OSError:
        return None
    return result.returncode == 0


def check_hal0_target(
    *,
    unit_dir: Path | None = None,
    exists: Callable[[Path], bool] | None = None,
    is_enabled: Callable[[], bool | None] | None = None,
) -> Check:
    """``hal0.target`` — the boot-enable anchor every slot Quadlet depends on.

    Every rendered per-slot Quadlet declares
    ``[Install] WantedBy=hal0.target`` (``providers/container.py``); if the
    target unit is missing, or installed but not enabled, slots that looked
    healthy before a reboot silently stay down after one
    (r5-sync-assessment §6.1, launch-blocker #1). ``exists``/``is_enabled``
    are injectable seams (mirrors ``check_model_store``'s ``exists`` param)
    so this is testable without a real systemd or filesystem.
    """
    _unit_dir = unit_dir if unit_dir is not None else Path("/etc/systemd/system")
    _exists = exists if exists is not None else (lambda p: p.exists())
    unit_path = _unit_dir / "hal0.target"
    if not _exists(unit_path):
        return Check(
            "hal0_target",
            "hal0.target",
            _FAIL,
            f"{unit_path} not installed — slots will not autostart after reboot; "
            "re-run the installer (sudo bash install.sh) to ship it",
        )
    _is_enabled = is_enabled if is_enabled is not None else _hal0_target_enabled_probe
    enabled = _is_enabled()
    if enabled is False:
        return Check(
            "hal0_target",
            "hal0.target",
            _FAIL,
            "hal0.target installed but not enabled — run `sudo systemctl enable --now hal0.target`",
        )
    if enabled is None:
        return Check(
            "hal0_target",
            "hal0.target",
            _WARN,
            "hal0.target installed — enabled state unknown (systemctl unavailable)",
        )
    return Check("hal0_target", "hal0.target", _PASS, "installed and enabled")


def check_seams(statuses: list[SeamStatus] | None = None) -> Check:
    """The privileged sudo seams every slot op and self-update runs through (#1465).

    ``install.sh`` installs each wrapper + ``/etc/sudoers.d`` grant best-effort:
    a ``visudo -cf`` failure or a missing source produced only a mid-log warn,
    and *nothing* verified the result afterwards — so a box where that warn
    fired reported all-green from every doctor surface while every slot start,
    unit write and daemon-reload failed undiagnosably. This row is that missing
    verification.

    Required seams (``hal0-systemctl``, ``hal0-update``) failing is a non-critical
    ``fail``; an optional seam, or a grant we could not test from this account,
    is an advisory ``warn``. ``statuses`` is injectable so the classification is
    testable without root or a provisioned box (mirrors ``check_hal0_target``).
    """
    rows = probe_seams() if statuses is None else statuses
    if not rows:
        return Check("seams", "Privileged seams", _WARN, "no seams to check")

    broken_required = [s for s in rows if s.spec.required and not s.ok]
    if broken_required:
        problems = "; ".join(p for s in broken_required for p in s.problems)
        roles = ", ".join(s.spec.role for s in broken_required)
        return Check(
            "seams",
            "Privileged seams",
            _FAIL,
            f"{problems} — {roles} cannot work; {REMEDIATION}",
        )

    broken_optional = [s for s in rows if not s.spec.required and not s.ok]
    if broken_optional:
        problems = "; ".join(p for s in broken_optional for p in s.problems)
        return Check("seams", "Privileged seams", _WARN, f"{problems} — {REMEDIATION}")

    untested = [s for s in rows if s.spec.probe is not None and s.grant_ok is None]
    if untested:
        names = ", ".join(s.spec.name for s in untested)
        return Check(
            "seams",
            "Privileged seams",
            _WARN,
            f"{len(rows)} seam(s) installed; grant not exercised for {names} "
            "(re-run as root to prove it end-to-end)",
        )

    proved = [s for s in rows if s.grant_ok is True]
    return Check(
        "seams",
        "Privileged seams",
        _PASS,
        f"{len(rows)} installed root:root; {len(proved)} grant(s) verified via sudo -n",
    )


def check_ports(slots: Any) -> Check:
    """Bound slot ports (informational evidence, always advisory-clean).

    Surfaces the ports hal0's slots currently occupy so a port-collision
    diagnosis has the evidence in the same report. Never fails — a box with no
    slots yet is legal.

    Two distinct non-answers are kept apart (#1501). ``None`` means the fetch
    itself produced nothing — which covers a genuinely down API *and* a healthy
    one that simply outran the request budget, so the copy must not assert
    unreachability. A non-list body means the route answered with a shape this
    check doesn't understand, which is a contract fault, not a connectivity
    one. Collapsing both into "unreachable" is what made this row cry wolf on a
    box serving all 18 slots.
    """
    if slots is None:
        return Check(
            "ports",
            "Slot ports",
            _WARN,
            "no answer from the slots endpoint (down, or slower than the probe budget) "
            "— run `hal0 doctor ports`",
        )
    if not isinstance(slots, list):
        return Check(
            "ports",
            "Slot ports",
            _WARN,
            f"unexpected slots payload: {type(slots).__name__}, expected a list "
            "— run `hal0 doctor ports`",
        )
    bound = sorted({int(s["port"]) for s in slots if isinstance(s, dict) and s.get("port")})
    if not bound:
        return Check("ports", "Slot ports", _PASS, "no slot ports bound yet")
    return Check("ports", "Slot ports", _PASS, f"{len(bound)} bound: {', '.join(map(str, bound))}")


def _default_mcp_probe_targets() -> tuple[tuple[str, str], ...]:
    """(name, mount-root URL) pairs for hal0's two bundled MCP servers."""
    return (
        ("hal0-admin", "http://127.0.0.1:8080/mcp/admin"),
        ("hal0-memory", "http://127.0.0.1:8080/mcp/memory"),
    )


def probe_builtin_mcp_mounts(
    probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the real MCP ``initialize`` → ``tools/list`` handshake against
    both bundled mounts, using the box service key the same way a
    provisioned Hermes config.yaml does.

    Reuses :func:`hal0.agents.hermes_provision._probe_mcp_server` — the same
    initialize/notifications/tools-list sequence + bearer resolution a real
    provision run already exercises — rather than re-implementing MCP
    transport here. Late import keeps a bad import in the (much larger)
    provisioning module from taking down `hal0 doctor`.
    """
    if probe is None:
        from hal0.agents.hermes_provision import _probe_mcp_server as probe

    return {
        name: probe(url, agent_id="hal0-doctor", private=(name == "hal0-memory"))
        for name, url in _default_mcp_probe_targets()
    }


def check_mcp_mounts(results: dict[str, dict[str, Any]] | None = None) -> Check:
    """Live regression guard for the silent-401 class (§ MCP bearer wiring).

    A provisioned box can look completely healthy — config.yaml has the
    right shape, the daemon is up — and still have every hermes-issued MCP
    call silently 401 (bearer never resolved at provision time, a key
    rotated since, or the ADR-0013 allow-list quietly filtering the builtins
    out). This is the row that actually dials both mounts and proves the
    handshake completes under the box's CURRENT auth posture, instead of
    trusting that a static config implies a working connection.

    ``results`` is injectable (mirrors ``check_hal0_target``/``check_seams``)
    so the classifier is testable without a live API. PASS requires BOTH
    mounts to answer ``tools/list`` with a non-empty tool list; a 401/403 is
    called out by name with the exact repair command, other transport
    failures (connection refused, timeout, 5xx) get their raw error text.
    """
    rows = results if results is not None else probe_builtin_mcp_mounts()
    failures: list[str] = []
    for name, probe in rows.items():
        if not probe.get("ok"):
            err = str(probe.get("error") or "unreachable")
            if any(marker in err for marker in ("401", "403", "Unauthorized", "Forbidden")):
                failures.append(
                    f"{name}: {err} — agent token missing/stale: "
                    "run `hal0 agent bootstrap hermes --repair`"
                )
            else:
                failures.append(f"{name}: {err}")
        elif not probe.get("tools"):
            failures.append(f"{name}: reachable but advertised zero tools")
    if failures:
        return Check("mcp_mounts", "MCP mounts (admin/memory)", _FAIL, "; ".join(failures))
    tool_counts = ", ".join(f"{n}={len(p.get('tools') or [])}" for n, p in rows.items())
    return Check(
        "mcp_mounts",
        "MCP mounts (admin/memory)",
        _PASS,
        f"both mounts completed initialize -> tools/list ({tool_counts})",
    )


def check_hermes_mcp_config_auth(
    *,
    auth: dict[str, Any] | None,
    config_path: Path | None = None,
    read_text: Callable[[Path], str] | None = None,
) -> Check:
    """Hermes' rendered ``config.yaml`` must carry ``Authorization`` on both
    ``mcp_servers`` entries whenever the box requires auth.

    Static counterpart to :func:`check_mcp_mounts`: that row proves the LIVE
    handshake works right now (as the box's own service key, from wherever
    `hal0 doctor` runs); this one proves the specific artifact
    ``hal0 agent bootstrap hermes`` renders is what actually carries the
    bearer forward into every Hermes-issued call — the drift class where
    ``HAL0_ADMIN_KEY`` was set or rotated *after* the last provision/repair
    pass, so the live config.yaml is stale even though the box's current key
    would probe clean.

    A missing ``config.yaml`` (hermes never provisioned) passes — nothing to
    check yet. An auth-open box passes unconditionally — a tokenless
    config.yaml is exactly the expected shape (see
    ``test_overlay_omits_bearer_when_no_key_discoverable``).
    """
    from hal0.agents.hermes_provision import HERMES_HOME_DEFAULT

    path = config_path if config_path is not None else (HERMES_HOME_DEFAULT / "config.yaml")
    _read = read_text if read_text is not None else (lambda p: p.read_text(encoding="utf-8"))
    if not path.exists():
        return Check(
            "hermes_mcp_auth",
            "Hermes MCP config auth",
            _PASS,
            "hermes not provisioned — nothing to check",
        )
    if not bool((auth or {}).get("auth_required")):
        return Check(
            "hermes_mcp_auth",
            "Hermes MCP config auth",
            _PASS,
            "auth not required — a tokenless config.yaml is expected",
        )
    try:
        text = _read(path)
    except OSError as exc:
        return Check(
            "hermes_mcp_auth", "Hermes MCP config auth", _WARN, f"could not read {path}: {exc}"
        )

    import yaml

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return Check(
            "hermes_mcp_auth", "Hermes MCP config auth", _WARN, f"could not parse {path}: {exc}"
        )
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    servers = servers if isinstance(servers, dict) else {}
    missing = [
        name
        for name in ("hal0-admin", "hal0-memory")
        if isinstance(servers.get(name), dict)
        and not ((servers[name].get("headers") or {}).get("Authorization"))
    ]
    if missing:
        return Check(
            "hermes_mcp_auth",
            "Hermes MCP config auth",
            _FAIL,
            f"auth is required but {', '.join(missing)} carries no Authorization header in "
            f"{path} — run `hal0 agent bootstrap hermes --repair`",
        )
    return Check(
        "hermes_mcp_auth",
        "Hermes MCP config auth",
        _PASS,
        "config.yaml carries Authorization for both bundled mounts",
    )


#: The placeholder credential the hindsight-api unit ships for the auth-off
#: posture — see installer/systemd/hindsight-api.service.
_HINDSIGHT_LLM_PLACEHOLDER = "hal0-local-noauth"


def check_hindsight_llm_auth(
    *,
    auth: dict[str, Any] | None,
    unit_path: Path | None = None,
    env_path: Path | None = None,
) -> Check:
    """The memory engine's LLM credential must be a real key when auth is on.

    hindsight-api calls back into hal0's ``/v1`` (CLIENT tier) for every
    extraction and reflect. Its unit ships the ``hal0-local-noauth``
    placeholder, overridden by ``/etc/hal0/hindsight-llm.env``. On an
    auth-required box a missing/placeholder/stale key means every retain
    fails extraction with a 401 — silently, because retain is async
    (#1543's engine-side sibling).
    """
    unit = unit_path if unit_path is not None else Path("/etc/systemd/system/hindsight-api.service")
    env_file = env_path if env_path is not None else Path("/etc/hal0/hindsight-llm.env")
    name, title = "hindsight_llm_auth", "Memory engine LLM auth"
    if not unit.exists():
        return Check(name, title, _PASS, "memory engine not installed — nothing to check")
    if not bool((auth or {}).get("auth_required")):
        return Check(name, title, _PASS, "auth not required — placeholder credential is fine")

    key = ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
                key = line.split("=", 1)[1].strip()
    except OSError:
        key = ""
    if not key or key == _HINDSIGHT_LLM_PLACEHOLDER:
        return Check(
            name,
            title,
            _FAIL,
            f"auth is required but {env_file} carries no real LLM key — every retain/reflect "
            "401s against /v1. Write a client-tier key there (`hal0 auth rotate` refreshes it "
            "automatically from now on) and restart hindsight-api",
        )

    from hal0.service_identity import keys_from_api_env

    live = set(keys_from_api_env().values())
    if live and key not in live:
        return Check(
            name,
            title,
            _FAIL,
            f"{env_file} carries a key that matches neither current box key (stale after a "
            "rotation?) — refresh it and restart hindsight-api",
        )
    return Check(name, title, _PASS, "memory engine carries a current client-tier LLM key")


# ── orchestration ──────────────────────────────────────────────────────────────

# GET /api/slots is the slowest read-only route hal0 serves: the aggregator
# merges SlotManager-backed entries with upstream-backed ones and container-
# probes each, so cost scales with slot count. Measured on lxc105 (18 slots,
# main @ 5be7f2a5): 11.2-14.6s, against the 10.0s default every other doctor
# fetch uses — which is exactly how a healthy box got reported unreachable
# (#1501). The budget is generous because this is a diagnostic: waiting is
# strictly better than a false negative on the operator's first-line tool.
#
# NOTE: this makes `doctor` honest about a slow endpoint; it does not make the
# endpoint fast. The underlying /api/slots latency is tracked separately — the
# dashboard polls that same route.
SLOTS_PROBE_TIMEOUT_S: float = 30.0


def _get_any(path: str, base: str | None, *, timeout: float = 10.0) -> Any:
    """Best-effort GET returning the raw parsed body (dict OR list), else None.

    ``timeout`` is per-call so a slow-but-legitimate route can be given room
    without loosening the budget for every other probe in the roll-up.
    """
    from hal0.cli._shared import CliApiError, api_get

    try:
        return api_get(path, base=base, timeout=timeout)
    except CliApiError:
        return None


def build_all_checks(base: str | None = None) -> list[Check]:
    """Fetch every read-only source and compose the full ordered check list."""
    payloads = gather_payloads(base)
    verify_rows = build_checks(
        health=payloads["health"],
        urls=payloads["urls"],
        system=payloads["system"],
        capabilities=payloads["capabilities"],
        memory=payloads["memory"],
        services=payloads["services"],
    )

    from hal0.cli.doctor_commands import pending_layout_migration

    auth_payload = _get_any("/api/auth/status", base)
    extra_rows = [
        check_auth_posture(auth_payload),
        check_model_store(_get_any("/api/models", base)),
        check_migrations(pending_layout_migration()),
        check_ui_dist(),
        check_ports(_get_any("/api/slots", base, timeout=SLOTS_PROBE_TIMEOUT_S)),
        check_hal0_target(),
        check_secret_file_modes(),
        check_voice_stt_weights(),
        check_seams(),
        check_mcp_mounts(),
        check_hermes_mcp_config_auth(auth=auth_payload),
        check_hindsight_llm_auth(auth=auth_payload),
    ]
    return verify_rows + extra_rows


def overall_verdict(checks: list[Check]) -> str:
    """Roll the rows up to ``ok`` | ``fail`` | ``critical``.

    ``critical`` iff any critical row failed; ``fail`` iff any non-critical
    ``fail`` is present; else ``ok`` (advisory ``warn`` rows do not block).
    """
    if any(c.status == _FAIL and c.critical for c in checks):
        return "critical"
    if any(c.status == _FAIL for c in checks):
        return "fail"
    return "ok"


_BADGE = {
    _PASS: "[green]✔ PASS[/green]",
    _WARN: "[yellow]▲ WARN[/yellow]",
    _FAIL: "[red]✖ FAIL[/red]",
}
_CRIT_BADGE = "[bold red]✖ FAIL[/bold red]"


def render_all(con: Console, checks: list[Check]) -> None:
    """Print the aggregate evidence table."""
    table = Table(title="hal0 doctor — evidence roll-up")
    table.add_column("Status", width=9)
    table.add_column("Check", style="bold", width=18)
    table.add_column("Detail")
    for c in checks:
        badge = _CRIT_BADGE if (c.status == _FAIL and c.critical) else _BADGE[c.status]
        table.add_row(badge, c.label, c.detail)
    con.print(table)


def _exit_code(checks: list[Check]) -> int:
    verdict = overall_verdict(checks)
    return {"ok": 0, "fail": 1, "critical": 2}[verdict]


def doctor_all_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the check rows as JSON instead of the human table.",
    ),
) -> None:
    """Run every read-only doctor check in one pass and roll up a verdict.

    Composes the ``doctor verify`` report card (API, runners, DNS, capability
    slots, memory, OpenWebUI, Hermes) with auth posture, model-store integrity,
    pending migrations, bound slot ports, the ``hal0.target`` boot-enable
    anchor, and the privileged sudo seams. Read-only — use the per-surface subcommands (``perms``/``models``)
    for ``--fix``.

    Exit codes: 0 clean, 1 an actionable fail, 2 a critical failure (API
    unreachable / zero healthy runners).
    """
    checks = build_all_checks()
    if json_output:
        rows = [
            {
                "key": c.key,
                "label": c.label,
                "status": c.status,
                "detail": c.detail,
                "critical": c.critical,
            }
            for c in checks
        ]
        console.print_json(jsonlib.dumps(rows))
    else:
        render_all(console, checks)
    raise typer.Exit(_exit_code(checks))


__all__ = [
    "SLOTS_PROBE_TIMEOUT_S",
    "build_all_checks",
    "check_auth_posture",
    "check_hal0_target",
    "check_hermes_mcp_config_auth",
    "check_hindsight_llm_auth",
    "check_mcp_mounts",
    "check_migrations",
    "check_model_store",
    "check_ports",
    "check_seams",
    "check_ui_dist",
    "doctor_all_cmd",
    "overall_verdict",
    "probe_builtin_mcp_mounts",
    "render_all",
]
