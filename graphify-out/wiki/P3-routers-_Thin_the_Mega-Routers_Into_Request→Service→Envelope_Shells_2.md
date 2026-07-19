# P3-routers: Thin the Mega-Routers Into Request→Service→Envelope Shells

> 46 nodes

## Key Concepts

- **P3-routers: Thin the Mega-Routers Into Request→Service→Envelope Shells** (13 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **6. Cross-lane coordination (must coordinate — do NOT design)** (9 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1. Current-state map (verified, line-anchored)** (8 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **4. MCP admin auto-generation** (6 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **5. Edit plan (lanes + order)** (6 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **7. Tests impact** (5 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **3. Interface boundaries** (4 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **spec-p3-routers.md** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **0. Executive summary** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.1 `api/routes/models.py` — **2,267 LOC as-built** (plan: 2,509)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.2 `api/routes/slots.py` — **1,888 LOC as-built** (plan: 1,846)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.3 `api/routes/comfyui.py` — **951 LOC** — typed-error outliers** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.4 `api/routes/benchmarks.py` — **480 LOC** — raw `HTTPException`** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.5 `api/routes/chat_templates.py` — **141 LOC** — bonus outliers** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.6 `mcp/admin.py` — **1,684 LOC** — `_REST_MAP`/`_PATH_ARGS` hand-maintained** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **1.7 `security/exposure.py` — **276 LOC** — **READ-ONLY CONTRACT**** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **2. Target module layout** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **3.1 Service-layer Protocols (unit-testability seams)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **3.2 Route-layer Pydantic bodies (replacing `await request.json()`)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **3.3 Typed-error migration (replace HTTPException + hand-built JSONResponse)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **4.1 What auto-generates** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **4.2 What stays hand-authored (security overlay)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **4.3 `_validate_catalog` adaptation** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **4.4 Why not auto-generate the security overlay?** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- **4.5 Risk to agent chat (tool-name back-compat)** (1 connections) — `docs/rework/hal0-specs/spec-p3-routers.md`
- *... and 21 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-p3-routers.md`

## Audit Trail

- EXTRACTED: 90 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*