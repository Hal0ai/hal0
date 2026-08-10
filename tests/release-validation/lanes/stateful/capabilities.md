# Lane: capabilities (stateful, order 5)

Capability slots, the model registry, and profiles — the configuration surface a user actually
drives when setting hal0 up for their hardware.

## Checks

1. **Baseline.** `hal0 capabilities --help` and `list`, plus `GET /api/capabilities`. Record
   which capabilities ship enabled and on which backend. A CPU-only box that ships capabilities
   bound to a GPU backend is a finding in itself.
2. **Enable a capability against the real hardware.** Bind the embedding model to the embed
   capability on the backend this box actually has, and enable it. Does the flip from the seeded
   default work cleanly? Does enabling load or require the slot? Verify through both
   `capabilities list` and the API.
3. **Same for the rerank child**, then verify the path that consumes it: the memory reranker
   config (`[memory.embedding] rerank_model`, `rerank_gateway_url`). Issue a rerank through the
   gateway with a query and three documents. In rc.4 this whole path was dead because profile
   flags never reached argv (#1787).
4. **Model defaults.** Promote a model to default for its type. Verify `hal0 model list` marks
   it (rc.4 had no default marker at all) and that a chat completion with no `model` field, or
   `model=default`, uses it.
5. **Registry mutations.** `hal0 model add` on a copy of a gguf staged into a scratch directory
   (copy, never move, and never touch the shared store's originals):
   * Is the capability type auto-detected correctly? rc.4 misclassified a reranker as `chat`.
   * Is the id derived sensibly? rc.4 derived it from an arch header, producing
     `jina-bert-implementation`.
   * Then `hal0 model rm <id>`: does it remove the registry entry, the file, or both — and does
     it *ask*? A remove that silently deletes files from a shared store is a `critical` finding.
     Clean up the scratch directory afterwards.
6. **Profiles.** rc.4 shipped 17 server-side profiles with a full CRUD API and **no CLI**.
   Check whether `hal0 profile` now exists; if not, that gap stands. Inspect what a profile
   contains and confirm the API's view matches what a loaded slot actually got — this is the
   same seam as #1787, from the configuration side. Do not destructively rewrite the brain
   profile; use a throwaway.
7. **Registry consistency after all of the above.** Re-list models and slots. Orphans, dangling
   references, stale capability bindings?

## Leave behind

Capabilities left enabled on the backend this box supports; defaults set; scratch model files
and throwaway profiles removed. Brain and utility slots healthy.
