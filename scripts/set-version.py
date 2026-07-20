#!/usr/bin/env python3
"""Atomic version synchronisation across hal0 release artifacts.

Updates all top-level version fields (pyproject.toml, ui/package.json,
ui/package-lock.json, manifest.json) and the PEP 440 version of the
editable ``hal0ai`` package in uv.lock in a single atomic transaction:
every candidate is written to a temporary file first, all candidates are
validated, and only then are originals replaced via ``os.replace``.

After a successful replacement ``uv lock`` is re-run and the resulting
lock version is re-validated.

Usage::

    python scripts/set-version.py VERSION
    python scripts/set-version.py --check VERSION   # dry-run — no I/O
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _resolve_pyproject_version(root: Path) -> str:
    """Parse the current version from pyproject.toml."""
    import tomllib

    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    try:
        return data["project"]["version"]
    except KeyError:
        msg = "pyproject.toml is missing [project].version"
        raise ValueError(msg) from None


def _version_to_pep440(version: str) -> str:
    """Convert SemVer pre-release to PEP 440 format.

    Examples:
        1.0.0-alpha.2   → 1.0.0a2
        1.0.0-beta.1    → 1.0.0b1
        1.0.0-rc.3      → 1.0.0rc3
        1.0.0           → 1.0.0
    """
    match = re.match(
        r"^(\d+\.\d+\.\d+)-(alpha|beta|rc)\.(\d+)$", version
    )
    if match:
        base = match.group(1)
        stage = match.group(2)
        seq = match.group(3)
        marker = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
        return f"{base}{marker}{seq}"
    return version  # stable — same format


def _resolve_channel(version: str) -> str:
    """Derive the manifest channel from a release version via ReleasePolicy.

    Delegates to ``hal0.release.policy.ReleasePolicy.from_tag()``.
    Nightly versions are rejected — nightly does not rewrite source
    versions.
    """
    # Inline minimal version of the policy check to avoid importing hal0
    # at the top level.  We only need kind from the policy.
    tag = f"v{version}"
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from hal0.release.policy import ReleasePolicy  # fmt: skip

    policy = ReleasePolicy.from_tag(tag)
    if policy.kind == "nightly":
        raise ValueError(
            f"nightly version {version!r} is not eligible for source-version "
            "rewriting — use a stable or preview tag"
        )
    return policy.kind  # "stable" or "preview"


def _read_json(path: Path) -> dict[str, object]:
    """Read and return a JSON file; raises ValueError on duplicate keys."""
    raw = path.read_text(encoding="utf-8")
    try:
        # Reject duplicate keys via object_pairs_hook
        data: dict[str, object] = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from None
    return data


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """object_pairs_hook that rejects duplicate keys."""
    seen: set[str] = set()
    result: dict[str, object] = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen.add(k)
        result[k] = v
    return result


def _write_json(path: Path, data: dict[str, object]) -> None:
    """Write *data* as pretty-printed JSON to *path*."""
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _update_uv_lock_version(lock_text: str, new_version_pep440: str) -> str:
    """Replace the version of the ``hal0ai`` editable package in uv.lock."""
    import tomllib

    lock_data = tomllib.loads(lock_text)

    # Verify hal0ai exists
    found = False
    for pkg in lock_data.get("package", []):
        if pkg.get("name") == "hal0ai":
            found = True
            break
    if not found:
        raise ValueError("uv.lock: no [[package]] with name = 'hal0ai' found")

    # Line-based replacement to preserve formatting
    lines = lock_text.splitlines()
    result: list[str] = []
    found_name = False
    for line in lines:
        if line.rstrip() == 'name = "hal0ai"':
            found_name = True
            result.append(line)
        elif found_name and line.strip().startswith("version "):
            indent = line[: len(line) - len(line.lstrip())]
            result.append(f'{indent}version = "{new_version_pep440}"')
            found_name = False
        else:
            result.append(line)
    return "\n".join(result)


def _update_file_atomic(
    dst: Path, content: str, tmpdir: Path
) -> Path:
    """Write *content* to a tempfile in *tmpdir*, return the temp path."""
    fd, tmp_path = tempfile.mkstemp(
        dir=tmpdir,
        prefix=f".{dst.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    Path(tmp_path).write_text(content, encoding="utf-8")
    return Path(tmp_path)


def set_version(root: Path, version: str) -> None:
    """Atomically update all version fields to *version*.

    Args:
        root: Repository root directory.
        version: SemVer version string (e.g. ``1.0.0-alpha.2``).

    Raises:
        ValueError: If the version is nightly or a required file is
            malformed.
        RuntimeError: If ``uv lock`` fails or re-validation fails.
    """
    # 1. Resolve channel and PEP 440 form; reject nightly
    channel = _resolve_channel(version)
    pep440 = _version_to_pep440(version)

    # 2. Build candidates in memory
    candidates: list[tuple[Path, str]] = []

    # pyproject.toml
    pyproj_path = root / "pyproject.toml"
    pyproj_text = pyproj_path.read_text(encoding="utf-8")
    import tomllib  # fmt: skip
    pyproj_data = tomllib.loads(pyproj_text)
    if "version" not in pyproj_data.get("project", {}):
        raise ValueError("pyproject.toml has no [project].version")
    # Replace version in the TOML text to preserve formatting
    pyproj_new = re.sub(
        r'(^version\s*=\s*")[^"]*',
        rf'\g<1>{version}',
        pyproj_text,
        count=1,
        flags=re.MULTILINE,
    )
    if pyproj_new == pyproj_text:
        raise ValueError("pyproject.toml: could not locate version field to replace")
    candidates.append((pyproj_path, pyproj_new))

    # ui/package.json
    ui_pkg_path = root / "ui" / "package.json"
    ui_pkg = _read_json(ui_pkg_path)
    if "version" not in ui_pkg:
        raise ValueError(f"{ui_pkg_path} has no 'version' field")
    ui_pkg["version"] = version
    candidates.append((ui_pkg_path, json.dumps(ui_pkg, indent=2) + "\n"))

    # ui/package-lock.json
    ui_lock_path = root / "ui" / "package-lock.json"
    if ui_lock_path.exists():
        ui_lock: dict[str, object] = _read_json(ui_lock_path)
        if "version" not in ui_lock:
            raise ValueError(f"{ui_lock_path} has no top-level 'version' field")
        ui_lock["version"] = version
        packages = ui_lock.get("packages", {})
        if isinstance(packages, dict) and "" in packages:
            root_pkg = packages[""]
            if isinstance(root_pkg, dict):
                root_pkg["version"] = version
        candidates.append((ui_lock_path, json.dumps(ui_lock, indent=2) + "\n"))

    # manifest.json
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path)
    if "version" not in manifest:
        raise ValueError(f"{manifest_path} has no 'version' field")
    manifest["version"] = version
    manifest["channel"] = channel
    candidates.append((manifest_path, json.dumps(manifest, indent=2) + "\n"))

    # uv.lock — hal0ai package
    lock_path = root / "uv.lock"
    lock_text_orig = lock_path.read_text(encoding="utf-8")
    lock_text_new = _update_uv_lock_version(lock_text_orig, pep440)
    if lock_text_new == lock_text_orig:
        # Might already be correct; re-check
        import tomllib

        lock_data_check = tomllib.loads(lock_text_new)
        hal0ai_check = next(
            (p for p in lock_data_check.get("package", []) if p.get("name") == "hal0ai"),
            None,
        )
        if hal0ai_check and hal0ai_check.get("version") != pep440:
            raise ValueError(
                f"uv.lock: hal0ai version is already {hal0ai_check['version']!r}, "
                f"cannot replace with {pep440!r}"
            )
    candidates.append((lock_path, lock_text_new))

    # 3. Write every candidate to a temporary file in a shared tmpdir
    tmpdir = Path(tempfile.mkdtemp(prefix=".set-version."))
    try:
        temp_paths: list[tuple[Path, Path]] = []
        for dst, content in candidates:
            tmp = _update_file_atomic(dst, content, tmpdir)
            temp_paths.append((tmp, dst))
            # Validate: pyproject.toml — re-parse
            if dst == pyproj_path:
                re_parsed_py = tomllib.loads(content)
                if re_parsed_py["project"]["version"] != version:
                    raise ValueError(
                        f"validation error: {dst} version mismatch after rewrite"
                    )
            # Validate: JSON files — re-parse
            elif dst.suffix in (".json",):
                re_parsed_json = json.loads(content)
                if re_parsed_json.get("version") != version:
                    raise ValueError(
                        f"validation error: {dst} version mismatch after rewrite"
                    )
                # Validate: manifest channel (same JSON parse)
                if dst == manifest_path and re_parsed_json.get("channel") != channel:
                    raise ValueError(
                        f"validation error: {dst} channel mismatch "
                        f"({re_parsed_json.get('channel')!r} != {channel!r})"
                    )
            # Validate: uv.lock
            if dst == lock_path:
                import tomllib

                re_parsed_lock = tomllib.loads(content)
                hal0ai_ver = next(
                    (
                        p["version"]
                        for p in re_parsed_lock.get("package", [])
                        if p.get("name") == "hal0ai"
                    ),
                    None,
                )
                if hal0ai_ver != pep440:
                    raise ValueError(
                        f"validation error: uv.lock hal0ai version "
                        f"{hal0ai_ver!r} != expected {pep440!r}"
                    )

        # 4. All good — atomically replace originals
        for tmp_path, dst_path in temp_paths:
            os.replace(str(tmp_path), str(dst_path))

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 5. Re-run uv lock and re-validate
    subprocess.run(
        ["uv", "lock"],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )

    # 6. Re-validate lock version
    lock_text_final = lock_path.read_text(encoding="utf-8")
    lock_data_final = tomllib.loads(lock_text_final)
    hal0ai_final = next(
        (
            p["version"]
            for p in lock_data_final.get("package", [])
            if p.get("name") == "hal0ai"
        ),
        None,
    )
    if hal0ai_final != pep440:
        raise RuntimeError(
            f"post-lock re-validation: uv.lock hal0ai version "
            f"{hal0ai_final!r} != expected {pep440!r}"
        )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Atomically synchronize version across hal0 release artifacts."
    )
    parser.add_argument(
        "version",
        help="SemVer version string (e.g. 1.0.0-alpha.2). Rejected if nightly.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: validate version + current state, perform no I/O.",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent

    if args.check:
        channel = _resolve_channel(args.version)
        pep440 = _version_to_pep440(args.version)
        print(f"version:      {args.version}")
        print(f"pep440:       {pep440}")
        print(f"channel:      {channel}")
        current_version = _resolve_pyproject_version(root)
        print(f"current:      {current_version}")
        print("result:       VALID (dry-run, no files modified)")
        return

    set_version(root, args.version)
    print(f"Version set to {args.version}")


if __name__ == "__main__":
    _main()
