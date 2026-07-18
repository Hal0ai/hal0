"""Golden-path verification harness (REWORK.md §Golden-path verification).

This package lands the CI-runnable subset of the 15 deployment-shaped
golden-path scenarios as *interface-level* integration tests, exercised
through the public FastAPI surface (``TestClient``) and the documented
``SlotManager`` entry points only. Per REWORK.md guiding principle 9
("Validate behavior through interfaces, not implementation structure"),
these tests deliberately never assert on manager-private dict shapes or
reach into internals, so they remain valid unchanged across the
destructive SLOT re-key increment B — they are its acceptance harness.

Scenarios covered here (the CI-runnable subset):

  #9  Slot rename without broken references    → test_gp09_slot_rename.py
  #10 Slot delete with unit/state/port cleanup → test_gp10_slot_delete.py
  #14 API restart without stopping slots       → test_gp14_api_restart.py
  #15 Core operation with Hermes disabled      → test_gp15_no_hermes.py

Real podman/systemd is never invoked: every scenario drives the in-memory
``ContainerProvider`` fake (see ``conftest.py``). Steps that are inherently
deploy-only are asserted at the INTENT boundary — the recorded
``load_sync`` / ``unload_sync`` dispatches the fake captures — with the
real half deferred to the halo143 acceptance runbook.

Deploy-only remainder per scenario (the real half these tests stub):

  #9  The live systemd unit is still name-keyed (``hal0-slot@<name>``);
      renaming the running Quadlet ``.container`` unit + the podman
      container on a live host is deploy-only. These tests assert only the
      OFFLINE relabel (stable id, preserved port claim, freed old name).
      Real half -> halo143 acceptance runbook, slot-rename step.

  #10 The real teardown -- ``systemctl stop/disable hal0-slot@<name>``,
      ``podman rm``, and Quadlet unit-file removal on the host -- is
      deploy-only. These tests assert the intent boundary (``unload_sync``
      recorded), the released port claim, and the erased state/config.
      Real half -> halo143 acceptance runbook, slot-teardown step.

  #14 That the slot systemd units genuinely survive an ``hal0-api``
      process restart (independent unit lifetimes) is deploy-only. These
      tests model the surviving containers with a persistent fake and
      assert reconcile-to-running issues NO stop/start dispatch.
      Real half -> halo143 acceptance runbook, api-restart step.

  #15 That a box provisioned WITHOUT ``hal0 agent install hermes`` (no
      hermes venv / gateway process) still serves the core is deploy-only.
      These tests assert ``create_app()`` builds with Hermes surfaces
      disabled, core routes stay live, and the brain engine's import
      graph pulls in no hermes module.
      Real half -> halo143 acceptance runbook, hermes-optional step.
"""
