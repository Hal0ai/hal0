# BadRequest

> God node · 107 connections · `src/hal0/errors.py`

**Community:** [BadRequest](BadRequest.md)

## Connections by Relation

### calls
- _read_body() `INFERRED`
- .apply() `INFERRED`
- import_stack() `INFERRED`
- set_memory_provider() `INFERRED`
- _forward_multipart() `INFERRED`
- memory_add() `INFERRED`
- memory_recall() `INFERRED`
- import_stack_route() `INFERRED`
- commit_update() `INFERRED`
- resolve() `EXTRACTED`
- import_profile() `INFERRED`
- update_agent_persona() `INFERRED`
- delete_bank() `INFERRED`
- swap_slot() `INFERRED`
- create_chat_template() `INFERRED`
- set_model_store() `INFERRED`
- create_slot() `INFERRED`
- update_slot_config() `INFERRED`
- update_slot_defaults() `INFERRED`
- _registry() `INFERRED`

### contains
- [errors.py](errors.py.md) `EXTRACTED`

### indirect_call
- .test_too_new_schema_rejected() `INFERRED`
- test_next_free_slot_port_pool_capped_below_comfyui() `INFERRED`
- .test_too_new_schema_rejected() `INFERRED`
- test_screen_extra_args_json_rejects_shell_stripped_json() `INFERRED`
- test_next_free_slot_port_exhausted_configured_range_raises() `INFERRED`
- test_validate_model_fit_blocks_profile_unsupported_slot_type() `INFERRED`
- test_validate_model_fit_blocks_wrong_model_class() `INFERRED`
- test_validate_id_rejects_path_traversal() `INFERRED`
- .test_bad_envelope_rejected() `INFERRED`
- test_ngl_in_extra_args_is_denied() `INFERRED`
- test_duplicate_same_id_raises() `INFERRED`
- test_resolve_argv_screens_model_extra_args_segment() `INFERRED`
- test_install_rejects_bad_id_charset() `INFERRED`
- test_resolve_blocks_link_local_url() `INFERRED`
- test_resolve_blocks_localhost_url() `INFERRED`
- test_resolve_blocks_mdns_local_hostname() `INFERRED`
- test_resolve_blocks_private_lan_url() `INFERRED`
- test_resolve_argv_rejects_managed_flag_in_extra_args() `INFERRED`
- test_resolve_argv_rejects_multiple_managed_flags_in_one_extra_args() `INFERRED`
- test_resolve_propagates_fetch_failure_as_bad_request() `INFERRED`

### inherits
- [Hal0Error](Hal0Error.md) `EXTRACTED`
- SsrfBlockedError `EXTRACTED`

### rationale_for
- 400 — the request was syntactically or semantically invalid.      Use this for c `EXTRACTED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*