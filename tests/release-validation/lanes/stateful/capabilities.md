# Lane: capabilities (stateful, order 5)

Capability slots, the model registry, and profiles — the configuration surface a user actually
drives when setting hal0 up for their hardware.

## Checks

Record `systemctl --failed` verbatim at lane entry and lane exit and diff them. On a box with no
snapshot, residue accumulates anonymously across a serialised run unless each lane bounds its own.

1. **Baseline, cross-checked against the advertised backends.** `hal0 capabilities --help` and
   `list`, plus `GET /api/capabilities`. Then assert that every device named in the shipped
   `/etc/hal0/capabilities.toml` appears in `/api/capabilities` `.backends` **and** in the
   catalog rows the picker actually offers for that child. The seed copying a GPU device out of
   the shipped slot TOML is by-design (`known-issues.yaml:
   capabilities-seed-devices-from-slot-tomls`); a default selection naming a backend the host
   never advertises, or one no catalog row can match, is a hard fail — that is where the seed
   costs the user something.
2. **Enable a capability against the real hardware.** Bind the embedding model to the embed
   capability on the backend this box actually has, and enable it. Does the flip from the seeded
   default work cleanly? Does enabling load or require the slot? Verify through both
   `capabilities list` and the API.
3. **Same for the rerank child**, then verify the path that consumes it: the memory reranker
   config (`[memory.embedding] rerank_model`, `rerank_gateway_url`). Issue a rerank through the
   gateway with a query and three documents. In rc.4 this whole path was dead because profile
   flags never reached argv (#1787).
4. **Model defaults.** Promote a model to default for its type. Verify `hal0 model list` marks it
   with the `*` in the unlabelled first column. Then probe dispatch with all three client shapes
   — omitted `model`, `model: "default"`, and the explicit id — because only the third normally
   gets tested and it is the one that hides the behaviour. The marker NOT steering the dispatcher
   is by-design (`known-issues.yaml: model-default-marker-is-per-type-fallback`); what is
   reportable is a model-less completion 404ing on a box whose `agent` anchor slot HAS a model
   bound.
5. **Registry mutations.** `hal0 model add` on copies of a gguf staged into a scratch directory
   (copy, never move, and never touch the shared store's originals):
   * Is the capability type auto-detected correctly? Register the **same file twice under two
     different names** — the upstream filename and a neutral one. Byte-identical input yielding
     two different types is the shortest possible proof of a detection bug and removes all
     argument about whether the file is unusual (#1838).
   * Register the same bytes by two different PATHS as well (`hal0 model add` versus the
     install-time auto-scan) and diff the resulting ids and display names.
   * Feed it a file with a `.gguf` name and no GGUF magic. Accepted as `chat` is a finding.
   * Try one add from a directory the `hal0` service user cannot read (e.g. under `/root`) — the
     first place an operator puts a downloaded gguf. The refusal is correct; an error that
     cannot distinguish "unreadable" from "missing" is not.
   * Then `hal0 model rm <id>`: does it remove the registry entry, the file, or both — and does
     it *ask*? Assert the file survives and that `hal0 model rm <id> < /dev/null` aborts rather
     than proceeding unprompted. A remove that silently deletes files from a shared store is a
     `critical` finding. Clean up the scratch directory afterwards.
6. **Profiles.** `hal0 profile` exists as of rc.5. Inspect what a profile contains, confirm the
   API's view matches what a loaded slot actually got (the same seam as #1787 from the
   configuration side), and diff `hal0 profile show <name>` field by field against both
   `GET /api/profiles/<name>` and the matching row of `hal0 profile list` — the detail view
   already drops `used_by`. Do not destructively rewrite the brain profile; use a throwaway.
7. **Registry consistency after all of the above.** Re-list models and slots. Orphans, dangling
   references, stale capability bindings?

## Leave behind

Capabilities left enabled on the backend this box supports; defaults set; scratch model files
and throwaway profiles removed. Brain and utility slots healthy.
