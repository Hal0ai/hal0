# test_doctor.py

> 70 nodes

## Key Concepts

- **test_doctor.py** (32 connections) — `tests/cli/test_doctor.py`
- **MonkeyPatch** (22 connections)
- **Path** (21 connections)
- **_exit_code()** (15 connections) — `tests/cli/test_doctor.py`
- **doctor()** (13 connections) — `src/hal0/cli/doctor_commands.py`
- **_locate_preflight()** (12 connections) — `src/hal0/cli/doctor_commands.py`
- **CaptureFixture** (10 connections)
- **_install_mock_httpx()** (10 connections) — `tests/cli/test_doctor.py`
- **_make_stub()** (9 connections) — `tests/cli/test_doctor.py`
- **_fake_ctx()** (9 connections) — `tests/cli/test_doctor.py`
- **test_doctor_success_propagates_exit_code()** (9 connections) — `tests/cli/test_doctor.py`
- **test_doctor_failure_propagates_exit_code()** (9 connections) — `tests/cli/test_doctor.py`
- **test_doctor_forwards_plain_flag()** (9 connections) — `tests/cli/test_doctor.py`
- **test_doctor_forwards_ports_option()** (9 connections) — `tests/cli/test_doctor.py`
- **test_doctor_sets_ports_soft_env()** (9 connections) — `tests/cli/test_doctor.py`
- **test_toolbox_pull_reports_ok_when_all_images_reachable()** (9 connections) — `tests/cli/test_doctor.py`
- **test_toolbox_pull_surfaces_digest_drift_without_failing()** (9 connections) — `tests/cli/test_doctor.py`
- **test_toolbox_pull_exits_nonzero_when_image_unreachable()** (9 connections) — `tests/cli/test_doctor.py`
- **test_toolbox_pull_skips_non_ghcr_refs_with_clear_error()** (9 connections) — `tests/cli/test_doctor.py`
- **_write_manifest()** (8 connections) — `tests/cli/test_doctor.py`
- **test_toolbox_pull_exits_2_when_manifest_empty()** (8 connections) — `tests/cli/test_doctor.py`
- **_patch_non_editable_hal0()** (7 connections) — `tests/cli/test_doctor.py`
- **test_toolbox_pull_invoked_subcommand_skips_preflight()** (7 connections) — `tests/cli/test_doctor.py`
- **test_doctor_missing_script_exits_2()** (6 connections) — `tests/cli/test_doctor.py`
- **test_locate_preflight_fhs_root_env()** (6 connections) — `tests/cli/test_doctor.py`
- *... and 45 more nodes in this community*

## Relationships

- [doctor_commands.py](doctor_commands.py.md) (8 shared connections)
- [Check](Check.md) (1 shared connections)
- [test_doctor_verify.py](test_doctor_verify.py.md) (1 shared connections)
- [hal0.sh](hal0.sh.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [socket](socket.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_commands.py`
- `tests/cli/test_doctor.py`

## Audit Trail

- EXTRACTED: 316 (90%)
- INFERRED: 37 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*