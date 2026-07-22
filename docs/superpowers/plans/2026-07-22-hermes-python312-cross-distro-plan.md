# Hermes Python 3.12 Cross-Distro Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every managed Hermes environment use exactly Python 3.12 across direct CLI, installer, updater/API, and systemd paths, with persisted resolution and transactional migration.

**Architecture:** `hermes_provision.py` owns one resolver and environment-file contract. The root Hermes prelude resolves and persists the interpreter before privilege drop; non-root provisioning reads the exported/persisted choice. Existing non-3.12 venvs are rebuilt in a sibling directory and swapped atomically only after verification, retaining rollback until health succeeds. Installer and systemd consume the shared persisted file rather than implementing parallel resolution.

**Tech Stack:** Python 3.12, stdlib subprocess/pathlib/tempfile, pytest, Bash installer scripts, systemd unit/drop-in files.

## Global Constraints

- Hermes managed venvs use exactly Python 3.12.
- Resolution precedence is explicit `HAL0_HERMES_PYTHON`, persisted `/etc/hal0/hermes-python.env`, system `python3.12`, then uv-managed 3.12.
- Invalid explicit or persisted paths fail; they must not silently fall back.
- uv-managed Python lives under `/var/lib/hal0/python`, with hal0-owned HOME/cache and mode `0755` traversal.
- `/etc/hal0/hermes-python.env` is root-owned mode `0644`, atomically written, and contains only the validated absolute assignment.
- Existing `/var/lib/hal0/.hermes` state is never moved or deleted during venv migration.
- Failed builds/swaps retain the old venv and roll back safely.
- Hermes failure remains degraded Hermes status, not core-installer failure.
- Preserve separate hal0 and Hindsight Python policies.

## File Map

- Modify `src/hal0/agents/hermes_provision.py`: resolver, validation, persistence, transactional venv installation, upgrade integration.
- Modify `src/hal0/cli/agent_commands.py`: root prelude resolution/export before privilege drop and upgrade/repair convergence.
- Modify `installer/agents/hermes-prereqs.sh`: exact-3.12 prerequisite checks and uv messaging.
- Modify `installer/install.sh`: install `/etc/hal0/hermes-python.env` consumer wiring and reporting without duplicate resolver logic.
- Modify `installer/systemd/hal0-api.service` generation in `installer/install.sh` and `installer/systemd/hal0-agent@hermes.service.d/override.conf`: load the persisted environment.
- Modify relevant updater/API modules only where Hermes jobs bypass the shared prelude.
- Extend `tests/agents/test_hermes_provision.py`: resolver precedence, validation, persistence, uv fallback, migration, rollback, permissions, idempotency.
- Add/extend installer shell tests for each distro family and systemd environment wiring.
- Update install/migration/CLI documentation identified by existing Hermes docs.

---

### Task 1: Establish exact-3.12 resolver contract

**Files:**
- Modify: `src/hal0/agents/hermes_provision.py`
- Test: `tests/agents/test_hermes_provision.py`

- [ ] Write failing tests for: valid explicit 3.12 acceptance; rejection of 3.11/3.13/3.14; precedence of process override over persisted value; persisted value over PATH discovery; system `python3.12` over uv; invalid explicit/persisted paths producing actionable errors.
- [ ] Run `pytest tests/agents/test_hermes_provision.py -k 'python or resolver or persisted' -v` and confirm the new tests fail for missing APIs/old behavior.
- [ ] Implement a typed resolver result/error seam that validates by executing the interpreter and requiring `(3, 12)`, safely parses the env file, rejects newline/metacharacter/path-invalid values, and exposes source/path/version for logs.
- [ ] Replace range constants with the exact 3.12 policy and make uv fallback request `3.12` only.
- [ ] Run the focused tests and confirm they pass; retain compatibility wrappers only where existing callers/tests require them.
- [ ] Commit: `feat(hermes): add exact Python 3.12 resolver`

### Task 2: Persist the resolved interpreter atomically

**Files:**
- Modify: `src/hal0/agents/hermes_provision.py`
- Test: `tests/agents/test_hermes_provision.py`

- [ ] Write failing tests using a temporary env-file path for atomic creation, mode `0644`, idempotent rewrites, root ownership seam where available, and no shell injection/newline output.
- [ ] Run the focused persistence tests and verify failure before implementation.
- [ ] Implement parent-directory creation, temporary-file write plus `os.replace`, exact single assignment format, restrictive input validation, and stable no-op behavior when contents already match.
- [ ] Add tests proving persisted configuration is loaded by subsequent resolver calls and invalid persisted configuration does not fall back.
- [ ] Run focused tests and commit: `feat(hermes): persist resolved Python interpreter`

### Task 3: Wire root prelude, CLI, upgrade, and API paths

**Files:**
- Modify: `src/hal0/cli/agent_commands.py`
- Modify: `src/hal0/api/routes/updater.py` and any exact bypass identified during implementation
- Test: `tests/cli/test_agent_install_hermes.py`, relevant updater tests

- [ ] Add failing tests proving root Hermes install/repair/upgrade resolves before `_run_as_hal0`, exports `HAL0_HERMES_PYTHON`, and passes the value through the re-exec; non-root direct calls load the persisted file.
- [ ] Run focused CLI/updater tests and confirm expected failures.
- [ ] Implement the root-prelude call to the shared resolver/persistence seam, preserve privilege-drop environment sanitization, and route updater/API Hermes operations through the same contract without duplicating distro logic.
- [ ] Add invalid override tests proving the existing venv is not modified.
- [ ] Run focused CLI/updater tests and commit: `feat(hermes): resolve Python before privilege boundaries`

### Task 4: Make venv migration transactional

**Files:**
- Modify: `src/hal0/agents/hermes_provision.py`
- Test: `tests/agents/test_hermes_provision.py`, `tests/agents/test_hermes_provision_idempotency.py`

- [ ] Write failing tests for retaining an existing 3.12 venv, replacing 3.11/3.13/3.14, preserving `.hermes` state, failed replacement leaving the old venv intact, failed swap rollback, and second install avoiding download/replacement.
- [ ] Run the migration tests and verify the old direct-delete behavior fails them.
- [ ] Implement sibling temporary build under the venv parent, install and smoke-verify Python 3.12/Hermes, rename the old venv to a unique rollback path, atomically rename the replacement, verify immediately, delete rollback only after success, and restore it on swap/verification failure. Ensure cleanup never touches `HERMES_HOME`.
- [ ] Update upgrade flow to use the same venv installation/migration seam and preserve actionable errors.
- [ ] Run focused migration/idempotency tests and commit: `feat(hermes): migrate Hermes venvs transactionally`

### Task 5: Update cross-distro prerequisites and installer behavior

**Files:**
- Modify: `installer/agents/hermes-prereqs.sh`
- Modify: `installer/install.sh`
- Test: existing installer shell test suite plus new focused fixtures under `tests/installer/` if established by repository conventions

- [ ] Add failing mocked-distro tests for Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE, and Alpine with system 3.12 and with only non-3.12 Python; verify only the latter requires usable uv and all messaging requests 3.12.
- [ ] Run shell tests and confirm old 3.11–3.13 acceptance fails the new expectations.
- [ ] Change prerequisite probes/messages to recognize only system Python 3.12 or uv, while retaining generic package prerequisites and `HAL0_SKIP_HERMES=1` behavior.
- [ ] Ensure installer invokes the shared Hermes install path and reports degraded Hermes failures without aborting core installation; do not add a second Python resolver.
- [ ] Run shell tests and commit: `fix(installer): require exact Hermes Python 3.12`

### Task 6: Wire systemd environment consumers

**Files:**
- Modify: `installer/install.sh`
- Modify: `installer/systemd/hal0-agent@hermes.service.d/override.conf`
- Test: installer/systemd fixture tests

- [ ] Add failing tests asserting generated `hal0-api.service` and Hermes drop-in contain `EnvironmentFile=-/etc/hal0/hermes-python.env`, while unrelated services remain unchanged.
- [ ] Run fixture tests and verify they fail before wiring.
- [ ] Add the optional environment file to the API unit generation and Hermes-specific drop-in, preserve existing env files and service behavior, and make reruns idempotent.
- [ ] Add installer assertions that the persisted file is present before service start when Hermes is enabled and that `HAL0_SKIP_HERMES=1` remains supported.
- [ ] Run systemd/installer tests and commit: `feat(systemd): load persisted Hermes interpreter policy`

### Task 7: Update diagnostics and documentation

**Files:**
- Modify: existing install/migration/CLI Hermes documentation identified by repository search
- Modify: `installer/README.md` if needed
- Test: documentation/example assertions where present

- [ ] Add the exact policy, precedence, env-file path, managed layout, offline remediation, rollback semantics, and distinction from hal0/Hindsight policies.
- [ ] Add doctor/installer summary coverage for persisted interpreter path, actual venv minor, and mismatch remediation.
- [ ] Run documentation/example checks and commit: `docs(hermes): document Python 3.12 policy and migration`

### Task 8: Full verification and graph refresh

**Files:**
- Modify: code/tests/docs from prior tasks only

- [ ] Run focused Python tests, installer tests, shell syntax checks, and systemd fixture tests.
- [ ] Run the full repository verification command from `pyproject.toml`/Makefile (at minimum `pytest`, Ruff/type checks as configured).
- [ ] Verify exact acceptance criteria: fresh 3.12 venv, 3.14-only uv fallback, no second-run download/replacement, all consumer paths agree, and state preservation.
- [ ] Run `graphify update .` from the worktree as required by `AGENTS.md`.
- [ ] Review `git diff`, `git status`, and Shepherd changes before reporting completion.
