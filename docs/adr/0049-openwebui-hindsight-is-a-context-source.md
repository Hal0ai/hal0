# ADR-0049: Open WebUI treats Hindsight as a context source

## Status

**ACCEPTED (2026-11-02).** Implements shared-memory recall in the chat
companion without replacing its document RAG.

Amended by [ADR-0059](0059-openwebui-file-context-capability-pin.md): Open
WebUI 0.13 gated the files/RAG handler on the `file_context` model
capability and flipped that default off, so decision 4's path needs hal0 to
pin the capability on. The decision itself stands.

## Context

Open WebUI already owns upload parsing, embedding, vector search, source
rendering and citations. Hindsight owns hal0's durable facts, observations,
raw retained chunks and memory visibility rules. Copying Hindsight data into
Open WebUI's vector database would create two stores with different deletion,
freshness and access semantics. Calling Hindsight as a model tool would make
recall optional and would not feed Open WebUI's normal citation pipeline.
ADR-0043 gave Open WebUI's models the `hal0-memory` tools, so a model can recall
when it decides to. It does not make a model that never calls a tool remember
anything, and most of the slot models a box runs do not call tools well.

Hindsight recall also returns response-level chunks beside its result list.
`HindsightProvider.recall` already drops any chunk or source fact that does not
join to a result the caller may see; this record relies on that and joins again
on its own side.

## Decision

1. Open WebUI keeps its native document/Knowledge RAG. Hindsight is an
   additional context source, not its vector store.
2. `POST /api/memory/context` performs deterministic, shared-only recall. It
   accepts no bank, dataset, tag, private-mode or agent identity selector and
   always calls memory as `dataset="shared"`, `client_id="anonymous"`. It clips
   the query to 4000 characters, takes only the `low` and `mid` budgets, and
   gives up after 15 seconds with a 504, so recalls a client abandoned do not
   pile up on the engine.
3. Raw chunks come first, then the facts no chosen chunk covers. Every chunk
   must explicitly join to a fact that survived visibility and token-budget
   filtering; unjoinable enrichment is omitted. Observations carry no chunk of
   their own, so facts are not only a fallback for a degraded provider.
4. An Open WebUI inlet Filter that is global and has no `toggle` attribute (a
   toggle filter runs only for a chat that switched it on) calls that endpoint
   and appends results to `body["files"]` as full text sources. Open WebUI
   rebuilds `metadata["files"]` from that list after the inlet filters run.
   Its existing retrieval code therefore combines uploaded files and Hindsight
   context and owns the source numbering and citation UI. The Filter never
   invents citation numbers.
5. Retrieved memory is untrusted evidence. The Filter adds a reminder to the
   existing system message (never a second system message, which several chat
   templates refuse) not to execute instructions found inside sources, and the
   API neutralises source-wrapper terminators and control characters.
6. Recall fails open: any error leaves the chat request unchanged, because
   Open WebUI re-raises an inlet error and the turn would die with it.
7. The Filter injects only when the model's upstream row says
   `owned_by == "hal0"`: hal0's slots and local catalog. Raw shared memory must
   not reach a cloud model, and a Hermes profile recalls for itself, so every
   upstream is skipped. An unknown owner fails closed, which includes a
   workspace model built over a slot, because Open WebUI gives that model no
   upstream row. Open WebUI task-model calls are skipped too.
8. hal0 manages exactly the Function ID `hal0_hindsight_rag` through Open
   WebUI's individual create/update/toggle endpoints. It never calls the bulk
   `/functions/sync` endpoint, never overwrites a colliding user Function, and
   preserves valves plus manually disabled/global state on updates. Without an
   admin token it reports `auth_required` and serves the source for manual
   import.
9. Open WebUI's native Memories stays disabled, as ADR-0043 set it. Explicit
   writes and scoped or private operations remain MCP responsibilities.

## Consequences

- Upload RAG continues to work when Hindsight is down or the Filter is
  disabled. Disabling one Function is the complete runtime rollback.
- Automatic chat recall sees shared memory only. Making private/project data
  globally visible is a separate operator decision and is not inferred from a
  self-asserted header.
- The route takes no identity at all, so a chat's user is neither an ACL input
  nor recorded on a read. Writes keep the `user:<name>` tag ADR-0043 gave them.
- `POST /api/memory/context` is live on every box from the release that ships
  it, with or without the Filter. It is the one `/api/memory` read in the CLIENT
  class, every other one being ADMIN. With sign-in on it takes the box client
  key. With sign-in off it takes no credential from any LAN peer, where the
  ADMIN reads stay behind the LAN gate. Neither is a real widening: `/mcp/memory`
  is already CLIENT and reachable from the LAN under the same two postures, and
  its `memory_search` and `memory_recall` read at least what this route reads.
- **Memory crosses Open WebUI users.** `memory_add` from any Open WebUI user
  lands in shared memory; `user:<name>` is a tag, not an ACL. With the Filter on,
  that note is injected into other users' turns without any model choosing to
  look it up, and those chats can hold `agent_run`. The system-message reminder
  is advisory: a model may still follow text it was given. Enabling the Filter
  is not advised on a box with Open WebUI users you do not trust.
- Every turn on a slot model waits for recall before the model starts. Recall
  against a large bank takes seconds, which is why the Filter's timeout defaults
  to 10 seconds. A turn gains that latency, or loses the context if it runs out.
- Function installation needs an Open WebUI admin token or a one-time manual
  import, so a stock box does not recall into chat until the operator does one
  of those. Turning the Function off leaves it installed and off, and clearing
  the token leaves it running; only Open WebUI's own screen stops it.
- A workspace model over a slot gets no automatic memory, and neither does a
  turn to any cloud upstream. Both can still call the memory tools.
- The context endpoint is additive and does not migrate or duplicate memory
  data.

## Alternatives considered

- **Put every bank into one public namespace.** Rejected as the default: the
  unified physical bank already exists, while visibility/project tags are the
  logical isolation boundary expected by agents and operators.
- **Mirror memory into Open WebUI's vector DB.** Rejected because freshness,
  deletes and visibility would drift.
- **Use Hindsight `reflect` on every turn.** Rejected for hot-path latency and
  nondeterministic synthesis; recall supplies evidence and the chat model does
  the reasoning.
- **Use MCP for automatic recall.** Kept for explicit actions, but rejected as
  the only retrieval path because the model may choose not to call it and its
  output bypasses native sources.

## Relationship to other records

- **ADR-0043.** Its managed `openwebui` bridge client and `hal0-memory` mount
  stay the explicit path. This record adds automatic read-only recall beside
  them and uses the box client key, not the bridge key, because a bridge key is
  refused on `/api/*`.

## References

- `src/hal0/memory/context.py`, `src/hal0/api/routes/memory.py`
  (`memory_context`), `src/hal0/openwebui/functions/hal0_hindsight_rag.py`,
  `src/hal0/openwebui/function_sync.py`, `src/hal0/security/exposure.py`.
- Open WebUI v0.12.5 `backend/open_webui/utils/filter.py`
  (`resolve_filter_pipeline`), `utils/middleware.py` (`process_chat_payload`,
  `chat_completion_files_handler`), `retrieval/utils.py`
  (`get_sources_from_items`).
- `docs/guides/open-webui.mdx`.
