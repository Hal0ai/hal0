"""Golden-path verification harness (REWORK.md §Golden-path verification).

This package lands the CI-runnable subset of the 15 deployment-shaped
golden-path scenarios as *interface-level* integration tests, exercised
through the public FastAPI surface (``TestClient``) and the documented
``SlotManager`` entry points only. Per REWORK.md guiding principle 9
("Validate behavior through interfaces, not implementation structure"),
these tests deliberately never assert on manager-private dict shapes or
reach into internals, so they remain valid unchanged across the
destructive SLOT re-key increment B — they are its acceptance harness.

Real podman/systemd is never invoked: every scenario drives the in-memory
``ContainerProvider`` fake (see ``conftest.py``). Steps that are inherently
deploy-only are asserted at the INTENT boundary — the recorded
``load_sync`` / ``unload_sync`` dispatches the fake captures — with the
real half deferred to the halo143 acceptance runbook.

---------------------------------------------------------------------------
Complete 15-scenario coverage map (REWORK.md §Golden-path verification L606)
---------------------------------------------------------------------------

Every scenario is verified by exactly ONE of three mechanisms. Per the
CONTRIBUTING anti-scar rules ("one owner per fact"; find the owner before
adding a parallel test), scenarios already owned by a suite are NOT
re-tested here — that would duplicate a source of truth. This map is the
authoritative index so no scenario is silently missing.

  #   scenario                              mechanism        owner
  --  ------------------------------------  ---------------  --------------------------------
  1   Fresh install on a clean LXC          deploy-runbook   docs/rework/golden-paths-halo143-runbook.md
  2   Installer rerun over a healthy box    deploy-runbook   golden-paths-halo143-runbook.md (idempotence); unit half: tests/install/
  3   Upgrade from current stable release   deploy-runbook   golden-paths-halo143-runbook.md
  4   Auth bootstrap and key rotation       existing suite   tests/api/test_auth_core.py + tests/api/test_auth_rotate.py
  5   Model pull, slot assign, inference    CI here          test_gp05_stamped_launch_layering.py (+ deploy half in runbook)
  6   Multi-shard and mmproj model pull     existing suite   tests/registry/test_pull.py + test_fileset.py (R2-golden)
  7   Model revision atomic pointer swap    existing suite   tests/registry/test_pull.py (revision) + test_gc.py (R2-golden)
  8   Model delete, refcount-safe cleanup   existing suite   tests/registry/test_gc.py (R2-golden)
  9   Slot rename without broken refs       CI here          test_gp09_slot_rename.py (+ deploy half in runbook)
  10  Slot delete: unit/state/port cleanup  CI here          test_gp10_slot_delete.py (+ deploy half in runbook)
  11  Permissions drift, repair, rollback   existing suite   tests/install/test_perms.py (depth-2 drift+heal, non-recursive regressions)
  12  Old config: removed fields or tables  existing suite   tests/config/test_extra_policy_lock.py + test_stacks_loader.py::test_unknown_field_raises
  13  NFS-backed model storage              deploy-runbook   golden-paths-halo143-runbook.md
  14  API restart without stopping slots    CI here          test_gp14_api_restart.py (+ deploy half in runbook)
  15  Core operation with Hermes disabled   CI here          test_gp15_no_hermes.py (+ deploy half in runbook)

Legend:
  CI here        — an interface-level test in THIS package, green in the
                   capped local gate + CI. Survives the SLOT-B rewrite.
  existing suite — already owned elsewhere; re-testing here would duplicate
                   a source of truth (anti-scar rule 1). Cited above.
  deploy-runbook — inherently needs a real box (LXC / installer / systemd /
                   podman / NFS mount); scripted in the halo143 runbook, NOT
                   faked in CI. No silent caps — each is an explicit step.

Deploy-only remainder for the "CI here" scenarios (the real half these
interface tests stub — all enumerated in the halo143 acceptance runbook):

  #5  The interface test asserts pull-record → stamped launch layering and
      the ``load_sync`` dispatch. Real GPU inference on a served model is
      deploy-only -> runbook, model-inference step.

  #9  The live systemd unit is still name-keyed (``hal0-slot@<name>``);
      renaming the running Quadlet ``.container`` unit + the podman
      container on a live host is deploy-only. These tests assert only the
      OFFLINE relabel (stable id, preserved port claim, freed old name).
      Real half -> runbook, slot-rename step.

  #10 The real teardown -- ``systemctl stop/disable hal0-slot@<name>``,
      ``podman rm``, and Quadlet unit-file removal on the host -- is
      deploy-only. These tests assert the intent boundary (``unload_sync``
      recorded), the released port claim, and the erased state/config.
      Real half -> runbook, slot-teardown step.

  #14 That the slot systemd units genuinely survive an ``hal0-api``
      process restart (independent unit lifetimes) is deploy-only. These
      tests model the surviving containers with a persistent fake and
      assert reconcile-to-running issues NO stop/start dispatch.
      Real half -> runbook, api-restart step.

  #15 That a box provisioned WITHOUT ``hal0 agent install hermes`` (no
      hermes venv / gateway process) still serves the core is deploy-only.
      These tests assert ``create_app()`` builds with Hermes surfaces
      disabled, core routes stay live, and the brain engine's import
      graph pulls in no hermes module.
      Real half -> runbook, hermes-optional step.
"""
