"""``hal0 doctor bundle`` — the §21.4 support-bundle generator.

A single, operator-curated directory an operator ``tar czf``s and hands to
support / attaches to a bug report. Read-only, offline-tolerant, and
redacts every config dump it writes (reuses :func:`hal0.api._redact.redact_config`
— the existing canonical masker; this module does not roll a second one).

Layout (see the module's :func:`build_bundle` docstring + spec-21-4-doctor.md
§3.1)::

    hal0-doctor-bundle-<host>-<UTC-ts>/
    ├── manifest.json
    ├── commands.tsv
    ├── system/            # uname, os-release, cmdline, rocminfo, rocm-smi, ...
    ├── config/            # hal0.toml / api.env / slots/*.toml / profiles.toml
    │                      # / capabilities.toml — every dump REDACTED
    ├── diagnostics/       # doctor {perms,migrations,profiles,verify}.json +
    │                      # best-effort GET /api/{models,slots,hardware,
    │                      # system-info,stats}
    ├── logs/              # journalctl captures (best-effort; absent on a
    │                      # systemd-less box like this dev sandbox)
    └── doctor-summary.txt # the rich `doctor verify` report, as plain text

No upload — the bundle is written to local disk only (§3.3). No
``hal0 doctor fix <id>`` dispatcher here either — ``next_steps[]`` in the
diagnostics only reference the future command by name.
"""

from __future__ import annotations

import json as jsonlib
import re
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

console = Console()

_BEARER_RE = re.compile(r"Bearer\s+\S+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

_REDACTION_POLICY = (
    "SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT "
    "(hal0.api._redact._SENSITIVE_RE)"
)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact_text(text: str) -> str:
    """Bearer-token / JWT scrub for free-text command output (§3.1).

    Complements (does not replace) ``api._redact.redact_config``, which
    scrubs by KEY NAME in structured config trees — this is for arbitrary
    stdout (e.g. a log line that happens to carry an ``Authorization:
    Bearer ...`` header, or a JWT printed by a debug command).
    """
    return _BEARER_RE.sub("Bearer ***REDACTED***", _JWT_RE.sub("***JWT***", text))


# ── §3.1 system/ probe table — (label, argv, output path) ─────────────────────
# Every probe degrades gracefully: a missing binary or a timeout writes a
# one-line stub instead of raising, per HARD REQUIREMENT #5 (no podman /
# systemd / GPU on this dev sandbox — and on plenty of real installer
# targets too, e.g. a CPU-only box has no rocminfo).
_CORE_PROBES: tuple[tuple[str, tuple[str, ...], Path], ...] = (
    ("uname", ("uname", "-a"), Path("system/uname.txt")),
    ("os-release", ("cat", "/etc/os-release"), Path("system/os-release.txt")),
    ("cmdline", ("cat", "/proc/cmdline"), Path("system/cmdline.txt")),
    ("tuned", ("tuned-adm", "active"), Path("system/tuned.txt")),
    ("cpuinfo", ("cat", "/proc/cpuinfo"), Path("system/cpuinfo.txt")),
    ("meminfo", ("cat", "/proc/meminfo"), Path("system/meminfo.txt")),
    ("df", ("df", "-h"), Path("system/df.txt")),
    (
        "systemctl-hal0",
        ("systemctl", "list-units", "hal0-*", "--no-pager"),
        Path("system/systemctl-hal0.txt"),
    ),
    ("podman-images", ("podman", "images", "--format", "json"), Path("system/podman-images.txt")),
    ("network", ("ss", "-tlnp"), Path("system/network.txt")),
)
_ROCM_PROBES: tuple[tuple[str, tuple[str, ...], Path], ...] = (
    ("rocminfo", ("rocminfo",), Path("system/rocminfo.txt")),
    ("rocm-smi-showall", ("rocm-smi", "--showall"), Path("system/rocm-smi.txt")),
    ("rocm-smi-version", ("rocm-smi", "--version"), Path("system/rocm-smi-version.txt")),
)
_LOG_UNITS: tuple[str, ...] = ("hal0-api", "hal0-agent")


def _run_one(argv: tuple[str, ...], dest: Path, *, timeout: float = 10.0) -> tuple[int, int, int]:
    """Run ``argv``, write (redacted) stdout to ``dest``.

    Returns ``(exit_code, stdout_bytes, duration_ms)``. ``exit_code=-1``
    means the binary isn't on PATH; ``-2`` means it timed out. Neither
    raises — every failure mode is captured as a TSV row + a stub file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        dest.write_text(f"# command not found: {argv[0]}\n")
        return -1, 0, int((time.monotonic() - t0) * 1000)
    except subprocess.TimeoutExpired:
        dest.write_text(f"# command timed out after {timeout}s: {' '.join(argv)}\n")
        return -2, 0, int((time.monotonic() - t0) * 1000)
    except OSError as exc:  # e.g. EACCES
        dest.write_text(f"# command failed to start: {exc}\n")
        return -1, 0, int((time.monotonic() - t0) * 1000)
    body = _redact_text(proc.stdout)
    dest.write_text(body)
    return proc.returncode, len(body.encode("utf-8")), int((time.monotonic() - t0) * 1000)


def _write_commands_tsv(out: Path, rows: list[list[str]]) -> None:
    lines = [
        "\t".join(("command", "exit_code", "stdout_bytes", "stderr_bytes", "ts_utc", "duration_ms"))
    ]
    for row in rows:
        # Double-quote any field containing a tab/quote/newline (§3.1).
        safe = [f'"{f}"' if any(c in f for c in '\t"\n') else f for f in row]
        lines.append("\t".join(safe))
    (out / "commands.tsv").write_text("\n".join(lines) + "\n")


# ── config/ — every dump redacted ───────────────────────────────────────────


def _write_redacted_toml(dest: Path, data: dict[str, Any]) -> None:
    import tomli_w

    from hal0.api._redact import redact_config

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(tomli_w.dumps(redact_config(data)))


def _write_redacted_env(dest: Path, text: str) -> None:
    """Redact a systemd ``EnvironmentFile``-style ``KEY=value`` dump.

    Preserves the key set (so the operator still sees *which* vars are
    configured) while masking sensitive values — same contract as
    ``redact_config``'s ``{value, set}`` projection, adapted to the flat
    ``KEY=value`` line format api.env actually uses.
    """
    from hal0.api._redact import MASK, is_sensitive_key

    dest.parent.mkdir(parents=True, exist_ok=True)
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key, _, _val = stripped.partition("=")
        out_lines.append(f"{key}={MASK}" if is_sensitive_key(key) else line)
    dest.write_text("\n".join(out_lines) + ("\n" if out_lines else ""))


def _write_config_section(out: Path) -> list[str]:
    """Write ``config/`` (§3.1). Returns the list of redacted-file labels
    for the manifest's ``redaction_applied`` field."""
    import tomllib

    from hal0.config import paths as cfg_paths

    redacted: list[str] = []

    hal0_toml_path = cfg_paths.hal0_toml()
    if hal0_toml_path.is_file():
        try:
            data = tomllib.loads(hal0_toml_path.read_text())
        except Exception:
            data = {}
        _write_redacted_toml(out / "config" / "hal0.toml", data)
        redacted.append("config/hal0.toml")

    api_env_path = cfg_paths.etc() / "api.env"
    if api_env_path.is_file():
        _write_redacted_env(out / "config" / "api.env", api_env_path.read_text())
        redacted.append("config/api.env")

    slots_dir = cfg_paths.slots_config_dir()
    if slots_dir.is_dir():
        for slot_toml in sorted(slots_dir.glob("*.toml")):
            try:
                data = tomllib.loads(slot_toml.read_text())
            except Exception:
                continue
            _write_redacted_toml(out / "config" / "slots" / slot_toml.name, data)
            redacted.append(f"config/slots/{slot_toml.name}")

    profiles_toml_path = cfg_paths.profiles_toml()
    if profiles_toml_path.is_file():
        try:
            data = tomllib.loads(profiles_toml_path.read_text())
        except Exception:
            data = {}
        _write_redacted_toml(out / "config" / "profiles.toml", data)
        redacted.append("config/profiles.toml")

    capabilities_toml_path = cfg_paths.etc() / "capabilities.toml"
    if capabilities_toml_path.is_file():
        try:
            data = tomllib.loads(capabilities_toml_path.read_text())
        except Exception:
            data = {}
        _write_redacted_toml(out / "config" / "capabilities.toml", data)
        redacted.append("config/capabilities.toml")

    manifest_path = cfg_paths.manifest_json()
    if manifest_path.is_file():
        # No redaction — manifest.json carries no secrets (image pins/versions).
        (out / "config").mkdir(parents=True, exist_ok=True)
        (out / "config" / "manifest.json").write_text(manifest_path.read_text())

    return redacted


# ── diagnostics/ — the §21.4 Diagnosis JSON + best-effort live API pulls ──────


def _write_json(dest: Path, payload: Any) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(jsonlib.dumps(payload, indent=2, default=str))


def _api_get_or_unavailable(path: str, base: str | None) -> Any:
    from hal0.cli._shared import CliApiError, api_get

    try:
        return api_get(path, base=base)
    except CliApiError:
        return {"_unavailable": path}


def _write_diagnostics_section(out: Path, *, base: str | None = None) -> None:
    import grp
    import pwd

    import hal0
    from hal0.cli.doctor_commands import (
        _dangling_registry_entries,
        _diagnose_audit_rows,
        _diagnose_migration,
        _diagnose_models,
        _diagnose_profiles,
        _local_image_repos,
        check_hermes_ownership,
        check_profile_images_present,
        check_slot_profile_refs,
        check_tree_group_share,
        detect_editable_root,
        pending_layout_migration,
    )
    from hal0.cli.doctor_diagnosis import render_json, to_diagnosis
    from hal0.cli.doctor_verify import build_checks, gather_payloads

    # perms — pure filesystem/git reads, no API/root needed to *audit*.
    def _owner(p: Path) -> str | None:
        try:
            return pwd.getpwuid(p.stat().st_uid).pw_name
        except (OSError, KeyError):
            return None

    def _group(p: Path) -> str | None:
        try:
            return grp.getgrgid(p.stat().st_gid).gr_name
        except (OSError, KeyError):
            return None

    def _mode(p: Path) -> int:
        try:
            return p.stat().st_mode
        except OSError:
            return 0

    def _git_shared(p: Path) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(p), "config", "--get", "core.sharedRepository"],
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None

    def _exists_quiet(p: Path) -> bool:
        # Path.exists() propagates EACCES (only ENOENT/ENOTDIR-class errnos
        # are swallowed) — stat'ing under mode-700 /root as a non-root user
        # raises instead of returning False. An unreadable path can't be
        # audited from here; degrade to absent like every other probe.
        try:
            return p.exists()
        except OSError:
            return False

    hermes_rows = check_hermes_ownership(owner_of=_owner, exists=_exists_quiet)
    root = detect_editable_root(Path(hal0.__file__).resolve())
    tree_rows = check_tree_group_share(
        root, group_of=_group, mode_of=_mode, git_shared_of=_git_shared
    )
    perms_diagnoses = _diagnose_audit_rows(
        hermes_rows, diagnosis_id="HAL0-PERMS-HERMES-DRIFT", ok_summary="Hermes ownership clean"
    ) + _diagnose_audit_rows(
        tree_rows,
        diagnosis_id="HAL0-PERMS-TREE-NOT-SHARED",
        ok_summary="editable checkout group-share clean",
    )
    _write_json(out / "diagnostics" / "perms.json", jsonlib.loads(render_json(perms_diagnoses)))

    # migrations — local planner, no API.
    migration_diagnoses = _diagnose_migration(pending_layout_migration())
    _write_json(
        out / "diagnostics" / "migrations.json", jsonlib.loads(render_json(migration_diagnoses))
    )

    # profiles — local catalog + slot TOMLs, no API.
    try:
        from hal0.config.loader import list_slots, load_slot_config
        from hal0.profiles import ProfileCatalog

        catalog = ProfileCatalog()
        profiles = catalog.list()
        valid_names = {p.name for p in profiles}
        slot_profiles: list[tuple[str, str | None]] = []
        slot_types: dict[str, str] = {}
        slot_devices: dict[str, str] = {}
        # id-aware (P3-runtime-db inc4): report cfg.name (the real display
        # name), not the raw list_slots() stem — see the identical fix +
        # rationale in doctor_commands.doctor_profiles.
        for slot_name in list_slots():
            try:
                cfg = load_slot_config(slot_name)
            except Exception:
                continue
            slot_profiles.append((cfg.name, cfg.profile))
            # Type + device feed the profile-less capability check (#1830):
            # the type decides whether it is drift, the device which profile
            # repairs it (npu embeddings want flm, not llama-server).
            slot_types[cfg.name] = str(getattr(cfg, "type", "") or "")
            slot_devices[cfg.name] = str(getattr(cfg, "device", "") or "")
        ref_rows = check_slot_profile_refs(slot_profiles, valid_names, slot_types, slot_devices)
        img_rows = check_profile_images_present(profiles, _local_image_repos())
        profile_diagnoses = _diagnose_profiles(ref_rows, img_rows)
    except Exception:
        profile_diagnoses = [
            {
                "id": "HAL0-DOCTOR-SKIPPED",
                "severity": "info",
                "confidence": "high",
                "summary": "profile layer unavailable",
                "detail": "",
                "fixable": False,
                "evidence": [],
                "next_steps": [],
            }
        ]
        _write_json(out / "diagnostics" / "profiles.json", profile_diagnoses)
    else:
        _write_json(
            out / "diagnostics" / "profiles.json", jsonlib.loads(render_json(profile_diagnoses))
        )

    # verify — live API report card (best-effort; API-down degrades every row).
    payloads = gather_payloads(base)
    checks = build_checks(
        health=payloads["health"],
        urls=payloads["urls"],
        system=payloads["system"],
        capabilities=payloads["capabilities"],
        memory=payloads["memory"],
        services=payloads["services"],
    )
    verify_diagnoses = [to_diagnosis(c) for c in checks]
    _write_json(
        out / "diagnostics" / "verify.json",
        {"diagnoses": jsonlib.loads(render_json(verify_diagnoses))},
    )

    # models — needs a live /api/models to know what's registered (§3.1 —
    # best-effort; API down writes {"_unavailable": ...} rather than failing
    # the whole bundle, per §7 risk #8).
    models_payload = _api_get_or_unavailable("/api/models", base)
    if isinstance(models_payload, dict) and "_unavailable" in models_payload:
        _write_json(out / "diagnostics" / "models.json", models_payload)
    else:
        rows = (
            models_payload.get("models", models_payload)
            if isinstance(models_payload, dict)
            else models_payload
        )
        local = [m for m in rows if isinstance(m, dict) and m.get("path")]
        model_diagnoses = _diagnose_models(
            dangling=_dangling_registry_entries(local),
            unregistered=[],
            store_missing=False,
            effective="",
            divergence=None,
            mount_warn=None,
            flm_dir=Path("."),
            writ=None,
        )
        _write_json(
            out / "diagnostics" / "models.json", jsonlib.loads(render_json(model_diagnoses))
        )

    # §21.3 introspection surfaces — thin best-effort pulls.
    _write_json(out / "diagnostics" / "slots.json", _api_get_or_unavailable("/api/slots", base))
    _write_json(
        out / "diagnostics" / "hardware.json", _api_get_or_unavailable("/api/hardware", base)
    )
    _write_json(
        out / "diagnostics" / "system-info.json",
        _api_get_or_unavailable("/api/system-info", base),
    )
    _write_json(out / "diagnostics" / "stats.json", _api_get_or_unavailable("/api/stats", base))


# ── logs/ — best-effort journalctl captures ─────────────────────────────────


def _write_logs_section(out: Path, *, lines: int = 500) -> list[str]:
    """Capture ``journalctl -u <unit> -n <lines> --no-pager`` per unit.

    Returns the labels captured (for the manifest). A systemd-less box
    (this dev sandbox included) writes a "journalctl not found" stub per
    unit rather than raising — §21.4 HARD REQUIREMENT #5.
    """
    captured: list[str] = []
    journalctl = ("journalctl", "-u", "{unit}", "-n", str(lines), "--no-pager")
    for unit in _LOG_UNITS:
        argv = tuple(a.format(unit=unit) for a in journalctl)
        dest = out / "logs" / f"{unit}.log"
        _run_one(argv, dest)
        captured.append(f"logs/{unit}.log")
    return captured


# ── orchestration ────────────────────────────────────────────────────────────


def build_bundle(
    out: Path,
    *,
    include_rocm_smi: bool = True,
    include_logs: bool = True,
    base: str | None = None,
) -> tuple[Path, int]:
    """Write the full bundle under ``out``. Returns ``(out, failed_probe_count)``.

    ``failed_probe_count`` counts probe commands with a nonzero/negative
    exit code (missing binary, timeout, real failure) — non-fatal; the
    caller uses it only to pick exit 0 vs 1 (§4.3).
    """
    out.mkdir(parents=True, exist_ok=True)

    probes = list(_CORE_PROBES)
    if include_rocm_smi:
        probes += list(_ROCM_PROBES)

    tsv_rows: list[list[str]] = []
    failed = 0
    for label, argv, rel in probes:
        rc, nbytes, dur = _run_one(argv, out / rel)
        if rc != 0:
            failed += 1
        tsv_rows.append([label, str(rc), str(nbytes), "0", _now_iso(), str(dur)])
    _write_commands_tsv(out, tsv_rows)

    redacted = _write_config_section(out)
    _write_diagnostics_section(out, base=base)

    sections = ["manifest.json", "commands.tsv", "system/", "config/", "diagnostics/"]
    if include_logs:
        _write_logs_section(out)
        sections.append("logs/")

    # doctor-summary.txt — the rich `doctor verify` report, as plain text.
    from hal0.cli.doctor_verify import run_verify

    summary_console = Console(
        file=(out / "doctor-summary.txt").open("w"), force_terminal=False, width=100
    )
    try:
        run_verify(console=summary_console, base=base)
    finally:
        summary_console.file.close()

    from hal0 import __version__
    from hal0.config import paths as cfg_paths

    manifest = {
        "schema_version": 1,
        "generated_at_utc": _now_iso(),
        "hal0_version": __version__,
        "hostname": socket.gethostname(),
        "installer_root": str(cfg_paths.usr_lib()),
        "hal0_home": str(cfg_paths.var_lib().parent),
        "sections": sections,
        "redaction_applied": redacted,
        "redaction_policy": _REDACTION_POLICY,
        "command_count": len(tsv_rows),
        "failed_probe_count": failed,
    }
    _write_json(out / "manifest.json", manifest)
    return out, failed


def _default_out() -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path.cwd() / f"hal0-doctor-bundle-{socket.gethostname()}-{ts}"


def doctor_bundle_cmd(
    out: Path | None = typer.Option(
        None, "--out", help="Bundle output directory (default: ./hal0-doctor-bundle-<host>-<ts>/)."
    ),
    no_rocm_smi: bool = typer.Option(
        False,
        "--no-rocm-smi",
        help="Skip the rocm-smi/rocminfo captures (faster on non-AMD boxes).",
    ),
    include_logs: str = typer.Option(
        "auto",
        "--include-logs",
        help="auto|yes|no — journalctl captures (auto == yes; kept for the future systemd-detect case).",
    ),
) -> None:
    """Write a support bundle: system/config/diagnostics/logs evidence in one dir.

    Read-only — never mutates the box. Every config dump is redacted
    (reuses ``api._redact.redact_config`` — SECRET/TOKEN/PASSWORD/API_KEY/
    PRIVATE_KEY/ENCRYPTION_KEY/SALT-named keys). No upload — `tar czf` and
    ship it yourself. Exit codes: 0 clean, 1 some probe commands failed
    (non-fatal, see commands.tsv), 2 could not write the bundle at all.
    """
    target = out or _default_out()
    logs_wanted = include_logs.strip().lower() != "no"
    try:
        written, failed = build_bundle(
            target, include_rocm_smi=not no_rocm_smi, include_logs=logs_wanted
        )
    except OSError as exc:
        console.print(f"[red]✗[/red]  could not write bundle to {target}: {exc}")
        raise typer.Exit(2) from exc

    console.print(f"[green]✓[/green]  bundle written to {written}")
    if failed:
        console.print(
            f"[yellow]![/yellow]  {failed} probe command(s) failed/unavailable — see commands.tsv"
        )
        raise typer.Exit(1)
    raise typer.Exit(0)


__all__ = ["build_bundle", "doctor_bundle_cmd"]
