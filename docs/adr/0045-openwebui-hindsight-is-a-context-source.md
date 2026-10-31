# ADR-0045: Open WebUI treats Hindsight as a context source

## Status

**ACCEPTED (2026-10-30).** Implements shared-memory recall in the chat
companion without replacing its document RAG.

## Context

Open WebUI already owns upload parsing, embedding, vector search, source
rendering and citations. Hindsight owns hal0's durable facts, observations,
raw retained chunks and memory visibility rules. Copying Hindsight data into
Open WebUI's vector database would create two stores with different deletion,
freshness and access semantics. Calling Hindsight as a model tool would make
recall optional and would not feed Open WebUI's normal citation pipeline.

Hindsight recall also returns response-level chunks beside its result list.
Those chunks do not automatically inherit a result ACL: filtering private or
project facts while forwarding the original chunk map would leak their raw
source text.

## Decision

1. Open WebUI keeps its native document/Knowledge RAG. Hindsight is an
   additional context source, not its vector store.
2. `POST /api/memory/context` performs deterministic, shared-only recall. It
   accepts no bank, dataset, tag, private-mode or agent identity selector and
   always calls memory as `dataset="shared"`, `client_id="anonymous"`.
3. Raw chunks are preferred to extracted facts. Every chunk and source fact
   must explicitly join to a fact that survived visibility and token-budget
   filtering; unjoinable enrichment is omitted. A degraded provider falls
   back to facts.
4. A global Open WebUI inlet Filter calls that endpoint and appends results to
   `body.metadata.files` as full text sources. Open WebUI's existing retrieval
   code therefore combines uploaded files and Hindsight context and owns the
   source numbering/citation UI. The Filter never invents citation numbers.
5. Retrieved memory is untrusted evidence. The Filter adds a system reminder
   not to execute instructions found inside sources, and the API neutralises
   source-wrapper terminators and control characters.
6. Recall fails open: timeout, authentication, malformed response and engine
   failure leave the chat request unchanged. Agent/Hermes models and Open
   WebUI task-model calls are skipped so agents do not receive the same memory
   twice.
7. hal0 manages exactly the Function ID `hal0_hindsight_rag` through Open
   WebUI's individual create/update/toggle endpoints. It never calls the bulk
   `/functions/sync` endpoint, never overwrites a colliding user Function, and
   preserves valves plus manually disabled/global state on updates. Without an
   admin token it reports `auth_required` and serves the source for manual
   import.
8. Open WebUI's independent native Memories feature is disabled. Explicit
   writes and scoped/private operations remain MCP responsibilities.

## Consequences

- Upload RAG continues to work when Hindsight is down or the Filter is
  disabled. Disabling one Function is the complete runtime rollback.
- Automatic chat recall sees shared memory only. Making private/project data
  globally visible is a separate operator decision and is not inferred from a
  self-asserted header.
- Open WebUI user and chat headers are audit provenance, not an ACL identity.
- Function installation needs an Open WebUI admin token or a one-time manual
  import; trusted-header deployments do not need to grant hal0 an admin token.
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
