# hal0 v1 RC — Updated Installer, Updater, Migration, and Uninstaller Handoff

> **Date:** 2026-07-22  
> **Release endpoint:** validated v1.0.0 RC; no tag or publication without operator approval  
> **Authoritative integration baseline:** PR #1330 head `b91567fcff9c771b89808eadc71379174a0b259f`  
> **Current checkout:** `feat/llama-set-rows` at `fceab946`, dirty; do not reset, clean, stash, or use it as the RC integration base

## Executive response

Do **not** create new standalone `setup.sh`, `updater.sh`, or `migration.sh` files. They do not exist on the authoritative baseline and are not missing production deliverables:

- Production bootstrap: `installer/bootstrap.sh`
- Production installation: `installer/install.sh`
- Guided setup: `hal0 setup`, implemented under `src/hal0/cli/setup_install.py`
- Canonical updater: `src/hal0/updater/updater.py`, exposed through existing CLI/API surfaces
- Migrations: typed, domain-specific Python migrations, including updater config migrations and `src/hal0/slots/migrate_id_keying.py`
- Production uninstall: `installer/uninstall.sh`

The earlier reference to `src/hal0/install/installer.py` is stale: that file is absent from both this checkout and baseline `b91567fc`. Current Python install surfaces include `src/hal0/cli/setup_install.py` and `src/hal0/api/routes/installer.py`. Do not create the stale path merely to satisfy old planning prose.

Most core mechanisms already exist. Remaining RC work is primarily reconciliation of a small number of lifecycle gaps, making the release gate truly blocking, and collecting complete two-host production evidence.

## What already exists

### Bootstrap and install

`installer/bootstrap.sh` and `installer/install.sh` already provide the main production chain:

- channel-manifest and artifact SHA-256 validation;
- Sigstore/cosign support, including legacy detached certificate/signature compatibility;
- `HAL0_BOOTSTRAP_VERIFIED=1` trust handoff;
- versioned `/usr/lib/hal0/hal0-<version>` release trees;
- atomic `/usr/lib/hal0/current` symlink;
- shared managed virtual environment and reinstall behavior;
- release verification gate for direct production installation;
- service-account creation and ownership repair;
- model-store preparation;
- hardware, disk, port, runtime, and GPU/NPU preflight;
- installation and enablement of `installer/systemd/hal0.target`;
- noninteractive first-run setup through the existing `hal0 setup` command.

### Updater

`src/hal0/updater/updater.py` is the **single canonical updater**. It already implements:

- manifest parsing and release/channel policy;
- digest and cosign verification;
- safe extraction into versioned release trees;
- migration gating;
- shared-venv reinstall;
- atomic symlink swap;
- restart handling;
- rollback and previous-version recording;
- stale install/image reconciliation and upgrade migrations.

Do not create a second shell updater or revive stale updater designs in `PLAN.md`.

### Migrations

There is intentionally no monolithic `migration.sh`. Migrations are distributed by ownership:

- updater-driven configuration/release migrations in `src/hal0/updater/updater.py`;
- slot identity migration in `src/hal0/slots/migrate_id_keying.py`;
- FLAGS/config and memory migrations in their owning modules and CLI commands;
- database evolution in database-owned code.

`src/hal0/slots/migrate_id_keying.py` is a guarded, one-shot deployment migration. It must remain explicit and must not run automatically at API startup. Bilingual runtime deployment must precede the live name-to-ID flip.

### Uninstaller

`installer/uninstall.sh` already handles a broad lifecycle surface:

- `hal0.target` and slot/agent units;
- legacy Honcho services/timers;
- Quadlet sources, drop-ins, and generated-unit cleanup;
- stale links and Hermes shims;
- sudoers grants;
- benchmark units/data according to mode;
- managed release trees and virtual environments;
- conservative uninstall preserving configuration/data;
- `--purge` cleanup of declared state, users, images, and related artifacts;
- daemon reload and best-effort residual reporting.

## Adjustments still needed

Only implement gaps verified against the isolated `b91567fc` worktree.

1. **Foreign Hermes backup restoration**  
   Define and test a provenance-safe policy for restoring a pre-hal0 executable captured as `hermes.pre-hal0`. The current cleanup path must not silently discard or strand a foreign operator-owned binary.

2. **First-run lock reconciliation**  
   Determine whether `/var/lib/hal0/.first-run.lock` still has a live producer and consumer. Either retain a defined, tested lifecycle or remove the dead surface. Uninstaller cleanup alone is not proof of correctness.

3. **Canonical channel contract**  
   Reconcile `stable`, `preview`, and `nightly` across schema, release manifest, bootstrap, updater, API, CLI, and tests. Remove obsolete release-test assumptions rather than adding another updater mechanism.

4. **Blocking release lifecycle gate**  
   `scripts/release-test.sh` is not yet adequate proof of the required lifecycle. Extend it, or add a dedicated production-lifecycle report consumed by `scripts/release-check.sh`, so required rows cannot pass as `skip` or `deferred`.

5. **Remove stale deployment-path assumptions**  
   Release validation must check the FHS installation (`/usr/lib/hal0`, `/usr/local/bin/hal0`) and must not allow legacy `/opt/hal0` assumptions to conceal a broken production install.

6. **Prove target behavior, including `--no-start`**  
   Verify `hal0.target` enablement and behavior for fresh install, upgrade, reboot, and no-start installation. Code presence is not live evidence.

7. **Prove model-store and render-device permissions**  
   A real model pull must succeed as the `hal0` service account using the installed default model store. Validate ownership repair and GPU/render-device access on both host types.

8. **Exercise updater failure boundaries**  
   Prove coherent rollback for verification, extraction, migration, venv reinstall, symlink swap, and service-restart failures. Confirm existing slot containers remain available where the contract requires it.

9. **Test conservative and purge contracts**  
   Record exact keep/remove sets. Test conservative uninstall/reinstall and purge/reinstall, including UID/data safety and daemon-reload/reboot ghost-unit checks.

10. **Resolve release-lineage decisions**  
    Confirm ComfyUI digest/path lineage and the CPU-runner image/manifest-key decision across repositories before repinning.

11. **Reconcile stale planning material**  
    Update `PLAN.md` and any handoff references that imply a second updater or nonexistent install modules. Historical documents are intent, not current-state evidence.

## Required RC lifecycle matrix

Every required row must be a recorded **pass**, not skip/deferred. Capture commands, versions, timestamps, outputs, and rollback evidence under `docs/rework/deploy-validation/`.

| Validation row | halo150 privileged | halo143 unprivileged | Required evidence |
|---|---:|---:|---|
| Bootstrap parity | Required | Required | `scripts/check-bootstrap-parity.sh`; fetched bootstrap selects and verifies intended artifact |
| Fresh signed production install | Required | Required | bootstrap-to-install chain, FHS paths, trust gate, healthy services |
| Upgrade in place | Required | Required | config/data preserved, new code active, slots remain coherent |
| Reboot restoration | Required | Required | `hal0.target` enabled; intended API/slots return after reboot |
| `--no-start` behavior | Required | Required | install completes without unintended service start; later enable/start works |
| Service-account model pull | Required | Required | real pull/write as `hal0`; model-store and render permissions proven |
| Update and explicit rollback | Required | Required | previous release restored and recorded; symlink/venv/service state coherent |
| Injected update failures | Required | Required | verification/extraction/migration/venv/restart failures recover safely |
| Migration idempotence | Fixture/live as appropriate | Fixture/live as appropriate | dry-run, refusal, apply, rerun, backup/rollback evidence |
| Conservative uninstall/reinstall | Required | Required | config/data retained; code, units, venvs removed; reinstall reuses state |
| Purge/reinstall | Required | Required | documented purge set removed; clean reinstall succeeds |
| Ghost-slot prevention | Required | Required | no Quadlet source/generated unit/container resurrection after reload/reboot |
| Legacy cleanup | Required | Required | Honcho, sudoers, bench, old shims/timers removed as contracted |
| Foreign Hermes fixture | Required when present | Required when present | safe restore or explicit approved refusal |
| First-run lifecycle | Required | Required | repeatable sentinel/credential behavior; no stale lock |
| Memory migration | Deterministic fixture only | Deterministic fresh fixture only | never mutate LXC105; config tolerance and ordered deletion proven |
| FLAGS migration | Fixture | Dry-run then guarded apply | refusal, idempotence, deployment-window gate, backup evidence |
| Slot ID flip | Fixture/non-destructive | Guarded live rehearsal | bilingual runtime first; no split brain or name-keyed reseeding |
| FLM lifecycle | Where supported | Required NPU | catalog, pull/remove progress, Chat/STT/Embed trio configuration |
| CI and release gates | Required | Required | capped local gates plus all required GitHub CI and lifecycle report freshness |

## Current evidence and limits

Known live evidence remains incomplete:

- halo150 upgrade-in-place reportedly succeeded;
- halo143 stopped at root keyring `EDQUOT`;
- neither result proves the complete lifecycle matrix;
- no current evidence proves fresh install, reboot restoration, service-account model pull, full update/rollback failure handling, either uninstall/reinstall mode, migration idempotence, or ghost-slot prevention on both hosts.

Development deployment (`scripts/dev-bootstrap.sh`, `scripts/push-dev.sh`, and similar helpers) is not production release-install evidence. Keep those reports separate.

Hindsight is reachable at `http://10.0.1.142:9177`, but the recall performed for this update returned only generic architecture/lifecycle memories and `reflect` timed out. Therefore memory supplements—but does not replace—the repository documents, exact baseline inspection, and live evidence. Use the native `:9177/mcp` endpoint until hal0's `/mcp` wrapper mount is repaired.

## Open decisions and risks

- Decide whether production bootstrap cosign verification remains optional while updater verification is mandatory.
- Define behavior for revoked or yanked channel releases.
- Ratify one channel enum and semantics across every surface.
- Define which forward-only migrations block rollback or require backup restoration.
- Decide automatic versus operator-guided restoration of foreign Hermes backups.
- Resolve first-run lock ownership and lifecycle.
- Confirm CPU-runner and ComfyUI image lineage.
- Treat privileged halo150 and unprivileged halo143 as separate validation targets; success on one does not imply success on the other.
- Never mutate LXC105 during Honcho-to-Hindsight rehearsal.
- Do not tag, publish, merge, push, or mutate live systems without the required operator approval.

## Ordered next actions

1. Create an isolated worktree from exact commit `b91567fcff9c771b89808eadc71379174a0b259f`; leave the dirty primary checkout untouched.
2. Verify every claim above against that worktree and record the exact baseline delta.
3. Run focused tests for target wiring, ownership repair, updater channels and rollback, uninstall keep/purge sets, and migration idempotence.
4. Resolve the channel, cosign, revoked-release, Hermes restore, first-run lock, CPU-runner, and ComfyUI decisions.
5. Implement only confirmed gaps; do not add `setup.sh`, `updater.sh`, `migration.sh`, or `src/hal0/install/installer.py`.
6. Make bootstrap parity and the two-host lifecycle matrix blocking inputs to `scripts/release-check.sh`.
7. Run capped local gates and required GitHub CI.
8. Execute the full production lifecycle matrix on halo150 and halo143, storing immutable evidence under `docs/rework/deploy-validation/`.
9. Review all evidence against the matrix. Stop at a validated RC and request operator approval before tagging or releasing.

## Definition of RC readiness

The installer lifecycle is RC-ready only when:

- the isolated branch is based on the exact PR head;
- no duplicate updater/setup/migration mechanism has been introduced;
- all confirmed lifecycle gaps are implemented and reviewed;
- all required CI checks are green;
- every mandatory matrix row passes on its required host/fixture;
- production and development evidence are clearly separated;
- rollback and uninstall residuals are explicitly documented;
- all security, channel, image-lineage, and migration decisions are recorded; and
- the operator explicitly approves any tag or publication.
