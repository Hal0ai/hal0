# Hermes Python 3.12 Cross-Distro Installation Design

**Date:** 2026-07-22  
**Status:** Approved for planning  
**Target:** hal0 installer, updater, Hermes provisioner, and service environment

## Problem

hal0 supports Python 3.12 through 3.14, but the supported Hermes release does not run reliably on Python 3.14. Hermes wheels declare `requires-python >=3.11,<3.14`; on a Python-3.14-only host, pip can select an older broken Hermes release. Current `main` avoids 3.14 but still chooses any Python from 3.11 through 3.13 and provisions Python 3.13 with uv as its fallback. That leaves deployments dependent on whichever compatible minor happens to be installed.

The installation contract must instead make Hermes deterministic: every Hermes managed venv uses Python 3.12. This must work on Proxmox LXC and bare metal across every distro family currently advertised by hal0, including hosts whose system Python is 3.14.

## Goals

- Pin the Hermes managed venv to Python 3.12 exactly.
- Prefer a working system `python3.12` when present.
- Otherwise install a uv-managed Python 3.12 under `/var/lib/hal0/python`.
- Persist the resolved interpreter as `HAL0_HERMES_PYTHON`.
- Use the same behavior on bare metal and systemd containers, including Proxmox LXC.
- Support Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE, and Alpine.
- Migrate existing Hermes venvs built on Python 3.11, 3.13, or 3.14 without changing `/var/lib/hal0/.hermes` state.
- Preserve the existing behavior where Hermes failure is reported clearly but does not invalidate an otherwise healthy hal0 core installation.

## Non-goals

- Restricting the Python version used by hal0 itself.
- Changing the Hindsight Python resolver.
- Installing distro-specific PPAs or third-party package repositories.
- Containerizing Hermes.
- Adding LXC-specific CPU or architecture overrides.
- Supporting non-systemd hosts or non-x86_64 inference images.

## Architecture

### Single policy

The Hermes interpreter policy has one source of truth:

```text
HERMES_PYTHON_VERSION = 3.12
```

The provisioner must not probe or accept Python 3.11, 3.13, or 3.14 for a newly created Hermes venv. Hermes upstream compatibility may remain broader, but hal0's deployment policy is deliberately narrower for reproducibility.

### Resolution order

The root prelude of `hal0 agent install hermes` resolves the interpreter before dropping privileges:

1. Read `HAL0_HERMES_PYTHON` from the process environment when explicitly set.
2. Otherwise read the persisted value from `/etc/hal0/hermes-python.env` when present.
3. Otherwise locate `python3.12` on `PATH`.
4. Otherwise ensure `uv` is installed using the existing cross-distro prerequisite path, then run `uv python install 3.12` and `uv python find 3.12`.
5. Validate the selected executable by running it and requiring exactly Python 3.12.

An explicit or persisted path that is missing, not executable, or not Python 3.12 is a configuration error. The resolver must not silently choose another interpreter in that case.

### Managed Python layout

uv-managed interpreters remain under:

```text
/var/lib/hal0/python
```

The directory is mode `0755`, and the managed interpreter must be traversable and executable by the `hal0` service account. uv cache and HOME remain pinned to hal0-owned paths rather than `/root`.

No distro-specific repository is required. Package-manager integration is used only for generic prerequisites such as pipx/uv where already supported; uv supplies Python 3.12 when the distro does not.

### Persisted environment contract

The resolver atomically writes:

```text
/etc/hal0/hermes-python.env
```

with exactly:

```text
HAL0_HERMES_PYTHON=/absolute/path/to/python3.12
```

The file contains no secret and is installed root-owned, mode `0644`. The resolved value is exported into the current provisioning process before privilege drop.

The following consumers load it:

- `hal0-api.service`, so API-triggered repair and upgrade jobs inherit the same interpreter.
- `hal0-agent@hermes.service` through its Hermes-specific drop-in.
- `hal0 agent install|upgrade|repair hermes`, which reads the file directly when the calling shell has not exported the variable.
- The installer, which invokes the same agent-install path rather than implementing a second Python resolver.

This makes the direct CLI, installer, updater, API, and systemd paths converge on one interpreter.

## Provisioning and Upgrade Flow

### Fresh installation

1. The normal installer establishes the hal0 runtime and cross-distro Hermes prerequisites.
2. `hal0 agent install hermes` resolves exact Python 3.12.
3. The resolver persists `HAL0_HERMES_PYTHON`.
4. The provisioner creates `/var/lib/hal0/venvs/hermes` with that interpreter.
5. It installs the vetted Hermes requirement and verifies:
   - venv reports Python 3.12;
   - the `hermes` console script imports and executes;
   - dashboard assets are discoverable without a hard-coded site-packages minor.
6. Existing Hermes config, plugins, personas, and service setup proceed unchanged.

### Existing Python 3.12 venv

The provisioner keeps the venv and performs the normal idempotent package reconciliation. It does not rebuild merely because the base interpreter path was newly persisted.

### Existing Python 3.11, 3.13, or 3.14 venv

The venv contains packages only; operator state lives under `/var/lib/hal0/.hermes`. The provisioner therefore:

1. Resolves and persists Python 3.12.
2. Builds and verifies a replacement venv in a sibling temporary directory.
3. Renames the old venv to a timestamped rollback path.
4. Atomically renames the verified Python-3.12 venv into `/var/lib/hal0/venvs/hermes`.
5. Removes the rollback venv only after Hermes health verification succeeds.
6. Restores the rollback venv if the swap or immediate verification fails.

`/var/lib/hal0/.hermes`, agent databases, credentials, personas, and project state are never deleted or moved by this operation.

## Platform Behavior

The resolver branches on available tools, not substrate identity. LXC and bare-metal hosts use identical logic.

| Platform example | System Python | Hermes result |
|---|---:|---|
| Ubuntu 24.04 LXC or bare metal | 3.12 | Use `/usr/bin/python3.12` |
| Ubuntu 26.04 LXC or bare metal | 3.14 | uv installs managed 3.12 |
| Debian with Python 3.11 | 3.11 | uv installs managed 3.12 |
| Fedora/RHEL with Python 3.13+ | 3.13+ | uv installs managed 3.12 |
| Arch rolling | current rolling Python | uv installs managed 3.12 unless system 3.12 exists |
| openSUSE rolling/current | current distro Python | uv installs managed 3.12 unless system 3.12 exists |
| Alpine | current distro Python | uv installs managed 3.12 unless system 3.12 exists |

Proxmox does not need CPU-type rewriting. Architecture detection remains the normal kernel/coreutils result, and the installer continues to require x86_64 for shipped inference assets.

## Error Handling

- **Invalid explicit override:** fail Hermes provisioning with the path and detected version; do not modify an existing venv.
- **Stale persisted override:** fail with instructions to remove or repair `/etc/hal0/hermes-python.env`; do not silently drift.
- **uv unavailable and installation fails:** report the package-manager attempt and the manual uv installation command.
- **Offline managed-Python download:** retain the existing Hermes venv and report that Python 3.12 could not be provisioned.
- **Replacement venv build failure:** leave the current venv and all Hermes state untouched.
- **Atomic swap verification failure:** restore the rollback venv and report both paths.
- **Core installer behavior:** finish or retain the hal0 core installation, but emit a prominent Hermes-degraded summary and remediation command.

Logs must include the selected interpreter source (`environment`, `persisted`, `system`, or `uv-managed`), path, and version, without printing unrelated environment values.

## Security and Permissions

- Never install managed Python under `/root`.
- Force provisioning subprocess `HOME`, uv cache, and install paths into hal0-owned state directories.
- Validate the interpreter by execution rather than trusting its filename.
- Write the environment file atomically and reject shell metacharacters/newlines in the path.
- Keep the environment file root-owned and non-secret.
- Preserve the existing privilege drop: root performs system installation and environment-file writes; Hermes package/config creation runs as `hal0`.

## Installer and Updater Integration

- `installer/agents/hermes-prereqs.sh` checks whether either system Python 3.12 or a usable uv path exists. It no longer treats Python 3.11/3.13 as a completed Hermes interpreter path.
- `hermes_provision.py` owns exact-version resolution, validation, managed installation, persistence, and venv migration.
- The updater invokes the same resolver before Hermes package reconciliation. It does not duplicate distro logic.
- Existing installation paths that use `hal0 agent install hermes` inherit the policy automatically.
- Installer summaries and `hal0 doctor` report the persisted interpreter path, actual venv Python minor, and mismatch remediation.

## Testing

### Unit tests

- Resolver accepts a valid explicit Python 3.12 path.
- Resolver rejects explicit Python 3.11, 3.13, and 3.14 paths.
- Persisted configuration has lower precedence than a process override and higher precedence than discovery.
- System `python3.12` wins without invoking uv.
- uv fallback requests exactly `3.12`, uses `/var/lib/hal0/python`, and sanitizes HOME/cache.
- Missing uv and failed download produce actionable errors.
- Environment file writing is atomic, idempotent, and safely escaped.
- Existing 3.12 venv is retained.
- Existing 3.11/3.13/3.14 venv is transactionally replaced.
- Failed replacement leaves the old venv intact.
- Hermes home and database paths remain untouched.

### Installer tests

Mock each advertised distro family and verify both cases:

- system Python 3.12 available;
- only a non-3.12 system Python available, requiring uv.

Verify systemd units load `/etc/hal0/hermes-python.env`, installer reruns are idempotent, and `HAL0_SKIP_HERMES=1` remains supported.

### Integration matrix

At minimum, validate:

- Ubuntu 24.04 with system Python 3.12;
- Ubuntu 26.04 with only system Python 3.14;
- one rolling distro without packaged Python 3.12;
- Proxmox LXC and bare-metal execution paths;
- upgrade from each existing Hermes venv minor: 3.11, 3.12, 3.13, and 3.14.

## Documentation

Update the install/migration guide and CLI reference with:

- the exact Python 3.12 policy;
- `HAL0_HERMES_PYTHON` and `/etc/hal0/hermes-python.env`;
- managed interpreter location and offline remediation;
- transactional venv migration and rollback behavior;
- the distinction between hal0, Hindsight, and Hermes interpreter policies.

Live deployment findings and repairs remain in the LXC 105 migration transcript and report so installer/updater regressions can be converted into targeted tests.

## Acceptance Criteria

- Every successful fresh Hermes installation reports Python 3.12 from `/var/lib/hal0/venvs/hermes/bin/python`.
- A Python-3.14-only host installs Hermes without selecting an old Hermes wheel.
- All advertised distro families have deterministic resolver tests.
- LXC and bare-metal paths require no special architecture override.
- A second install performs no Python download or venv replacement.
- Existing non-3.12 Hermes venvs migrate without loss of `/var/lib/hal0/.hermes` data.
- Direct CLI, installer, updater, API, and systemd paths agree on `HAL0_HERMES_PYTHON`.
