# ADR-0059: hal0 pins Open WebUI's `file_context` capability on

## Status

**ACCEPTED (2026-11-10).** Amends the file/RAG path assumption of ADR-0049
decision 4. Ships with the Open WebUI v0.13.3 image pin.

## Context

Open WebUI 0.13 changed what a chat turn does with its files, and 0.13.1
changed the default. The files/RAG handler — the code that turns
`metadata["files"]` into retrieved context in the prompt and into the source
list the chat UI renders — is gated on a per-model capability:

```python
def is_file_context_enabled(model: dict, metadata: dict) -> bool:
    # Calls without a session get no file tools, so they need the file text.
    return (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get(
        'file_context',
        metadata.get('params', {}).get('function_calling') == 'legacy'
        or not metadata.get('session_id'),
    )
```

The fallback is off for the ordinary case: a web chat with native function
calling and a session id evaluates to `False`. Upstream 0.12.5 always ran the
handler (`…get('file_context', True)`), and so did 0.13.0 (the capability
existed there, defaulting to on). 0.13.1 replaced the constant default with
the session-aware one, so this is a default flip, not a new feature.

hal0's Hindsight Filter (ADR-0049 decision 4) injects the recalled memory by
appending full-text sources to `body["files"]`; Open WebUI rebuilds
`metadata["files"]` from that list after the inlet filters run, and its
retrieval code then turns those entries into context and citations. Under
0.13.x the whole handler — the exact block that consumes those entries — is
skipped, so the injected Hindsight context never reaches the model. The chat
still works, the Filter still runs, the memory is still fetched and still
charged for: it is simply dropped before the model sees it. That is the worst
failure shape for this feature, because it looks like a model that forgot
rather than an error.

Upstream's own answer to "which models get the file context" is the global
model-defaults metadata, which the admin UI edits under **Admin → Models →
Model Defaults** and which the backend reads from the `DEFAULT_MODEL_METADATA`
environment variable. The backend merges it into every model: models with no
`info` get `meta` copied from it, and models with their own metadata get
`capabilities` merged with the per-model values winning. Upstream's frontend
default for the capability is `undefined` — unset, so upstream's own fallback
applies.

## Decision

1. **hal0 ships `DEFAULT_MODEL_METADATA={"capabilities":{"file_context":true}}`
   in the Open WebUI environment file.** It is a shipped default in
   `src/hal0/openwebui/env_writer.py`, written verbatim (no spaces, one
   line), so podman's `--env-file` and systemd's `EnvironmentFile=` both hand
   Open WebUI the same JSON.

2. **The pin restores 0.12.5's behavior; it does not invent one.** It is
   upstream's own key, in upstream's own shape, read from upstream's own
   environment variable. The capability the operator can see and change is
   the same one upstream's admin UI edits.

3. **A per-model capability still wins.** Upstream merges the defaults as
   `{**defaults, **existing}`, so a model whose own metadata sets
   `file_context` (workspace model, or admin-panel edit) overrides the
   global default. hal0 does not override that.

4. **The pin lives in the environment, not in a database write.** hal0 runs
   Open WebUI with `ENABLE_PERSISTENT_CONFIG=False`, so `Config.get()` for a
   non-persistent key returns the shipped default and no row is written or
   read. The environment is the one authority; a boot-time value cannot
   drift from the file hal0 renders.

5. **The operator can still turn it off.** The key is a shipped default, not
   a hal0-managed one, so a hand edit to it survives every re-render (the
   "one owner per fact" rule in `CONTRIBUTING.md` applies: hal0 states the
   default, the operator may override it, and the file records which is
   which).

## Consequences

- Hindsight recall keeps reaching the model on Open WebUI 0.13.x, and so
  does the document RAG path that shares the handler.
- The Open WebUI **Admin → Models → Model Defaults** panel can overwrite the
  environment-derived default in memory: upstream's `Config.upsert` writes
  non-persistent keys into `Config.DEFAULTS`, which takes precedence over
  the shipped default until the container restarts. An operator who saves
  that panel without `file_context` set will turn the recall path off until
  the next restart. hal0 does not fight the panel.
- The capability also re-enables the classic file-context behavior upstream
  moved behind the gate: the builtin file tools (`list_chat_files`,
  `query_chat_files`, `view_file`, …) are suppressed while the capability is
  on, and the attached-knowledge injection is skipped. That is the 0.12.5
  behavior hal0's recall path was built against.
- Open WebUI 0.13 also changed retrieval defaults (`RAG_TOP_K` and
  `RAG_TOP_K_RERANKER` 3 → 10, `CHUNK_SIZE` 1000 → 1024). hal0's RAG env
  block does not set those keys, so document retrieval on 0.13.x returns more
  and larger chunks than on 0.12.5. That is upstream's change, surfaced to
  operators in the release notes rather than pinned by hal0.
- The value is JSON in an environment file, so it is written verbatim and
  never quoted. hal0's env tests parse the rendered file with ports of both
  podman's and systemd's parsers to prove the JSON survives both.

## Alternatives considered

- **Document the capability and let the operator set it.** Rejected: it makes
  the recall path broken by default on every fresh 0.13 box, and the failure
  is silent. A feature that only works after a manual admin-panel edit is not
  a feature the platform ships.
- **Write `models.default_metadata` into Open WebUI's config database.**
  Rejected: hal0 runs Open WebUI with persistent config off, so the row
  would be ignored on the next boot and the effective value would depend on
  boot order. The environment has no such race.
- **Set the capability per model through hal0's own API.** Rejected: hal0
  does not own Open WebUI's model table, and a workspace model over a slot
  has no upstream row to attach a capability to (ADR-0049 decision 7 already
  notes that such a model gets no automatic memory at all).
- **Keep the memory out of the files path entirely — inject the context as a
  system or user message.** Rejected: it would give up Open WebUI's source
  numbering and citation UI for memory, which is the point of ADR-0049
  decision 4, and it would put recalled text in a place the model treats as
  instructions rather than as evidence.
- **Pin Open WebUI to 0.12.5 indefinitely.** Rejected: 0.13.0 carries
  security fixes, and holding an old image to preserve one code path is a
  worse trade than pinning one capability.

## Relationship to other records

- **ADR-0049.** Its decision (Hindsight is a context source through
  Open WebUI's native files/RAG path) stands. What 0.13 changed is the
  precondition that path needed, so this record pins that precondition.
- **ADR-0043.** The explicit `hal0-memory` MCP tools are unaffected: they
  do not go through the files handler.

## References

- `src/hal0/openwebui/env_writer.py` (`_DEFAULT_OPENWEBUI_ENV`),
  `tests/openwebui/test_env_file_context.py`.
- Open WebUI v0.13.3 `backend/open_webui/utils/tools.py`
  (`is_file_context_enabled`), `utils/middleware.py`
  (`process_chat_payload`), `utils/models.py` (`get_all_models`),
  `config.py` (`DEFAULT_MODEL_METADATA`), `models/config.py` (`Config`).
- Open WebUI v0.12.5 `backend/open_webui/utils/middleware.py:2963` (the
  unconditional handler).
- `docs/guides/open-webui.mdx`.
