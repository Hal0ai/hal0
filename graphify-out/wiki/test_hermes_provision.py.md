# test_hermes_provision.py

> 111 nodes · cohesion 0.04

## Key Concepts

- **test_hermes_provision.py** (117 connections) — `tests/agents/test_hermes_provision.py`
- **Path** (56 connections)
- **InstallIO** (48 connections) — `src/hal0/agents/hermes_provision.py`
- **MonkeyPatch** (47 connections)
- **_patch_dropin_to_tmp()** (12 connections) — `tests/agents/test_hermes_provision.py`
- **fake_hermes_run()** (8 connections) — `tests/agents/_hermes_fakes.py`
- **_brain_profile_state()** (6 connections) — `tests/agents/test_hermes_provision.py`
- **test_config_write_records_fallbacks_for_placeholder_primary_and_default_mcp()** (6 connections) — `tests/agents/test_hermes_provision.py`
- **test_config_write_phase_applies_overrides()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_config_write_phase_writes_yaml_idempotently()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_config_write_records_no_fallbacks_when_inputs_live()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_config_write_renders_role_slots_from_live_state()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_failed_step_surfaces_and_blocks_ok()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_gateway_secrets_wire_routes_through_seam_non_root()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_install_phase_installs_canonical_wrapper()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_preflight_fails_when_hermes_home_unwritable_but_var_lib_ok()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_voice_wire_does_not_provision_stt_when_npu_anchor_is_not_ready()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_voice_wire_finds_local_tts_and_transcription_slots()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_voice_wire_provisions_stt_for_npu_trio_facade()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_write_gateway_secrets_dropin_routes_through_seam_non_root()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_bootstrap_cli_dry_run_skips_report()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_bootstrap_cli_returns_one_on_failure()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_bootstrap_cli_returns_zero_on_success()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_context_link_falls_back_when_soul_render_fails()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_context_link_renders_all_three_files()** (4 connections) — `tests/agents/test_hermes_provision.py`
- *... and 86 more nodes in this community*

## Relationships

- [_Runner](_Runner.md) (17 shared connections)
- [_build_overlay_keys](_build_overlay_keys.md) (10 shared connections)
- [hermes_provision.py](hermes_provision.py.md) (7 shared connections)
- [test_hermes_provision_idempotency.py](test_hermes_provision_idempotency.py.md) (7 shared connections)
- [_hermes_fakes.py](_hermes_fakes.py.md) (5 shared connections)
- [_version_pin_for](_version_pin_for.md) (4 shared connections)
- [install_hermes](install_hermes.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`
- `tests/agents/_hermes_fakes.py`
- `tests/agents/test_hermes_provision.py`

## Audit Trail

- EXTRACTED: 555 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*