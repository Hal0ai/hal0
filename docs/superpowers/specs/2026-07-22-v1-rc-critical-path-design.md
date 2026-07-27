# hal0 v1.0.0 RC Critical Path Design

> **Date:** 2026-07-22
> **Status:** Approved design, pending written-spec review
> **Release endpoint:** Validated v1.0.0 release candidate; no tag or publication

## Goal

Produce a validated hal0 v1.0.0 release candidate by landing PR #1330, completing the remaining R5 Memory/Admin MCP, CLI, and Installer/Uninstaller work, closing backend-to-UI contract gaps, completing the FLM rework, and validating the result on halo150 and halo143.

## Scope

The RC includes:

- Reconcile the integration baseline and repair PR #1330 CI.
- R5 section 4: Memory/Admin MCP completion and UI parity.
- R5 section 5: CLI completion and parity with shared API behavior.
- R5 section 6: Installer/Uninstaller completion and upgrade-state UI parity.
- Security UI and migration UX previously grouped under UI D4-D5.
- FLM data/config alignment, model catalog UI, pull/remove behavior, and NPU trio configuration.
- Contract, component, end-to-end, CI, and live two-box validation.

The RC excludes:

- The general diagnostics panel previously grouped under UI D6.
- Raw ONNX and ONNX Runtime GenAI providers.
- Post-v1.0 HP plugins already marked deferred.
- Publishing, tagging, or distributing v1.0.0.

## Architecture

Work proceeds as contract-first vertical slices. Each affected operation is completed through the full path:

```text
domain behavior
  -> backend request/response model
  -> API route and CLI adapter
  -> frontend normalization hook
  -> rendered state and mutation flow
  -> contract, UI, and end-to-end tests
```

Backend compatibility is handled in a narrow adapter. UI components consume one normalized shape and do not infer semantics from absent fields or obsolete names. CLI and UI presentation may differ, but defaults, validation, authorization, and error behavior must come from the same domain operation.

FLM remains an intentional exception to inference-slot architecture: one NPU process serves Chat, STT, and Embed. It is not converted into a one-model-per-slot runner.

## Delivery Gates

### Gate 1: Integration Baseline

PR #1330's `merge/rework-descar-into-main` branch is authoritative. The divergent `feat/llama-set-rows` branch must not be merged wholesale. Existing repair commits may be cherry-picked only after their diff and provenance are verified against the PR head.

PR #1330 lands only when required Python, UI, sunset, and Chromium checks are green.

### Gate 2: Shared Contract Stabilization

Create a contract matrix for every backend shape changed by R5 sections 4-6. Each entry records:

- Canonical request and response model.
- API route and equivalent CLI operation, when both exist.
- Frontend hook or adapter.
- Loading, empty, partial, unauthorized, unavailable, validation-error, and success states.
- Mutation confirmation, progress, refresh, and refusal behavior.
- Backend contract test and frontend fixture or end-to-end test.

Security and migration UX are RC requirements. The broad diagnostics panel remains deferred.

### Gate 3: FLM Completion

Complete FLM as one backend-to-UI contract:

- Remove the stale device-class ownership from the FLM profile while preserving slot-owned NPU placement.
- Populate the FLM seed's hardware-grid fields with NPU-appropriate values.
- Keep the existing FLM image-resolution fallback only with its v1.1.0 sunset marker.
- Add the NPU/FLM model catalog tab with installed/available state, capability filters, and consistent row actions.
- Support pull progress, removal, and refresh against the live FLM catalog.
- Support independent Chat, STT, and Embed model selection through the exact fields accepted by `NpuConfig`.
- Pass per-role models to the single `flm serve` process.

### Gate 4: RC Validation

Run deterministic local gates, full CI, and live validation on halo150 and halo143. Record the evidence in the deploy-validation documentation and RC checklist. Completion produces a release-ready commit and evidence set, but no release tag or publication.

## UI Contract Rules

- Every backend field displayed or mutated by the UI has one canonical normalized representation.
- Components never silently substitute a default for an unknown backend value.
- Compatibility aliases are localized and sunset-stamped.
- Destructive memory, migration, uninstall, and removal actions expose a preview or dry run where supported and require explicit confirmation.
- Backend refusal messages remain visible and actionable; the UI does not collapse them into a generic failure.
- Credentials, keys, and unsanitized logs are never rendered.
- Installed, unavailable, disabled, degraded, and partially configured states are visually and semantically distinct.
- A skipped or stale UI test is not RC evidence. It must be repaired or replaced at the correct contract boundary.

## Error Handling

Validation errors remain field-addressable from backend model through UI control. Authorization failures are distinct from connectivity failures. Long-running pulls and migrations expose progress and terminal status. Partial failures preserve completed work only where the backend operation is explicitly resumable; otherwise operations must be atomic or refuse before mutation.

Migration safety rules are mandatory:

- Run deterministic fixtures on a fresh halo143 environment.
- Never mutate LXC105 during rehearsal.
- Run FLAGS migration in dry-run mode before guarded apply.
- Preserve divergent-share refusal and idempotence.
- Deploy the bilingual slot runtime before the SLOT-B ID flip.
- Preserve and verify the pre-flight backup and rollback path.

## Verification Strategy

Each vertical slice must pass:

- Backend unit and API contract tests.
- CLI tests for shared operations, output, exit status, and refusal behavior.
- UI lint and production build.
- Component or hook tests for normalized response states.
- Targeted Playwright coverage for user-visible flows.
- At least one live-backend smoke test using real API responses rather than only mocked fixtures.
- The repository capped gate: Ruff check, Ruff format check, import/create-app smoke, sunset check, and targeted pytest.

The final live sequence is:

1. Upgrade halo150 and halo143 to the bilingual runtime.
2. Exercise Memory/Admin operations and security refusals.
3. Rehearse memory and config migrations with deterministic fixtures.
4. Run the FLAGS migrator dry run before guarded apply.
5. Re-run the SLOT-B ID flip on halo143 and verify that name-keyed files are not re-seeded and no split-brain occurs.
6. Validate install, upgrade, `doctor perms --fix`, default-store pull, uninstall/reinstall, reboot autostart, and absence of ghost slots on both boxes.
7. Validate FLM catalog, pull/remove progress, and Chat/STT/Embed configuration against the live NPU runtime.
8. Run full CI and record the evidence required for RC acceptance.

Any mismatch among backend response, CLI behavior, and rendered UI reopens the owning vertical slice.

## Deferred Work

The following work remains outside this RC:

- UI D6 general diagnostics panel.
- Raw ONNX and OGA providers and their Phase 0 container feasibility test.
- HP voice, automation, context, legacy-suite, and realtime tail work already marked post-core.
- Router LOC reduction and broad god-module burn-down unless a touched module blocks safe testing.
- GPU benchmark work that requires unsupported newer GGUF inputs.

## Completion Criteria

The design is complete when all four delivery gates are green, the contract matrix has no unowned UI consumer, no stale or skipped UI test is being used as acceptance evidence, both deployment targets pass the live checklist, and the resulting commit is ready for a v1.0.0 release decision without requiring additional implementation.
