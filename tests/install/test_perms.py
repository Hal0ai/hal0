"""Unit tests for hal0.install.perms — the declarative ownership table.

Covers:
  * ``plan()`` reports no drift when disk matches the table (the Phase-0 no-op).
  * Drift is detected per owner / group / mode; absent paths are ``absent``,
    never ``changed``.
  * Glob rows expand to one diff per matching child.
  * ``commit()`` applies drifted diffs and rolls back atomically on failure.
  * ``audit_rows`` renders the ok / drift / absent vocabulary.
  * ``ownership_table()`` builds under HAL0_HOME so it is test-isolated.

The plan/diff/commit logic is exercised through injected ``observe_fn`` /
``chown`` / ``chmod`` seams so no real privileged filesystem is needed.
"""

from __future__ import annotations

import grp
import os
import pwd
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.install import perms


def _me() -> tuple[str, str]:
    return (
        pwd.getpwuid(os.getuid()).pw_name,
        grp.getgrgid(os.getgid()).gr_name,
    )


def _obs(path: Path, owner: str, group: str, mode: int) -> perms.PermObservation:
    return perms.PermObservation(path=path, exists=True, owner=owner, group=group, mode=mode)


# ── plan / drift ──────────────────────────────────────────────────────────────


def test_plan_is_noop_when_disk_matches_table() -> None:
    row = perms.PermRow(Path("/etc/hal0/hal0.toml"), "root", "root", 0o600, role="hal0.toml")
    observe = lambda p: _obs(p, "root", "root", 0o600)  # noqa: E731
    pl = perms.plan([row], observe_fn=observe)
    assert pl.changed is False
    assert pl.drifted == ()
    assert pl.diffs[0].changed is False


@pytest.mark.parametrize(
    "obs_owner,obs_group,obs_mode",
    [
        ("hal0", "root", 0o600),  # wrong owner
        ("root", "hal0", 0o600),  # wrong group
        ("root", "root", 0o644),  # wrong mode
    ],
)
def test_plan_detects_each_drift_axis(obs_owner: str, obs_group: str, obs_mode: int) -> None:
    row = perms.PermRow(Path("/etc/hal0/hal0.toml"), "root", "root", 0o600)
    pl = perms.plan([row], observe_fn=lambda p: _obs(p, obs_owner, obs_group, obs_mode))
    assert pl.changed is True
    assert len(pl.drifted) == 1


def test_absent_path_is_not_changed() -> None:
    row = perms.PermRow(Path("/var/lib/hal0/secrets"), "root", "root", 0o755)
    absent = lambda p: perms.PermObservation(p, exists=False, owner=None, group=None, mode=None)  # noqa: E731
    pl = perms.plan([row], observe_fn=absent)
    assert pl.changed is False
    assert pl.diffs[0].changed is False
    rows = perms.audit_rows(pl)
    assert rows[0]["status"] == "absent"


# ── glob expansion against a real tmp tree ────────────────────────────────────


def test_glob_row_expands_and_noops_on_self_owned_tree(tmp_path: Path) -> None:
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "agent.toml").write_text("x = 1\n")
    (slots / "util.toml").write_text("y = 2\n")
    owner, group = _me()
    # Declare the table to match what this test process actually owns -> no-op.
    dir_mode = perms.observe(slots).mode
    file_mode = perms.observe(slots / "agent.toml").mode
    assert dir_mode is not None and file_mode is not None
    row = perms.PermRow(
        slots, owner, group, dir_mode, glob="*.toml", child_mode=file_mode, role="slots"
    )
    pl = perms.plan([row])  # real observe
    # dir + 2 files = 3 diffs, all clean (dir keeps dir_mode, files get child_mode)
    assert len(pl.diffs) == 3
    assert pl.changed is False

    # A row whose declared file mode differs from disk -> that file drifts.
    pl2 = perms.plan(
        [perms.PermRow(slots / "agent.toml", owner, group, file_mode ^ 0o044, role="agent")]
    )
    assert pl2.changed is True


# ── commit / rollback ─────────────────────────────────────────────────────────


def _diff(path: Path, before: perms.PermObservation, owner: str, group: str, mode: int):
    return perms.PermDiff(path=path, before=before, owner=owner, group=group, mode=mode, role="r")


def test_commit_applies_only_drifted_and_records_calls() -> None:
    owner, group = _me()
    chown_calls: list[tuple[str, int, int]] = []
    chmod_calls: list[tuple[str, int]] = []
    # one clean (skipped), one drifted (applied)
    clean = _diff(Path("/a"), _obs(Path("/a"), owner, group, 0o600), owner, group, 0o600)
    dirty = _diff(Path("/b"), _obs(Path("/b"), owner, group, 0o644), owner, group, 0o600)
    pl = perms.OwnershipPlan(diffs=(clean, dirty))
    changed = perms.commit(
        pl,
        chown=lambda p, u, g: chown_calls.append((p, u, g)),
        chmod=lambda p, m: chmod_calls.append((p, m)),
    )
    assert changed == [Path("/b")]
    assert chmod_calls == [("/b", 0o600)]
    assert len(chown_calls) == 1


def test_commit_rolls_back_on_failure() -> None:
    owner, group = _me()
    applied_chmod: list[tuple[str, int]] = []

    def chmod(p: str, m: int) -> None:
        if p == "/second":
            raise PermissionError("boom")
        applied_chmod.append((p, m))

    first = _diff(Path("/first"), _obs(Path("/first"), owner, group, 0o644), owner, group, 0o600)
    second = _diff(Path("/second"), _obs(Path("/second"), owner, group, 0o644), owner, group, 0o600)
    pl = perms.OwnershipPlan(diffs=(first, second))
    with pytest.raises(PermissionError):
        perms.commit(pl, chown=lambda p, u, g: None, chmod=chmod)
    # /first applied (0o600), then /second failed -> /first rolled back to 0o644
    assert applied_chmod == [("/first", 0o600), ("/first", 0o644)]


# ── audit + table smoke ───────────────────────────────────────────────────────


def test_audit_rows_status_vocabulary() -> None:
    owner, group = _me()
    clean = _diff(Path("/a"), _obs(Path("/a"), owner, group, 0o600), owner, group, 0o600)
    dirty = _diff(Path("/b"), _obs(Path("/b"), "root", "root", 0o644), owner, group, 0o600)
    absent = _diff(
        Path("/c"),
        perms.PermObservation(Path("/c"), exists=False, owner=None, group=None, mode=None),
        owner,
        group,
        0o600,
    )
    rows = perms.audit_rows(perms.OwnershipPlan(diffs=(clean, dirty, absent)))
    assert [r["status"] for r in rows] == ["ok", "drift", "absent"]


def test_ownership_table_builds_under_hal0_home(tmp_hal0_home: str) -> None:
    table = perms.ownership_table()
    assert table, "table must not be empty"
    assert all(isinstance(r, perms.PermRow) for r in table)
    home = Path(tmp_hal0_home)
    # every declared path lives under the isolated HAL0_HOME tree
    for row in table:
        assert home in row.target.parents or row.target == home, row.target
    # the config root + slots dir are non-optional anchors
    targets = {r.target for r in table}
    assert paths.etc() in targets
    assert paths.slots_config_dir() in targets


# ── the D hardened-perms flip (service_user != root) ──────────────────────────


def _by_target(table: list[perms.PermRow]) -> dict[Path, perms.PermRow]:
    return {r.target: r for r in table}


def test_root_table_is_unchanged_by_the_flip(tmp_hal0_home: str) -> None:
    """service_user="root" must reproduce the byte-identical root-era table.

    Existing root installs must not move a single bit when the flip code lands.
    """
    rows = _by_target(perms.ownership_table(service_user="root"))
    etc = paths.etc()
    # /etc/hal0 + every mutable seed stays root:root with the legacy modes.
    assert (rows[etc].owner, rows[etc].group, rows[etc].mode) == ("root", "root", 0o755)
    assert rows[paths.hal0_toml()].owner == "root"
    assert rows[paths.hal0_toml()].group == "root"
    slots = rows[paths.slots_config_dir()]
    assert (slots.owner, slots.group, slots.mode) == ("root", "root", 0o755)
    assert slots.child_mode == 0o600
    # agents/ + secrets/ root:root; state root defaults to the literal hal0 acct.
    assert rows[paths.agents_config_dir()].owner == "root"
    assert rows[paths.var_lib() / "secrets"].owner == "root"
    assert rows[paths.var_lib()].owner == "hal0"


def test_flip_makes_etc_hal0_service_owned_and_setgid(tmp_hal0_home: str) -> None:
    """service_user="hal0" hands /etc/hal0 + its mutable contents to the daemon.

    The config root (and slots/) go service-owned + setgid 2775 so the daemon's
    temp-file+rename rewrites work; the flat seed files become service-owned too.
    """
    rows = _by_target(perms.ownership_table(service_user="hal0"))
    etc = paths.etc()
    assert (rows[etc].owner, rows[etc].group, rows[etc].mode) == ("hal0", "hal0", 0o2775)
    # slots dir flips to setgid + service-owned; children keep 0600.
    slots = rows[paths.slots_config_dir()]
    assert (slots.owner, slots.group, slots.mode) == ("hal0", "hal0", 0o2775)
    assert slots.child_mode == 0o600
    # the mutable flat seeds the API rewrites all flip to hal0; modes unchanged.
    for target in (
        paths.hal0_toml(),
        etc / "profiles.toml",
        etc / "api.env",
        etc / "capabilities.toml",
        etc / "upstreams.toml",
        paths.hardware_json(),
        paths.openwebui_env(),
    ):
        assert rows[target].owner == "hal0", target
        assert rows[target].group == "hal0", target
    # mode warts are NOT touched by the flip (ownership only).
    assert rows[paths.hal0_toml()].mode == 0o600
    # api.env is 0640, NOT world-readable: it carries HAL0_ADMIN_KEY /
    # HAL0_CLIENT_KEY (service_identity._KEY_ENV). It used to be seeded 0644,
    # which handed the admin key to every local account AND let
    # `doctor perms --fix` re-widen a file that key rotation had deliberately
    # tightened to 0640 (service_identity._API_ENV_MODE).
    assert rows[etc / "api.env"].mode == 0o640
    assert not rows[etc / "api.env"].mode & 0o007, "api.env must never be world-readable"


def test_flip_keeps_agents_and_secrets_root_owned(tmp_hal0_home: str) -> None:
    """agents/ + secrets/ must stay root:root even under the flip.

    The API only reads agents/; systemd reads the secrets/ EnvironmentFile as
    root before dropping to the service user, so neither may be service-writable.
    """
    rows = _by_target(perms.ownership_table(service_user="hal0"))
    agents = rows[paths.agents_config_dir()]
    assert (agents.owner, agents.group) == ("root", "root")
    secrets = rows[paths.var_lib() / "secrets"]
    assert (secrets.owner, secrets.group) == ("root", "root")


def test_flip_makes_state_root_service_owned(tmp_hal0_home: str) -> None:
    """/var/lib/hal0 + HERMES_HOME flip to the service user under the flip."""
    rows = _by_target(perms.ownership_table(service_user="hal0"))
    state = rows[paths.var_lib()]
    assert (state.owner, state.group, state.mode) == ("hal0", "hal0", 0o2775)
    hermes = rows[paths.var_lib() / ".hermes"]
    assert (hermes.owner, hermes.group, hermes.mode) == ("hal0", "hal0", 0o700)


def test_runtime_slots_and_registry_are_service_owned(tmp_hal0_home: str) -> None:
    """O13: the /var/lib/hal0 runtime slots/ + registry/ trees must be declared.

    install.sh births them root:root; the User=hal0 daemon writes
    ``slots/<id>/state.json`` and the registry into them, so a fresh box that
    only chowned the top-level state root left the slots degrading to ``error``.
    Both must appear as non-optional, setgid, service-owned rows so
    ``doctor perms`` audits AND ``--fix`` heals them.
    """
    rows = _by_target(perms.ownership_table(service_user="hal0"))
    var_lib = paths.var_lib()

    slots = rows[var_lib / "slots"]
    assert (slots.owner, slots.group, slots.mode) == ("hal0", "hal0", 0o2775)
    assert slots.optional is False
    # a pre-existing root-owned per-slot dir is healed via the glob.
    assert slots.glob == "*"
    assert slots.child_mode == 0o2775
    # NB: distinct from the /etc/hal0/slots CONFIG dir row.
    assert (var_lib / "slots") != paths.slots_config_dir()

    registry = rows[var_lib / "registry"]
    assert (registry.owner, registry.group, registry.mode) == ("hal0", "hal0", 0o2775)
    assert registry.optional is False


def test_models_store_row_is_declared_recursive_and_service_owned(tmp_hal0_home: str) -> None:
    """r5-sync-assessment §6.2 (O13 class): models/ needs its own PermRow.

    ``${VAR_DIR}/models`` is born root:root 0755 by the same install.sh mkdir
    as slots/ and registry/, but had no row — ``doctor perms --fix`` could not
    heal it and default-store pulls failed with PermissionError under
    User=hal0. The row must be non-optional, setgid, service-owned, recursive
    (pulls nest ``<model_id>/<file>``), and give files a distinct mode from
    dirs (plain ``open(part, "wb")`` births files 0644, not the dir's 2775).
    """
    rows = _by_target(perms.ownership_table(service_user="hal0"))
    var_lib = paths.var_lib()

    models = rows[var_lib / "models"]
    assert (models.owner, models.group, models.mode) == ("hal0", "hal0", 0o2775)
    assert models.optional is False
    assert models.recursive is True
    assert models.glob == "*"
    assert models.child_mode == 0o2775  # nested per-model dirs
    assert models.child_file_mode == 0o644  # weight files


def test_models_store_row_heals_root_owned_tree(tmp_hal0_home: str) -> None:
    """A root-owned models/ tree (+ a root-owned per-model subdir) plans as drift."""
    var_lib = paths.var_lib()
    models = var_lib / "models"
    (models / "some-model").mkdir(parents=True)

    def _root_observe(p: Path) -> perms.PermObservation:
        return perms.PermObservation(
            path=p, exists=p.exists(), owner="root", group="root", mode=0o755
        )

    rows = [r for r in perms.ownership_table() if r.target == models]
    assert rows, "models/ row must be present in the table"
    pl = perms.plan(rows, observe_fn=_root_observe)
    drifted = {d.path for d in pl.drifted}
    assert models in drifted
    assert models / "some-model" in drifted


def test_runtime_slots_row_heals_root_owned_tree(tmp_hal0_home: str) -> None:
    """O13 heal path: a root-owned runtime slots/ tree is planned as drift.

    Mirrors the fresh-install symptom — slots/ + a per-slot dir land root:root —
    and asserts the table's plan reports both as ``drift`` (so ``commit`` would
    chown them to hal0) rather than silently leaving them unhealed as before.
    """
    var_lib = paths.var_lib()
    slots = var_lib / "slots"
    (slots / "agent").mkdir(parents=True)

    def _root_observe(p: Path) -> perms.PermObservation:
        # Everything the plan touches reads back root:root 0755 (the install-time
        # birth state) so the runtime-slots rows register as drift.
        return perms.PermObservation(
            path=p, exists=p.exists(), owner="root", group="root", mode=0o755
        )

    rows = [r for r in perms.ownership_table() if r.target == slots]
    assert rows, "runtime slots row must be present in the table"
    pl = perms.plan(rows, observe_fn=_root_observe)
    drifted = {d.path for d in pl.drifted}
    assert slots in drifted
    assert slots / "agent" in drifted  # the glob expanded onto the per-slot dir


# ── O13 follow-up: recursion into slots/<id>/state.json (r4-stage-validation) ─


def test_runtime_slots_row_is_recursive_with_distinct_file_mode(tmp_hal0_home: str) -> None:
    """The declared slots/ row must recurse and give files their own mode.

    Guards the exact regression the O13 follow-up finding described: a bare
    single-level glob heals ``slots/<id>/`` but never reaches
    ``slots/<id>/state.json`` one level deeper.
    """
    rows = [r for r in perms.ownership_table() if r.target == paths.var_lib() / "slots"]
    assert len(rows) == 1
    row = rows[0]
    assert row.recursive is True
    assert row.glob == "*"
    assert row.child_mode == 0o2775  # dirs
    assert row.child_file_mode == 0o600  # files — matches write_state_atomic's mkstemp birth mode


def test_nested_state_json_two_levels_deep_plans_as_drift_and_heals(tmp_hal0_home: str) -> None:
    """A root-owned ``slots/<id>/state.json`` two levels deep is audited AND fixed.

    This is the concrete O13 follow-up repro: the per-slot dir is already
    healed by the shallow glob, but a root-owned ``state.json`` inside it (left
    over from a root-run install/reinstall, before the hal0 daemon ever wrote
    through it) was previously invisible to both ``plan()`` and ``commit()``.

    ``commit()`` resolves owner/group names to real uid/gid via pwd/grp (only
    the chown/chmod syscalls themselves are seamed), so this row is built
    directly with the CURRENT test user/group — matching the ``_me()`` pattern
    the other commit tests in this file use — decoupling the recursion/heal
    mechanics under test here from ``ownership_table()``'s service-user flip
    logic (that wiring is covered separately by
    ``test_runtime_slots_row_is_recursive_with_distinct_file_mode``). This also
    keeps the test valid when run as root, where ``service_user="root"``
    would otherwise take the byte-identical root-era table path.
    """
    owner, group = _me()
    var_lib = paths.var_lib()
    slots = var_lib / "slots"
    slot_dir = slots / "agent"
    slot_dir.mkdir(parents=True)
    state_file = slot_dir / "state.json"
    state_file.write_text('{"state": "error"}\n')
    os.chmod(state_file, 0o644)  # simulate a root-born file (mode aside from owner)

    row = perms.PermRow(
        slots,
        owner,
        group,
        0o2775,
        glob="*",
        child_mode=0o2775,
        child_file_mode=0o600,
        recursive=True,
        optional=False,
        role="slots/ (test)",
    )

    def _root_observe(p: Path) -> perms.PermObservation:
        return perms.PermObservation(
            path=p, exists=p.exists(), owner="root", group="root", mode=0o755
        )

    pl = perms.plan([row], observe_fn=_root_observe)
    drifted = {d.path for d in pl.drifted}
    assert slots in drifted
    assert slot_dir in drifted
    assert state_file in drifted, "nested state.json must be reachable by plan(), not just the dir"

    # confirm the declared target for the file diff is the FILE mode, not the dir mode
    file_diff = next(d for d in pl.diffs if d.path == state_file)
    assert file_diff.mode == 0o600
    assert file_diff.owner == owner
    assert file_diff.group == group

    # --fix: commit() must actually chown/chmod the nested file, not just the dir.
    chown_calls: list[tuple[str, int, int]] = []
    chmod_calls: list[tuple[str, int]] = []
    changed = perms.commit(
        pl,
        chown=lambda p, u, g: chown_calls.append((p, u, g)),
        chmod=lambda p, m: chmod_calls.append((p, m)),
    )
    assert state_file in changed
    assert slot_dir in changed
    assert slots in changed
    assert (str(state_file), 0o600) in chmod_calls


def test_recursive_glob_does_not_alter_non_recursive_rows(tmp_hal0_home: str) -> None:
    """Auditing the recursion feature: every OTHER glob row stays single-level.

    ``recursive`` defaults False and only the slots/ + models/ rows opt in
    (both nest per-item state one level below the glob'd dir) — this locks
    that in so future edits don't silently widen the lock-file / .hermes /
    secrets/agents / benchmarks glob rows to walk nested subdirectories.
    """
    table = perms.ownership_table(service_user="hal0")
    _recursive_targets = {paths.var_lib() / "slots", paths.var_lib() / "models"}
    non_recursive_glob_rows = [
        r for r in table if r.glob is not None and r.target not in _recursive_targets
    ]
    assert non_recursive_glob_rows, "expected at least one non-recursive glob row to audit"
    for row in non_recursive_glob_rows:
        assert row.recursive is False, row.label
        assert row.child_file_mode is None, row.label


def test_lock_file_rows_unchanged_by_recursion_feature(tmp_hal0_home: str) -> None:
    """Regression: the etc/ + var_lib *.lock rows keep their exact pre-change shape."""
    table = perms.ownership_table(service_user="hal0")
    etc = paths.etc()
    var_lib = paths.var_lib()

    etc_lock_rows = [r for r in table if r.target == etc and r.glob == "*.lock"]
    assert len(etc_lock_rows) == 1
    assert etc_lock_rows[0].child_mode == 0o664
    assert etc_lock_rows[0].recursive is False

    var_lib_lock_rows = [r for r in table if r.target == var_lib and r.glob == "*.lock"]
    assert len(var_lib_lock_rows) == 1
    assert var_lib_lock_rows[0].child_mode == 0o664
    assert var_lib_lock_rows[0].recursive is False


def test_hermes_home_row_unchanged_by_recursion_feature(tmp_hal0_home: str) -> None:
    """Regression: the .hermes row (no glob at all) is untouched."""
    table = perms.ownership_table(service_user="hal0")
    hermes_rows = [r for r in table if r.target == paths.var_lib() / ".hermes"]
    assert len(hermes_rows) == 1
    row = hermes_rows[0]
    assert (row.owner, row.group, row.mode) == ("hal0", "hal0", 0o700)
    assert row.glob is None
    assert row.recursive is False
    assert row.child_file_mode is None


def test_registry_files_get_explicit_rows_matching_each_writer(tmp_hal0_home: str) -> None:
    """registry/ is flat: its 3 known files get explicit rows, not a recursive glob.

    Each mode matches that file's actual writer (see the comment above the
    registry rows in ownership_table): registry.toml born via the same
    tempfile.mkstemp atomic-write path as hal0.toml (0600); registry.toml.lock
    matches the *.lock cross-process-shared convention (0664); hal0.db matches
    sqlite3.connect's birth mode (0644).
    """
    table = perms.ownership_table(service_user="hal0")
    by_target = {r.target: r for r in table}
    var_lib = paths.var_lib()

    toml_row = by_target[var_lib / "registry" / "registry.toml"]
    assert (toml_row.owner, toml_row.group, toml_row.mode) == ("hal0", "hal0", 0o600)

    lock_row = by_target[var_lib / "registry" / "registry.toml.lock"]
    assert (lock_row.owner, lock_row.group, lock_row.mode) == ("hal0", "hal0", 0o664)

    db_row = by_target[var_lib / "registry" / "hal0.db"]
    assert (db_row.owner, db_row.group, db_row.mode) == ("hal0", "hal0", 0o644)

    # all optional: these files don't exist until the registry is first used.
    assert toml_row.optional is True
    assert lock_row.optional is True
    assert db_row.optional is True


def test_ownership_table_has_no_rootless_podman_home_rows(tmp_hal0_home: str) -> None:
    """O12: the 9e07c0d3 ``.config``/``.local`` rootless-HOME rows are gone.

    They papered over the WRONG-context symptom (hal0's rootless podman
    erroring on a root-owned HOME dir) instead of the actual finding: slot
    introspection must route through root's podman store via the
    hal0-podman-ro seam (see hal0.providers.podman_introspect), not hal0's
    own rootless store at all. Every OTHER var_lib row — especially the
    *.lock advisory-lock rows — must be untouched by the removal.
    """
    table = perms.ownership_table(service_user="hal0")
    var_lib = paths.var_lib()
    targets = {r.target for r in table}
    assert var_lib / ".config" not in targets
    assert var_lib / ".local" not in targets
    # lock-file rows survive untouched.
    assert var_lib / ".first-run.lock" in targets
    lock_rows = [r for r in table if r.target == var_lib and r.glob == "*.lock"]
    assert len(lock_rows) == 1
    assert lock_rows[0].child_mode == 0o664
    # HERMES_HOME and the rest of the state-root rows are untouched.
    assert var_lib / ".hermes" in targets
    assert var_lib / "agents" in targets
    assert var_lib / "secrets" in targets


def test_flip_honors_custom_service_group(tmp_hal0_home: str) -> None:
    """A non-default service_group threads through the service-owned rows."""
    rows = _by_target(perms.ownership_table(service_user="svc", service_group="svcgrp"))
    etc = paths.etc()
    assert (rows[etc].owner, rows[etc].group) == ("svc", "svcgrp")
    assert rows[paths.var_lib()].group == "svcgrp"
    # pinned-root rows ignore the service group.
    assert rows[paths.agents_config_dir()].group == "root"
