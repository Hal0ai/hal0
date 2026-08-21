"""#1896 — ``hal0 doctor perms`` must be GREEN on a fresh box, and STAY green.

The defect this file locks down is not a leak; it is a *contradiction* between
three shipped components that each declared a different truth for the same
paths, so the self-audit could never converge:

  1. ``STATE.md`` — ``hermes_provision._atomic_write`` replaced the file with a
     fresh tmp file born at the process umask (``0644`` under the daemon's
     ``UMask=0022``), silently discarding the ``0664`` the table declares and
     ``--fix`` had just applied.
  2. ``secrets/`` + ``secrets/agents/`` — the ``hal0-agentenv`` privileged seam
     re-tightens both to ``0700`` on every ``merge-secrets``, while the table
     declared ``0755``. The two shipped components disagreed, so the mode
     oscillated forever. Reconciled toward the RESTRICTIVE side (``0700``):
     nothing but root ever traverses that tree.
  3. Daemon-created dirs under the recursive ``slots/`` / ``models/`` /
     ``model-pull-jobs/`` rows — ``Path.mkdir`` masks its mode with the umask,
     so a ``UMask=0022`` daemon births ``2755`` where the table declares
     ``2775`` (setgid survives inheritance; the group-write bit does not).

Deliberately NOT asserted anywhere here: a drift ROW COUNT. The concrete path
set grows with every slot loaded and every model pulled, so a count assertion
is a false-failure generator. The invariant is *green after mutation*.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.install import perms

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTENV_WRAPPER = REPO_ROOT / "installer" / "wrappers" / "hal0-agentenv"


# ── helpers ───────────────────────────────────────────────────────────────────


def _mode_drift(table: list[perms.PermRow]) -> list[tuple[str, str, str]]:
    """Every existing path whose MODE differs from its declared mode.

    Owner/group are deliberately out of scope: an unprivileged test process
    cannot chown to ``hal0``/``root``, and the #1896 non-convergence is a
    mode-only phenomenon. Returns ``(path, declared, observed)`` octal triples
    so a failure names the offender instead of a bare count.
    """
    out: list[tuple[str, str, str]] = []
    for diff in perms.plan(table).diffs:
        before = diff.before
        if not before.exists or before.is_symlink or before.mode is None:
            continue
        if before.mode != diff.mode:
            out.append((str(diff.path), oct(diff.mode), oct(before.mode)))
    return out


def _materialise_fresh_install(table: list[perms.PermRow]) -> None:
    """Build the on-disk tree a fresh install leaves AFTER ``--fix`` ran.

    Every non-optional row's target is created and chmod'ed to its declared
    mode — the state ``hal0 doctor perms`` is supposed to call green.
    """
    for row in table:
        if row.optional:
            continue
        target = row.target
        # A row's mode carries the file-type-independent permission bits; the
        # non-optional rows in the table are all directories.
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, row.mode)


# ── component 2: the secrets/ mode contradiction ──────────────────────────────


def test_secrets_rows_declare_0700(tmp_hal0_home: str) -> None:
    """The table must declare the RESTRICTIVE mode the agentenv seam enforces."""
    by_target = {r.target: r for r in perms.ownership_table(service_user="hal0")}
    var_lib = paths.var_lib()

    secrets = by_target[var_lib / "secrets"]
    agents = by_target[var_lib / "secrets" / "agents"]

    assert (secrets.owner, secrets.group, secrets.mode) == ("root", "root", 0o700)
    assert (agents.owner, agents.group, agents.mode) == ("root", "root", 0o700)
    # The secrecy contract one level down is untouched by the widening fix:
    # the *.env files stay owner-read-only.
    assert agents.glob == "*.env"
    assert agents.child_mode == 0o600


def test_agentenv_wrapper_and_table_agree_on_the_secrets_mode() -> None:
    """The two shipped components may never disagree again.

    ``hal0-agentenv`` runs ``install -d -m <mode>`` on both secrets dirs on
    every ``merge-secrets``. If that mode and the table's ever diverge the
    audit oscillates forever (#1896) — so parse the wrapper and compare.
    """
    body = AGENTENV_WRAPPER.read_text(encoding="utf-8")
    match = re.search(
        r"install\s+-d\s+-m\s+0?(?P<mode>[0-7]{3,4})\b[^\n]*/var/lib/hal0/secrets",
        body,
    )
    assert match, "hal0-agentenv no longer creates the secrets dirs with an explicit mode"
    wrapper_mode = int(match.group("mode"), 8)

    table = perms.ownership_table(service_user="hal0")
    by_target = {r.target: r for r in table}
    declared = by_target[paths.var_lib() / "secrets"].mode
    assert wrapper_mode == declared, (
        f"hal0-agentenv installs secrets/ 0{wrapper_mode:o} but the ownership "
        f"table declares 0{declared:o} — that disagreement IS #1896"
    )


# ── component 3: umask-proof shared state dirs ────────────────────────────────


@pytest.mark.parametrize("umask", [0o022, 0o077, 0o002])
def test_ensure_shared_dir_beats_the_process_umask(tmp_hal0_home: str, umask: int) -> None:
    """``mkdir`` masks its mode; the helper must not."""
    var_lib = paths.var_lib()
    var_lib.mkdir(parents=True, exist_ok=True)
    old = os.umask(umask)
    try:
        made = perms.ensure_shared_dir(var_lib / "models" / "org" / "repo")
    finally:
        os.umask(old)
    assert made.is_dir()
    for part in (var_lib / "models", var_lib / "models" / "org", made):
        assert os.stat(part).st_mode & 0o7777 == perms.SHARED_DIR_MODE, part


def test_ensure_shared_dir_never_widens_an_existing_dir(tmp_hal0_home: str) -> None:
    """Only components this call CREATES are chmod'ed.

    An existing dir may be declared at a deliberately tighter mode elsewhere in
    the table (``secrets/`` 0700, ``agents/`` 0711); a lazy mkdir passing
    through it must never widen it.
    """
    tight = paths.var_lib() / "secrets"
    tight.mkdir(parents=True, exist_ok=True)
    os.chmod(tight, 0o700)
    perms.ensure_shared_dir(tight / "agents")
    assert os.stat(tight).st_mode & 0o7777 == 0o700


def test_ensure_shared_dir_never_chmods_outside_hal0_roots(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    """The chmod sink is contained; the mkdir behaviour is not.

    Callers derive these paths from request-supplied ids. A path that escapes
    every hal0 root is still created — the caller may legitimately be pointed
    at an operator's own tree — but it is never re-moded.
    """
    outside = tmp_path / "elsewhere" / "not-hal0"
    old = os.umask(0o022)
    try:
        made = perms.ensure_shared_dir(outside)
    finally:
        os.umask(old)
    assert made.is_dir()
    assert os.stat(made).st_mode & 0o7777 == 0o755  # umask default, untouched


def test_ensure_shared_dir_is_fail_soft_on_chmod_refusal(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model store on NFS may refuse chmod — that must never break a pull."""

    def _boom(*_a: object, **_k: object) -> None:
        raise PermissionError("read-only export")

    monkeypatch.setattr(perms.os, "chmod", _boom)
    made = perms.ensure_shared_dir(paths.var_lib() / "nfs" / "models")
    assert made.is_dir()


# ── component 1: the atomic-write mode discard ────────────────────────────────


def test_atomic_write_preserves_an_existing_files_mode(tmp_path: Path) -> None:
    """tmp-write + ``os.replace`` must not silently re-mode the target."""
    from hal0.agents import hermes_provision

    target = tmp_path / "STATE.md"
    target.write_text("old\n", encoding="utf-8")
    os.chmod(target, 0o664)

    old = os.umask(0o022)
    try:
        hermes_provision._atomic_write(target, "new\n")
    finally:
        os.umask(old)

    assert target.read_text(encoding="utf-8") == "new\n"
    assert os.stat(target).st_mode & 0o7777 == 0o664


def test_atomic_write_honours_an_explicit_mode(tmp_path: Path) -> None:
    """A first render (no existing file) still lands the declared mode."""
    from hal0.agents import hermes_provision

    target = tmp_path / "STATE.md"
    old = os.umask(0o022)
    try:
        hermes_provision._atomic_write(target, "fresh\n", mode=0o664)
    finally:
        os.umask(old)
    assert os.stat(target).st_mode & 0o7777 == 0o664


def test_state_md_row_matches_what_the_renderer_writes(tmp_hal0_home: str) -> None:
    """The declared STATE.md mode and the renderer's write mode are one value."""
    from hal0.agents import hermes_provision

    by_target = {r.target: r for r in perms.ownership_table(service_user="hal0")}
    declared = by_target[paths.var_lib() / "STATE.md"].mode
    assert declared == hermes_provision.RUNTIME_SNAPSHOT_MODE


# ── the whole point: green, and green again after real mutations ──────────────


def test_fresh_install_tree_is_green(tmp_hal0_home: str) -> None:
    table = perms.ownership_table(service_user="hal0")
    _materialise_fresh_install(table)
    assert _mode_drift(table) == []


def test_fresh_install_tree_actually_exercises_the_secrets_mode(tmp_hal0_home: str) -> None:
    """#1942 review finding 4: the 0700 change must be BOUND by a convergence test.

    ``PermRow.optional`` defaults ``True``, and neither secrets row overrode
    it, so ``_materialise_fresh_install`` (which skips optional rows) never
    created ``secrets/`` — the fresh-install green check above passed
    trivially by never looking at the path the whole PR is about. Both
    secrets rows are now ``optional=False``; this asserts the fresh-install
    tree actually contains them, born at the declared 0700, not merely that
    the declaration table says 0700 (that's :func:`test_secrets_rows_declare_0700`).
    """
    table = perms.ownership_table(service_user="hal0")
    _materialise_fresh_install(table)
    var_lib = paths.var_lib()
    secrets = var_lib / "secrets"
    agents = var_lib / "secrets" / "agents"
    assert secrets.is_dir(), "_materialise_fresh_install must create secrets/"
    assert agents.is_dir(), "_materialise_fresh_install must create secrets/agents/"
    assert os.stat(secrets).st_mode & 0o7777 == 0o700
    assert os.stat(agents).st_mode & 0o7777 == 0o700


def test_stays_green_after_slot_load_model_pull_and_state_render(
    tmp_hal0_home: str,
) -> None:
    """The #1896 acceptance criterion, verbatim from the issue.

    "On a fresh install, ``hal0 doctor perms`` is green, and stays green after
    a slot load, a model pull, and a hermes ``STATE.md`` render."

    Each mutation runs through the REAL writer, under the daemon's own
    ``UMask=0022``, and the audit is re-run after each one. No row count is
    asserted — only that the drift set is empty.
    """
    from hal0.agents import hermes_provision
    from hal0.registry import pull
    from hal0.slots import state as slot_state

    table = perms.ownership_table(service_user="hal0")
    _materialise_fresh_install(table)
    var_lib = paths.var_lib()

    old_umask = os.umask(0o022)  # the shipped hal0-api unit's umask
    try:
        # 1. slot load -> slots/<id>/state.json (dir born by the writer)
        slot_state.write_state_atomic(
            var_lib / "slots" / "gpu-rocm-0" / "state.json",
            slot_state.SlotStateRecord(name="gpu-rocm-0", state=slot_state.SlotState.READY),
        )
        assert _mode_drift(table) == [], "drift after a slot load"

        # 2. model pull -> model-pull-jobs/<id>.json + models/<org>/<repo>/
        pull.persist_pull_job(pull.PullJob(job_id="j1", model_id="org/repo", state="completed"))
        perms.ensure_shared_dir(var_lib / "models" / "org" / "repo")
        assert _mode_drift(table) == [], "drift after a model pull"

        # 3. hermes STATE.md render
        state_md = var_lib / "STATE.md"
        hermes_provision._atomic_write(state_md, "# state\n", mode=0o664)
        assert _mode_drift(table) == [], "drift after the first STATE.md render"
        hermes_provision._atomic_write(state_md, "# state 2\n")
        assert _mode_drift(table) == [], "drift after a STATE.md re-render"
    finally:
        os.umask(old_umask)


def test_stays_green_after_update_job_image_cache_and_chat_template_writes(
    tmp_hal0_home: str,
) -> None:
    """#1958 review finding 1 (Codex P2): the same #1896 acceptance criterion,
    for the three newest ``var_lib()``-rooted writers this table declares rows
    for (``update-jobs/``, ``images/cache/``, ``models/chat-templates/``).

    Each birthed its directory via a bare ``mkdir`` (masked by the daemon's
    own umask), so the declared ``2775`` mode landed ``2755`` and
    ``doctor perms`` drifted right after first normal use — on every update
    job, every image generation, every custom chat-template save. Routing
    each writer through :func:`hal0.install.perms.ensure_shared_dir` (the
    same fix ``registry/pull.py``'s ``persist_pull_job`` already uses) is
    what this test locks down.

    The chat-template leg runs :func:`hal0.templates.seed_chat_templates`
    BEFORE ``create_chat_template`` — production order, not test-of-convenience
    order. ``create_app()`` calls ``seed_chat_templates`` at startup
    (``api/__init__.py``), so it is the ACTUAL first creator of
    ``models/chat-templates/`` on every real box; the daemon never reaches
    ``create_chat_template`` against an absent dir. Exercising the writers in
    the opposite order (as an earlier revision of this test did) let
    ``create_chat_template``'s own ``ensure_shared_dir`` heal a dir it never
    actually births in production, masking a real no-op: ``ensure_shared_dir``
    only chmods components it creates (see
    ``test_ensure_shared_dir_never_widens_an_existing_dir`` above), so once
    the startup seed has birthed the dir ``2755`` a second, later
    ``ensure_shared_dir(store)`` call changes nothing — #1958 delta review.
    """
    import asyncio

    from hal0.api import image_cache
    from hal0.api.routes import chat_templates
    from hal0.api.routes.updater import _persist_job
    from hal0.templates import seed_chat_templates

    table = perms.ownership_table(service_user="hal0")
    _materialise_fresh_install(table)

    old_umask = os.umask(0o022)  # the shipped hal0-api unit's umask
    try:
        # 1. update job persist -> update-jobs/<id>.json (dir born by the writer)
        _persist_job({"id": "job-1", "state": "queued"})
        assert _mode_drift(table) == [], "drift after an update-job persist"

        # 2. image-gen write -> images/cache/<uuid>.png (dir born by the writer)
        image_cache.write_png(b"\x89PNG\r\n\x1a\nfake-png-bytes")
        assert _mode_drift(table) == [], "drift after an image-cache write"

        # 3a. hal0-api startup -> seed_chat_templates() births
        #     models/chat-templates/ FIRST, exactly like create_app() does on
        #     every real boot, well before any POST /api/chat-templates.
        seed_chat_templates()
        assert _mode_drift(table) == [], "drift after the startup chat-template seed"

        # 3b. custom chat-template write -> models/chat-templates/<id>.jinja,
        #     into the dir the seed above already created.
        asyncio.run(
            chat_templates.create_chat_template(
                chat_templates._TemplateBody(id="custom", content="{{ x }}")
            )
        )
        assert _mode_drift(table) == [], "drift after a chat-template write"
    finally:
        os.umask(old_umask)
