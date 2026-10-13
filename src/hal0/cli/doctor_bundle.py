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
    ├── diagnostics/       # doctor {perms,profiles,verify}.json +
    │                      # best-effort GET /api/{models,slots,hardware,
    │                      # system-info,stats}
    ├── logs/              # journalctl captures (best-effort; absent on a
    │                      # systemd-less box like this dev sandbox), plus the
    │                      # latest install log and installer failure report
    └── doctor-summary.txt # the rich `doctor verify` report, as plain text

No upload — the bundle is written to local disk only (§3.3). No
``hal0 doctor fix <id>`` dispatcher here either — ``next_steps[]`` in the
diagnostics only reference the future command by name.
"""

from __future__ import annotations

import contextlib
import errno
import json as jsonlib
import os
import re
import socket
import stat
import subprocess
import tempfile
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

console = Console()

_BEARER_RE = re.compile(r"Bearer\s+\S+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

_REDACTION_POLICY = (
    "SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|"
    "ENCRYPTION[_-]?KEY|SALT|_KEY$|^KEY$ "
    "(hal0.api._redact._SENSITIVE_RE)"
)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact_text(text: str) -> str:
    """Redact free-text command output before it is written into the bundle.

    Every free-text capture goes through here: the system/ probes, the
    journal captures under logs/ and the install-log / failure-report
    copies. The bundle is what users attach to public bug reports, so this
    is the full shareable-text pass (#2434), not just a Bearer/JWT scrub:

    1. :func:`hal0.redaction.redact_audit_row_secret_args` masks the
       secret args of each ``mcp.tool.invoked`` audit row, which rows logged
       before the write-time masking still carry in clear (#2434). It runs
       first, on the text as rendered, so the row is still parseable;
    2. :func:`hal0.redaction.redact_shareable_text` masks every plausible
       secret value seen as ``NAME=value`` / ``NAME: value`` wherever it
       reappears, then the secret shapes on each line (#2409);
    3. :func:`hal0.redaction.redact_log_line` runs on each line, as
       ``/api/logs`` and MCP ``logs_tail`` do on read (#2403);
    4. the Bearer / JWT scrub, kept as a last pass.

    Complements (does not replace) ``api._redact.redact_config``, which
    scrubs by KEY NAME in structured config trees. Line breaks, including a
    trailing newline, are preserved.
    """
    from hal0.redaction import (
        redact_audit_row_secret_args,
        redact_log_line,
        redact_shareable_text,
    )

    audited = "\n".join(redact_audit_row_secret_args(line) for line in text.split("\n"))
    shared = redact_shareable_text(audited)
    body = "\n".join(redact_log_line(line) for line in shared.split("\n"))
    return _BEARER_RE.sub("Bearer ***REDACTED***", _JWT_RE.sub("***JWT***", body))


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


# ── safe bundle writes — every file the bundle writes goes through here ──────
#
# The bundle is often written by root, and its directory used to be created
# with ``mkdir(exist_ok=True)`` under a guessable name in the cwd: from /tmp,
# a local user could pre-create it and plant ``commands.tsv`` as a symlink to
# any file root would then overwrite, and read the bundle besides (#2528).
# The output directory is now created fresh (0700) or must be an empty,
# euid-owned directory, and every file is created ``O_CREAT|O_EXCL|O_NOFOLLOW``
# relative to that directory's fd, each subdirectory opened ``O_NOFOLLOW``.

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_DIR_OPEN = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NOCTTY | os.O_CLOEXEC

#: The bundle being written: its root path and an fd for that directory.
_ACTIVE_OUT: ContextVar[tuple[Path, int] | None] = ContextVar("_ACTIVE_OUT", default=None)


class BundleOutputError(OSError):
    """The output directory is unsafe to write a bundle into."""


def _open_subdir(dir_fd: int, name: str) -> int:
    """Open (creating 0700 if absent) ``name`` under ``dir_fd``, never via a symlink."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(name, _DIR_MODE, dir_fd=dir_fd)
    return os.open(name, _DIR_OPEN, dir_fd=dir_fd)


def _create_file(dest: Path) -> int:
    """Create ``dest`` exclusively (0600) and return a writable fd.

    Inside the active bundle, every component below the bundle root is walked
    from the root's fd with ``O_NOFOLLOW``, so a planted symlink anywhere in
    the path fails (``ELOOP``/``EEXIST``) instead of being written through.
    Outside one (a helper called on its own, as the unit tests do), only the
    final directory and the file are pinned.
    """
    active = _ACTIVE_OUT.get()
    if active is not None:
        root, root_fd = active
        if not dest.is_relative_to(root):
            # Every bundle write is under ``out``; anything else is a bug,
            # and must not quietly fall through to the weaker path below.
            raise BundleOutputError(errno.EINVAL, f"outside the bundle at {root}", str(dest))
        parts = dest.relative_to(root).parts
        base = os.dup(root_fd)
    else:
        # Fallback, reached only with no bundle active: a write helper called
        # on its own (the unit tests do this). build_bundle always sets
        # _ACTIVE_OUT, so a real bundle never takes this path. Only the final
        # directory and the file are pinned here, not the path above them.
        dest.parent.mkdir(parents=True, exist_ok=True)
        parts = (dest.name,)
        base = os.open(dest.parent, _DIR_OPEN)
    try:
        for name in parts[:-1]:
            nxt = _open_subdir(base, name)
            os.close(base)
            base = nxt
        return os.open(parts[-1], _FILE_CREATE, _FILE_MODE, dir_fd=base)
    finally:
        os.close(base)


def _write_file(dest: Path, text: str) -> None:
    with os.fdopen(_create_file(dest), "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_default_out(parent: Path) -> Path:
    """A fresh 0700 bundle directory under ``parent`` with an unguessable name."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"hal0-doctor-bundle-{socket.gethostname()}-{ts}-"
    return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))


def _claim_out(out: Path) -> int:
    """Return an fd for ``out``, creating it fresh or vetting an existing one.

    A missing ``out`` is created with ``os.mkdir(0700)``. An existing one is
    accepted only if it is a real (not symlinked) directory owned by the euid,
    not group- or world-writable, and empty; it is then tightened to 0700.
    The fd is ``fstat``-checked against the ``lstat`` so a swap is refused.
    """
    try:
        os.mkdir(out, _DIR_MODE)
    except FileExistsError:
        pass
    except FileNotFoundError:
        out.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(out, _DIR_MODE)
    st = os.lstat(out)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise BundleOutputError(errno.ENOTDIR, "not a real directory", str(out))
    if st.st_uid != os.geteuid():
        raise BundleOutputError(errno.EPERM, f"owned by uid {st.st_uid}, not us", str(out))
    if st.st_mode & 0o022:
        raise BundleOutputError(errno.EPERM, "group- or world-writable", str(out))
    fd = os.open(out, _DIR_OPEN)
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
            raise BundleOutputError(errno.EAGAIN, "replaced while being opened", str(out))
        if os.listdir(fd):
            raise BundleOutputError(errno.ENOTEMPTY, "not empty", str(out))
        os.fchmod(fd, _DIR_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _run_one(argv: tuple[str, ...], dest: Path, *, timeout: float = 10.0) -> tuple[int, int, int]:
    """Run ``argv``, write (redacted) stdout to ``dest``.

    Returns ``(exit_code, stdout_bytes, duration_ms)``. ``exit_code=-1``
    means the binary isn't on PATH; ``-2`` means it timed out. Neither
    raises — every failure mode is captured as a TSV row + a stub file.
    """
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        _write_file(dest, f"# command not found: {argv[0]}\n")
        return -1, 0, int((time.monotonic() - t0) * 1000)
    except subprocess.TimeoutExpired:
        _write_file(dest, f"# command timed out after {timeout}s: {' '.join(argv)}\n")
        return -2, 0, int((time.monotonic() - t0) * 1000)
    except OSError as exc:  # e.g. EACCES
        _write_file(dest, f"# command failed to start: {exc}\n")
        return -1, 0, int((time.monotonic() - t0) * 1000)
    body = _redact_text(proc.stdout)
    _write_file(dest, body)
    return proc.returncode, len(body.encode("utf-8")), int((time.monotonic() - t0) * 1000)


def _write_commands_tsv(out: Path, rows: list[list[str]]) -> None:
    lines = [
        "\t".join(("command", "exit_code", "stdout_bytes", "stderr_bytes", "ts_utc", "duration_ms"))
    ]
    for row in rows:
        # Double-quote any field containing a tab/quote/newline (§3.1).
        safe = [f'"{f}"' if any(c in f for c in '\t"\n') else f for f in row]
        lines.append("\t".join(safe))
    _write_file(out / "commands.tsv", "\n".join(lines) + "\n")


# ── safe file reads — every file the bundle copies goes through here ─────────
#
# ``hal0 doctor bundle`` is run as root, and some of what it copies lives in
# directories a less-privileged account can write: /tmp (anyone), and
# /var/log/hal0 and /etc/hal0 (the hal0 service user under the ownership
# flip, install/perms.py). A planted symlink or hard link there would put a
# root-only file such as /etc/shadow into a bundle made to be shared (#2522).
# :func:`_safe_read_text` is the one way the bundle reads such a file.

#: Files a safe-read check refused to copy, as ``{"path", "reason"}`` rows
#: for the manifest's ``skipped_files``.
SkippedFiles = list[dict[str, str]]


def _note_skip(skipped: SkippedFiles | None, path: Path, reason: str) -> None:
    if skipped is not None:
        skipped.append({"path": str(path), "reason": reason})


def _trusted_uids() -> frozenset[int]:
    """Owners a copied file may have: root, or whoever runs the bundle."""
    return frozenset({0, os.geteuid()})


def _reject_reason(st: os.stat_result, allowed_uids: frozenset[int]) -> str | None:
    """Why an ``lstat``/``fstat`` result is not safe to copy, or ``None``."""
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if st.st_uid not in allowed_uids:
        return f"owned by uid {st.st_uid}"
    if st.st_nlink != 1:
        # A hard link to a root-only file keeps that file's (root) owner.
        return "hard-linked"
    return None


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino, a.st_uid) == (b.st_dev, b.st_ino, b.st_uid)


def _safe_read_text(
    path: Path,
    *,
    expected: os.stat_result | None = None,
    allow_dir_owner: bool = False,
    skipped: SkippedFiles | None = None,
) -> str | None:
    """Read ``path`` without following a symlink a non-root writer could plant.

    The parent directory is opened ``O_NOFOLLOW`` and the file is looked up
    relative to it, so neither that directory nor the file can be a symlink.
    The file must be a regular, singly linked file owned by root or the
    running euid, or by the parent directory's owner when ``allow_dir_owner``
    (/etc/hal0 is the service user's under the ownership flip, and that
    account can only hand over content it could write anyway). It is opened
    ``O_NOFOLLOW | O_NONBLOCK`` and ``fstat``-rechecked against the ``lstat``,
    and against ``expected`` (the stat a caller selected the file by), before
    a byte is read, so a swap in between is rejected.

    Returns ``None`` for a missing, unreadable or rejected file and never
    raises ``OSError``; a rejection is recorded in ``skipped``.
    """
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            _note_skip(skipped, path, "parent directory is a symlink")
        return None
    try:
        allowed = _trusted_uids()
        if allow_dir_owner:
            allowed |= {os.fstat(dir_fd).st_uid}
        try:
            st = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            return None
        reason = _reject_reason(st, allowed)
        if reason is None and expected is not None and not _same_file(st, expected):
            reason = "replaced after it was selected"
        if reason is not None:
            _note_skip(skipped, path, reason)
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
        try:
            fd = os.open(path.name, flags, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                _note_skip(skipped, path, "replaced by a symlink before it was opened")
            return None
        with os.fdopen(fd, "rb") as fh:
            fst = os.fstat(fh.fileno())
            if _reject_reason(fst, allowed) is not None or not _same_file(fst, st):
                _note_skip(skipped, path, "replaced before it was opened")
                return None
            try:
                data = fh.read()
            except OSError:
                return None
    finally:
        os.close(dir_fd)
    return data.decode("utf-8", errors="replace")


# ── config/ — every dump redacted ───────────────────────────────────────────


def _write_redacted_toml(dest: Path, data: dict[str, Any]) -> None:
    import tomli_w

    from hal0.api._redact import redact_config

    _write_file(dest, tomli_w.dumps(redact_config(data)))


def _write_redacted_env(dest: Path, text: str) -> None:
    """Redact a systemd ``EnvironmentFile``-style ``KEY=value`` dump.

    Preserves the key set (so the operator still sees *which* vars are
    configured) while masking sensitive values — same contract as
    ``redact_config``'s ``{value, set}`` projection, adapted to the flat
    ``KEY=value`` line format api.env actually uses.
    """
    from hal0.api._redact import MASK, is_sensitive_key

    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key, _, _val = stripped.partition("=")
        out_lines.append(f"{key}={MASK}" if is_sensitive_key(key) else line)
    _write_file(dest, "\n".join(out_lines) + ("\n" if out_lines else ""))


def _write_config_section(out: Path, *, skipped: SkippedFiles | None = None) -> list[str]:
    """Write ``config/`` (§3.1). Returns the list of redacted-file labels
    for the manifest's ``redaction_applied`` field.

    /etc/hal0 is the service user's under the ownership flip, so every file
    here is read with :func:`_safe_read_text` (#2522): a planted symlink or
    hard link is skipped, not followed.
    """
    import tomllib

    from hal0.config import paths as cfg_paths

    redacted: list[str] = []

    def _read(path: Path) -> str | None:
        return _safe_read_text(path, allow_dir_owner=True, skipped=skipped)

    def _toml(text: str) -> dict[str, Any]:
        try:
            return tomllib.loads(text)
        except Exception:
            return {}

    hal0_toml_text = _read(cfg_paths.hal0_toml())
    if hal0_toml_text is not None:
        _write_redacted_toml(out / "config" / "hal0.toml", _toml(hal0_toml_text))
        redacted.append("config/hal0.toml")

    api_env_text = _read(cfg_paths.etc() / "api.env")
    if api_env_text is not None:
        _write_redacted_env(out / "config" / "api.env", api_env_text)
        redacted.append("config/api.env")

    slots_dir = cfg_paths.slots_config_dir()
    if slots_dir.is_dir():
        for slot_toml in sorted(slots_dir.glob("*.toml")):
            slot_text = _read(slot_toml)
            if slot_text is None:
                continue
            try:
                data = tomllib.loads(slot_text)
            except Exception:
                continue
            _write_redacted_toml(out / "config" / "slots" / slot_toml.name, data)
            redacted.append(f"config/slots/{slot_toml.name}")

    profiles_text = _read(cfg_paths.profiles_toml())
    if profiles_text is not None:
        _write_redacted_toml(out / "config" / "profiles.toml", _toml(profiles_text))
        redacted.append("config/profiles.toml")

    capabilities_text = _read(cfg_paths.etc() / "capabilities.toml")
    if capabilities_text is not None:
        _write_redacted_toml(out / "config" / "capabilities.toml", _toml(capabilities_text))
        redacted.append("config/capabilities.toml")

    manifest_text = _read(cfg_paths.manifest_json())
    if manifest_text is not None:
        # No redaction — manifest.json carries no secrets (image pins/versions).
        _write_file(out / "config" / "manifest.json", manifest_text)

    return redacted


# ── diagnostics/ — the §21.4 Diagnosis JSON + best-effort live API pulls ──────


def _write_json(dest: Path, payload: Any) -> None:
    _write_file(dest, jsonlib.dumps(payload, indent=2, default=str))


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
        _diagnose_models,
        _diagnose_profiles,
        _local_image_repos,
        check_hermes_ownership,
        check_profile_images_present,
        check_slot_profile_refs,
        check_tree_group_share,
        detect_editable_root,
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


def _write_logs_section(
    out: Path, *, lines: int = 500, skipped: SkippedFiles | None = None
) -> list[str]:
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
    captured += _write_install_log(out, skipped=skipped)
    captured += _write_install_report(out, skipped=skipped)
    return captured


#: Where installer/lib/logging.sh's hal0_install_log_path() puts the tee'd
#: install log — root gets /var/log/hal0, a --dev / non-root install falls
#: back to /tmp. Both are globbed here so the bundle picks up whichever one
#: this box actually has, newest first.
_INSTALL_LOG_GLOBS: tuple[tuple[str, str], ...] = (
    ("/var/log/hal0", "install-*.log"),
    ("/tmp", "hal0-install-*.log"),
)


#: Where installer/lib/failure-report.sh writes hal0-install-report-<ts>.txt
#: on an aborted install — next to the install log, so the same two dirs.
_INSTALL_REPORT_GLOBS: tuple[tuple[str, str], ...] = (
    ("/var/log/hal0", "hal0-install-report-*.txt"),
    ("/tmp", "hal0-install-report-*.txt"),
)


def _latest_candidate(
    globs: tuple[tuple[str, str], ...], *, skipped: SkippedFiles | None = None
) -> tuple[Path, os.stat_result] | None:
    """The newest safe match for ``globs``, with the ``lstat`` it was chosen by.

    Both directories are writable by someone other than root (#2522), so a
    candidate is ``lstat``-ed, never followed: a symlink, anything that is
    not a regular file, a hard link, or a file owned by anyone but root or
    the running euid is skipped. A candidate can also vanish between glob()
    and lstat() (e.g. /tmp cleanup): skip it rather than abort the bundle.
    """
    allowed = _trusted_uids()
    best: tuple[Path, os.stat_result] | None = None
    for directory, pattern in globs:
        d = Path(directory)
        if d.is_dir():
            for p in d.glob(pattern):
                try:
                    st = p.lstat()
                except OSError:
                    continue
                reason = _reject_reason(st, allowed)
                if reason is not None:
                    _note_skip(skipped, p, reason)
                    continue
                if best is None or st.st_mtime > best[1].st_mtime:
                    best = (p, st)
    return best


def _latest_match(globs: tuple[tuple[str, str], ...]) -> Path | None:
    found = _latest_candidate(globs)
    return found[0] if found else None


def _latest_install_log() -> Path | None:
    return _latest_match(_INSTALL_LOG_GLOBS)


def _read_latest(
    globs: tuple[tuple[str, str], ...], *, skipped: SkippedFiles | None = None
) -> str | None:
    """Select the newest safe match and read it with :func:`_safe_read_text`,
    which rejects it if it is no longer the file that was selected."""
    found = _latest_candidate(globs, skipped=skipped)
    if found is None:
        return None
    path, st = found
    return _safe_read_text(path, expected=st, skipped=skipped)


def _install_artifacts() -> dict[str, dict[str, str] | None]:
    """The manifest's ``install_artifacts`` surface (#2307): the newest
    install log and installer failure report on this box, or ``None`` for
    each that is absent. A ``HAL0-INSTALL-*`` diagnosis keys off this."""

    def _describe(found: tuple[Path, os.stat_result] | None) -> dict[str, str] | None:
        if found is None:
            return None
        path, st = found
        mtime = datetime.fromtimestamp(st.st_mtime, UTC)
        return {"path": str(path), "mtime_utc": mtime.strftime("%Y-%m-%dT%H:%M:%SZ")}

    return {
        "install_log": _describe(_latest_candidate(_INSTALL_LOG_GLOBS)),
        "failure_report": _describe(_latest_candidate(_INSTALL_REPORT_GLOBS)),
    }


def _redact_install_text(text: str) -> str:
    """Redact an installer log or failure report copied into the bundle.

    The same pass as every other free-text capture (:func:`_redact_text`);
    kept as its own name for the install-copy call sites and their tests.
    Trailing line breaks are dropped, as before.
    """
    return _redact_text("\n".join(text.splitlines()))


def _write_install_report(out: Path, *, skipped: SkippedFiles | None = None) -> list[str]:
    """Copy the newest installer failure report into the bundle.

    The installer already redacts it (installer/lib/failure-report.sh); it
    is scrubbed again here with :func:`_redact_install_text` so a report
    from an older installer cannot carry a ``NAME=value`` secret in.
    The report is 0600 root-owned: an unreadable one is skipped, not fatal,
    and so is one that fails the :func:`_safe_read_text` checks (#2522).
    """
    text = _read_latest(_INSTALL_REPORT_GLOBS, skipped=skipped)
    if text is None:
        return []
    _write_file(out / "logs" / "install-failure-report.txt", _redact_install_text(text) + "\n")
    return ["logs/install-failure-report.txt"]


def _write_install_log(
    out: Path, *, lines: int = 500, skipped: SkippedFiles | None = None
) -> list[str]:
    """Copy the tail of the most recent installer log (installer/lib/logging.sh)
    into the bundle, redacted like the failure report (§3.1, #2360).

    Best-effort: no install log on this box (a long-lived install predating
    this feature, or a box whose /tmp was cleaned) writes nothing rather
    than failing the bundle. A candidate that fails the
    :func:`_safe_read_text` checks (#2522) is skipped the same way.
    """
    text = _read_latest(_INSTALL_LOG_GLOBS, skipped=skipped)
    if text is None:
        return []
    tail = text.splitlines()[-lines:]
    _write_file(out / "logs" / "install.log", _redact_install_text("\n".join(tail)) + "\n")
    return ["logs/install.log"]


# ── orchestration ────────────────────────────────────────────────────────────


def build_bundle(
    out: Path | None,
    *,
    include_rocm_smi: bool = True,
    include_logs: bool = True,
    base: str | None = None,
) -> tuple[Path, int]:
    """Write the full bundle under ``out``. Returns ``(out, failed_probe_count)``.

    ``out=None`` creates a fresh, uniquely named 0700 directory in the cwd;
    the returned path is the one actually written. An existing ``out`` must
    be an empty directory the euid owns (#2528), else
    :class:`BundleOutputError` is raised before anything is written.

    ``failed_probe_count`` counts probe commands with a nonzero/negative
    exit code (missing binary, timeout, real failure) — non-fatal; the
    caller uses it only to pick exit 0 vs 1 (§4.3).
    """
    if out is None:
        out = _make_default_out(Path.cwd())
    out_fd = _claim_out(out)
    token = _ACTIVE_OUT.set((out, out_fd))
    try:
        return out, _build_into(
            out, include_rocm_smi=include_rocm_smi, include_logs=include_logs, base=base
        )
    finally:
        _ACTIVE_OUT.reset(token)
        os.close(out_fd)


def _build_into(out: Path, *, include_rocm_smi: bool, include_logs: bool, base: str | None) -> int:
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

    # Files a safe-read check refused to copy (#2522), for the manifest.
    skipped: SkippedFiles = []
    redacted = _write_config_section(out, skipped=skipped)
    _write_diagnostics_section(out, base=base)

    sections = ["manifest.json", "commands.tsv", "system/", "config/", "diagnostics/"]
    if include_logs:
        _write_logs_section(out, skipped=skipped)
        sections.append("logs/")

    # doctor-summary.txt — the rich `doctor verify` report, as plain text.
    from hal0.cli.doctor_verify import run_verify

    summary_console = Console(
        file=os.fdopen(_create_file(out / "doctor-summary.txt"), "w", encoding="utf-8"),
        force_terminal=False,
        width=100,
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
        "install_artifacts": _install_artifacts(),
        "skipped_files": skipped,
    }
    _write_json(out / "manifest.json", manifest)
    return failed


def doctor_bundle_cmd(
    out: Path | None = typer.Option(
        None,
        "--out",
        help=(
            "Bundle output directory: created 0700, or an existing empty directory you own "
            "(default: a new ./hal0-doctor-bundle-<host>-<ts>-<random>/)."
        ),
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
    logs_wanted = include_logs.strip().lower() != "no"
    try:
        written, failed = build_bundle(
            out, include_rocm_smi=not no_rocm_smi, include_logs=logs_wanted
        )
    except OSError as exc:
        where = out if out is not None else Path.cwd()
        console.print(f"[red]✗[/red]  could not write bundle to {where}: {exc}")
        raise typer.Exit(2) from exc

    console.print(f"[green]✓[/green]  bundle written to {written}")
    if failed:
        console.print(
            f"[yellow]![/yellow]  {failed} probe command(s) failed/unavailable — see commands.tsv"
        )
        raise typer.Exit(1)
    raise typer.Exit(0)


__all__ = ["build_bundle", "doctor_bundle_cmd"]
