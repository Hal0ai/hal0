# Shared `hindsight-api` deploy runbook (P1-6)

Platform-owned Hindsight memory engine on CT 105 (10.0.1.142). One shared instance;
banks isolate; Hermes re-points onto it in P5-H. Brain gate stays **OFF** until P2-6.

## Pre-flight (read-only, 2026-06-06)

| Check | Result |
|---|---|
| lemond | up, v10.6.0, `status:ok` on `:13305` |
| extraction model `qwen3-it-4b-FLM` | present in lemond ✅ |
| §4b reranker `bge-reranker-v2-m3-q4_k_m` | present in lemond ✅ (for P2-5) |
| disk `/var/lib` | 347 G free ✅ |
| python | 3.12.3 |
| cognee sidecar `/var/lib/hal0/memory/cognee/hal0_memory_index.sqlite` | **54 rows** (table `hal0_memory_items`) |
| prior spike Hindsight install | GONE (hermes bank abandoned) — clean slate |

⚠ **Cognee store is NOT empty (54 rows).** Spec [Q10] assumed empty → no-op. P2 migration
(`hal0 memory migrate --dry-run`) will report 54 rows; the migrate-vs-start-fresh decision is
a P2 call, not part of this deploy.

## Plan corrections (plan unit was wrong on 3 points — verified vs hindsight-docs 0.7.2)

1. **`HINDSIGHT_API_DATA_DIR` does not exist.** pg0 embedded Postgres lives at `$HOME/.pg0`.
   → control it with `Environment=HOME=/var/lib/hal0/memory/hindsight` (external PG would use
   `HINDSIGHT_API_DATABASE_URL`; we use pg0).
2. **No `serve` subcommand.** The CLI is `hindsight-api --host <h> --port <p>` (plan said
   `hindsight-api serve`).
3. **`/v1` suffix required** on the LLM base_url: `http://127.0.0.1:13305/v1` (plan omitted it).
   Provider value is `openai` for any OpenAI-compatible endpoint (confirmed; the spike's
   `openai_compatible` was the bundled-plugin config.json schema, a different surface).

## Package + topology choice

- `pip install hindsight-api==0.7.2` (**full** — bundles local BGE embedder 384-d + MiniLM
  cross-encoder; only needs the external LLM). Spec-aligned ([Q4] bge-small 384-d) and
  matches the spike's proven 0.7.2.
- Embeddings + reranker LOCAL (no external embed config). Extraction LLM → lemond.
- pg0 embedded (single instance, single `public` schema + `bank_id` discriminator).
- venv: `/var/lib/hal0/memory/hindsight/.venv`; data root `/var/lib/hal0/memory/hindsight`
  (owner hal0:hal0).

## Deploy steps

1. `install -d -o hal0 -g hal0 /var/lib/hal0/memory/hindsight{,/hf-cache}` ✅
2. `python3 -m venv .../.venv` + `pip install hindsight-api==0.7.2` (run as hal0, HOME+HF_HOME
   set, detached + logged to `pip-install.log`) — IN PROGRESS / see below.
3. Install unit: `cp installer/systemd/hindsight-api.service /etc/systemd/system/` (file
   authored on hal0-dev feat branch + copied to CT105 — the feat branch is NOT checked out on
   the runtime).
4. `systemctl daemon-reload && systemctl enable --now hindsight-api.service`
5. Health: `curl -fsS http://127.0.0.1:9177/health`
6. pg0 persistence: `systemctl restart hindsight-api && sleep 5 && curl .../health`

## Results (2026-06-06, deployed)

- Installed: `hindsight-api==0.7.2` + `hindsight-api-slim==0.7.2` + `pg0-embedded==0.14.2`
  + torch 2.12.0 + sentence-transformers 5.5.1 + onnxruntime 1.26.0 + pgvector 0.4.2.
- `/health`: `{"status":"healthy","database":"connected"}` — first boot ~85s (HF model
  download for BGE+MiniLM into HF_HOME + pg0 init + migrations); ~60s on warm restart.
- Migrations: completed for schema `public`, vector extension `pgvector` (exact alembic
  head hash not captured — functional success confirmed in journal; pg0 query deferred).
- **pg0 persists across restart** ✅ — data root `/var/lib/hal0/memory/hindsight/.pg0/`
  (`installation/` + `instances/`) survives `systemctl restart`; health returns
  `database:connected` post-restart.
- Bound: `Uvicorn running on http://127.0.0.1:9177` (pinned, not LAN-exposed).
- lemond `After=` target confirmed real: `hal0-lemonade.service`.
- **Benign log noise:** onnxruntime `pthread_setaffinity_np failed ... Invalid argument`
  — CPU-affinity is restricted inside the LXC; onnxruntime falls back fine, service is
  healthy. Cosmetic; silence later by pinning intra-op thread count if desired.
- No CUDA errors (FORCE_CPU + no NVIDIA device → clean CPU path), no tracebacks.

## [Q5'] FLM extraction schema gap — RESOLVED (official, no patch)

Surfaced at the P1-7 retain smoke: `qwen3-it-4b-FLM` (NPU) ignores `response_format` and
returns a bare JSON **list**; Hindsight's `fact_extraction` rejects it
(`LLM returned non-dict JSON ... list`, 3× retry → `RuntimeError: Fact extraction failed`),
~63s/attempt. There is NO official toggle in 0.7.2 (the spike patched `fact_extraction.py` —
we do NOT carry that unversioned patch).

Models evaluated live:
- `gemma-4-26b-a4b-it-q4kxl` (iGPU GGUF) → returns `{"facts":[...]}` dict ✅ but contends with the 35B primary on the iGPU.
- `qwen3.5-4b-q4kxl` (iGPU, reasoning) → empty content ✗ (reasoning models fail extraction).
- `qwen3-it-4b-FLM` (NPU) → bare list ✗.

### FINAL CHOICE (2026-06-07): NPU extraction via `gemma3-4b-FLM`

Moved extraction to the **NPU** to free the iGPU for the user-facing primary (kills the
P2-6 eviction risk). Path-finding:
- `gemma4-it:e2b` AND `gemma4-it:e4b` (NPU) → ~~**BLOCKED**: `DRM_IOCTL_AMDXDNA_CREATE_HWCTX
  err=-22` ("Alloc hw resource failed"). Both sizes fail identically → Gemma-3n NPU2 arch is
  unsupported by the installed NPU stack (amdxdna `0.7`, NPU FW `1.1.2.65`, FLM `0.9.43`).
  Needs a host NPU driver/firmware update to enable.~~ **CORRECTED 2026-06-07 (see PR #605):**
  this conclusion was WRONG. `gemma4-it:e4b` **loads and serves chat on this exact NPU stack**
  (no driver/FW update needed) — proven via direct `flm run` + `flm check`. The real blocker is
  the lemond **trio flags** (`--asr 1 --embed 1`): bisected — trio+any-ctx fails, chat-only
  loads fine. e4b's vision+audio weights + asr/embed heads overrun the NPU's 8 columns; gemma3-4b
  tolerated the trio only because it's smaller. e4b chat-only is viable; extraction quality
  through it is still unproven (the validation retain died on teardown, not on its merits).
  ~16GB parked at `/var/lib/hal0/.config/flm/models/Gemma4-E{2,4}B-IT-NPU2/`.
- `gemma3-4b-FLM` (NPU) → **WORKS** ✅. gemma3 arch loads on this NPU. Emits a ```json-fenced
  `{"facts":[...]}`; Hindsight's `fact_extraction` strips the fence + parses. Live retain+recall
  green (2 on-topic facts, with temporal/entity enrichment).

→ unit sets `HINDSIGHT_API_LLM_MODEL=gemma3-4b-FLM`, `LLM_TIMEOUT=300`.
**Trade-off:** NPU extraction ~160s vs iGPU gemma-26b ~74s — but retain is async/background
(client sends `async:true`), so latency doesn't block, and the iGPU stays free.
**To switch back to iGPU** (faster extraction, accepts contention): set model to
`gemma-4-26b-a4b-it-q4kxl`. **To get gemma4 on NPU:** update the host amdxdna driver/FW.

## Recall sanity (P1-7, recorded not gated)

Live end-to-end on bank `smoketest` (deleted after):
- RETAIN `POST /v1/default/banks/{bank}/memories` `{items:[{content,document_id}]}` →
  `{success:true, items_count:1, usage:{...}}`, ~74s incl. gemma load.
- RECALL `POST /v1/default/banks/{bank}/memories/recall` `{query,max_tokens}` → 0.7s,
  `results` = 2 facts, both on-topic:
  - "The hal0 memory engine was swapped from Cognee to Hindsight in version 0.5."
  - "hal0 performs inference on a Strix Halo iGPU via Lemonade."

## ⚠ Real 0.7.2 API paths (differ from plan + the P1-5 client — MUST fix before P2-5)

Discovered via the live `/openapi.json`:
| op | P1-5 client (WRONG) | real 0.7.2 |
|---|---|---|
| retain | `POST .../{bank}/retain` `{content,...}` | `POST .../{bank}/memories` `{items:[MemoryItem]}` |
| recall | `POST .../{bank}/recall` | `POST .../{bank}/memories/recall` |
| delete | `DELETE .../{bank}/documents/{id}` | ✅ same |

MemoryItem: `{content* , document_id?, tags?, metadata?{str:str}, context?, timestamp?, ...}`.
RetainRequest: `{items:[MemoryItem], async?, document_tags?}`. RecallRequest: `{query*, max_tokens?,
types?, tags?, budget?, ...}`. Recall response: `{results:[...], trace, entities, chunks,
source_facts}`. Retain response: `{success, bank_id, items_count, usage}`.
→ `hindsight_client.py` (53ec956) corrected in follow-up commit.

## Carry-forward — status

1. **Reranker wiring — DONE in P2-5 (290ccfe).** Factory's hindsight branch now constructs
   `LemonadeReranker(url=embed.rerank_url, ...)` and passes it to `HindsightProvider`; the
   §4b cross-bank UNION merge reranks via it. `_rerank_union` now gates on `_rerank_enabled`
   so `set_rerank_enabled()` is a real toggle. Fail-soft: rerank down/evicted → `[]` → fused
   order. Unit-tested (mock-transport + fail-soft). ⚠ **P2-6 ACTIVATION:** the rerank endpoint
   is currently DORMANT — `:8086` refuses, lemond exposes no `/rerank` route, and the
   `rerank` slot (`jina-reranker-v1-tiny-en-GGUF`, vulkan) is evicted/unbound (port 0). At
   gate-on, bring up a llama.cpp-`/rerank` endpoint serving a reranker and point
   `[memory.embedding] rerank_url` at it (and set `rerank_enabled=true`), THEN confirm §4b
   rerank actually fires end-to-end (it's only unit-tested so far). Until then recall returns
   fused (un-reranked) order — functional, just not §4b-ordered.
   (Scope note: Hindsight's *own* per-bank recall can also rerank server-side via
   `HINDSIGHT_API_RERANKER_*`; the hal0-side LemonadeReranker reranks the CROSS-BANK union,
   a different scope — not double-ranking. For single-bank callers it's mildly redundant.)
2. **Async retain still UNVERIFIED live.** Client sends `async:true` (`hindsight_client.py`);
   the P1-7 smoke used sync. P2-5 wired the path but didn't exercise async end-to-end against
   the daemon (the new provider+client code isn't deployed to /opt/hal0 yet — only unit-tested).
   At the /opt/hal0 code deploy + gate-on, confirm a fire-and-forget retain lands + becomes
   recallable.
3. **iGPU eviction risk — RESOLVED 2026-06-07.** Extraction is on the NPU (`gemma3-4b-FLM`).
   Remaining (minor): NPU extraction shares the NPU with the FLM asr/embed trio; confirm no NPU
   contention under load at gate-on. Reverting to iGPU `gemma-4-26b-a4b-it` reinstates the risk.

## P2-6 — DEPLOYED + BRAIN LIVE (2026-06-07)

PR #601 (feat/hindsight-memory → main, 30 commits) merged after **full CI green** (py3.11+3.12
2793 tests, ui, γ-suite). CI caught one miss my scoped local runs didn't: `memory migrate`
undocumented in `cli.mdx` (parity gate) → fixed (af87a49).

Deployed `/opt/hal0` via `scripts/deploy.sh` (reset→origin/main `ee98106` + clean ui/dist
rebuild + hal0-api restart). Gate-OFF verify first: `/api/{models,slots,config/urls}`=200,
`/api/memory/add`=503 (dark) — P2-1 rename didn't break anything. Then flipped
`HAL0_MEMORY_ENABLED=1` in `/etc/hal0/api.env` + restart → `/api/status memory_enabled=True`.
No `[memory].engine` pin → schema default `hindsight` wins.

### End-to-end verified through the live front door
- `POST /api/memory/add` → `{id,timestamp}` (HindsightProvider→:9177 retain, async) ✅
- NPU `gemma3-4b-FLM` extraction ~150s (async/background) → `POST /api/memory/recall` returns
  the fact ("HAL 0.5 re-enabled its brain using Hindsight memory engine | When: 2026-06-07") ✅
- `POST /api/memory/search` (back-compat) → 1 item ✅
- ACL: foreign-private read (`X-hal0-Agent: bob`, `dataset=private:alice`) → 0 items
  (fail-open-empty) ✅
- `/mcp/memory` mount live (`GET`→200 streamable-http; memory_recall tool unit-tested P2-3).

### Known follow-ups (not blockers; brain is functional)
- **`/api/memory/list` returns 0** — `HindsightProvider.list_items` is the P1-2 `recall("*")`
  placeholder. FIX: wire it to Hindsight `GET /v1/default/banks/{bank}/memories/list`.
- **Rerank still dormant** (see carry-forward #1) — recall is fused, not §4b-ordered, until a
  `/rerank` endpoint is stood up + `rerank_url`/`rerank_enabled` set. Verify by ordering change.
- **MCP full tool-call round-trip** only unit-tested (raw curl can't do the MCP handshake).
- **install.sh template** flip (new installs default-on) — follow-up PR.

### Rollback (instant): unset `HAL0_MEMORY_ENABLED` in `/etc/hal0/api.env` + restart hal0-api
(back to dark). Or `[memory] engine = "cognee"` to revert engine (Cognee store never mutated).

## Rollback

`systemctl disable --now hindsight-api && systemctl mask hindsight-api`. Default engine is
still Cognee; the Hindsight data root is isolated under `/var/lib/hal0/memory/hindsight`.

## P5-H — Hermes convergence: IN PROGRESS (blocked on stub rewrite)

Code (P5H-1/2, PR #604): src plugin renamed `memory_cognee`→`memory_hindsight`,
`Hal0CogneeProvider`→`Hal0MemoryProvider`, `prefetch`→`recall` (/api/memory/recall). Unit-tested.

P5H-MIG: recorded — spike `hermes` bank abandoned (5 test rows / 40 failed retains), start fresh.

### ⚠ BLOCKER found by verify-before-destroy (do NOT teardown until resolved)
The LIVE Hermes plugin is the vendored stub at
`/var/lib/hal0/.hermes/plugins/memory/hal0-memory/__init__.py` (source:
`installer/agents/hermes/plugins/hal0-memory/__init__.py`) — NOT the src package. It is
**doubly broken** against current hal0-api:
1. **Missing `is_available`** — upstream `agent.memory_provider.MemoryProvider` ABC now requires
   `{get_tool_schemas, initialize, is_available, name}`; the stub lacks `is_available` → can't
   instantiate → flipping `provider: hal0-memory` leaves Hermes with NO memory provider.
2. **MCP transport 405** — stub's `_call_mcp` does a bare JSON-RPC `tools/call` POST to
   `/mcp/memory`; the current streamable-HTTP mount returns 405 (needs the MCP initialize/session
   handshake). Verified with + without `Accept: text/event-stream`.
It never surfaced because Hermes was on `provider: hindsight` (local_embedded) — the stub was
deployed but never loaded.

### Fix path (the real reconciliation)
Rewrite the installer stub to the WORKING REST path (verified end-to-end this session):
- add `def is_available(self) -> bool: return True`;
- replace `_call_mcp` (MCP→REST): `prefetch`→`POST /api/memory/recall {query,max_tokens,types}`,
  `sync_turn`→`POST /api/memory/add {text,tags}`, keeping headers `X-hal0-Agent` + `X-hal0-Private:1`
  (no `dataset` — #317). (The src `memory_hindsight/_client.py` is the reference REST client.)
Then redeploy the stub to `$HERMES_HOME/plugins/memory/hal0-memory/` (re-provision or copy),
verify it loads + `prefetch` returns an existing shared-bank fact, THEN flip provider + verify a
real turn, THEN teardown.

### Verify-before-destroy log (this session)
- Flipped `provider: hindsight→hal0-memory`, restarted → stub fails `is_available` → **REVERTED**
  to `provider: hindsight` (known-good restored, backup `config.yaml.bak-pre-p5h-flip-*`).
- ⚠ **Teardown safety (stale-plan trap):** :5432 postgres (pid 192259) is the PLATFORM brain's
  pg0 (`/var/lib/hal0/memory/hindsight/.pg0`, parent = P1-6 daemon 192123) — NOT the spike's.
  Plan's "`:5432` expect empty" is INVERTED: after teardown :5432 must STILL show the platform pg.
  KEEP `/var/lib/hal0/memory/hindsight/*`. DESTROY only `/var/lib/hal0/.pg0` (spike, 61M, NOT
  running) + hindsight-* in `/var/lib/hal0/venvs/hermes/` (NOT the platform `.venv`). The pip
  uninstall is the fatal trap — both venvs have `hindsight-api`; use the exact `…/venvs/hermes/bin/pip`.

## P5-H — COMPLETE (2026-06-07): Hermes converged onto the shared brain

- P5H-3: stub rewritten to REST `/api/memory/*` + `is_available` (deployed to
  `$HERMES_HOME/plugins/memory/hal0-memory/`); `provider: hindsight → hal0-memory`.
  **local_embedded retired:** hindsight-{all,api-slim,embed,client} uninstalled from
  `/var/lib/hal0/venvs/hermes` (platform `.venv` UNTOUCHED, verified imports + /health);
  spike `20-hindsight.conf` + both patch scripts removed; `/var/lib/hal0/.pg0` (spike, dormant)
  deleted. Platform pg0 (`/var/lib/hal0/memory/hindsight/.pg0`, :5432 pid 192259) intact.
- Verified: deployed-stub prefetch → `/api/memory/recall` → shared brain returns shared facts
  (post-teardown); a Hermes turn fanned recall to `private__hermes-agent` + `shared` banks.
  NO local_embedded respawn (both guards gone). hermes venv: 0 hindsight pkgs.
- P5H-4: config `hal0:hal0 600`; self-guard `/usr/local/bin/hermes` + backup present.
- P5H-5: §4b precedence stanza appended to `$HERMES_HOME/SOUL.md`; `ground-truth-precedence`
  directive set on `shared` bank. (private__hermes-agent directive deferred — bank auto-creates
  on Hermes's first write; FK requires the bank to exist first.)
- P5H-6: single provider `hal0-memory` (no second); plugin `kind: memory-provider`.
- P5H-MIG: spike `hermes` bank abandoned (not migrated); started fresh.
- Rollback: `config.yaml.bak-pre-p5h-flip-*` reverts the flip; local_embedded retire is one-way
  (re-spike if ever needed — data was disposable).

**PLAN COMPLETE: P0 + P1 + P2 + P5-H all shipped. The brain is live + Hermes runs on it.**
