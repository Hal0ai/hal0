-- 004_slots_ports.sql  (schema version 4)
--
-- SLOT lane (rework §11.1 + §11.2). Two tables land together because the
-- port_claim rows reference slot.id, not slot.name: separating them would
-- let a claim outlive the identity it points at.
--
--   * slot        — id-keyed slot identity. id is the stable primary key;
--                   name is a mutable display label. Units, ports, state
--                   and routes address a slot by id, so a rename is a
--                   pure UPDATE of the label with zero reference churn.
--   * slot_link   — parent/child edges between slots (absorbs the TOML
--                   ``served_by = <name>`` field: the FLM-trio shadows
--                   point at their chat anchor by id).
--   * port_claim  — the single port authority. One live row per port;
--                   released rows stay as an audit trail. Allocation,
--                   reservation and release all flow through this table
--                   instead of a hand-assigned TOML ``port`` field.
--
-- Numbered 004 (001=registry, 002=metrics, 003=store already applied); the
-- forward-only runner (hal0.db.migrate) applies it on top of those.

-- Slot identity. id is the stable primary key; name is a mutable label
-- (display only). Slot TOML stays on disk for reads during the migration
-- window, but the runtime, the router, the unit name, and every port_claim
-- row address the slot by id.
CREATE TABLE slot (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,  -- opaque, monotonic, never reused
    name             TEXT NOT NULL,                       -- mutable label
    slot_type        TEXT NOT NULL,                       -- llm | embedding | reranking |
                                                          -- transcription | tts | image | ...
    device           TEXT NOT NULL DEFAULT '',            -- gpu-rocm | gpu-vulkan | npu | cpu
    runtime          TEXT NOT NULL DEFAULT 'container',   -- today always 'container'
    coresident_group TEXT,                                -- e.g. 'npu-flm-trio' (shared port)
    is_seed          INTEGER NOT NULL DEFAULT 0,          -- 1 for seeded slots
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at       REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(name)                                          -- names still unique (UI labels)
);

-- Anchor the FLM trio to its chat-anchor by id (was served_by=<name> in
-- TOML). The trio shadows carry the same coresident_group as the anchor;
-- their port is the anchor's port (one container, three virtual slots).
CREATE TABLE slot_link (
    parent_id  INTEGER NOT NULL REFERENCES slot(id) ON DELETE CASCADE,
    child_id   INTEGER NOT NULL REFERENCES slot(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                             -- 'served_by' | 'gated_by'
    PRIMARY KEY (parent_id, child_id, kind)
);

CREATE INDEX idx_slot_name ON slot(name);                 -- name lookup is the bridge path
CREATE INDEX idx_slot_type ON slot(slot_type, enabled);   -- route-for-request fan-in

-- Single authority on who owns which port. Source of truth for the pool.
-- A surrogate id primary key (not ``port``) lets one port carry many
-- historical rows: each acquire inserts a live row (released_at NULL),
-- release stamps released_at, and a later acquire of the same port inserts
-- a fresh live row -- the full acquire/release timeline survives as an
-- audit trail.
CREATE TABLE port_claim (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    port             INTEGER NOT NULL,                    -- the claimed port
    slot_id          INTEGER REFERENCES slot(id) ON DELETE SET NULL,  -- owner (NULL=reserved/orphaned)
    owner_kind       TEXT NOT NULL,                       -- 'slot' | 'reserved' | 'listener'
    owner_label      TEXT NOT NULL,                       -- 'api', 'slot:agent', 'listener:llama-server'
    coresident_group TEXT,                                -- shared with slot.coresident_group
    acquired_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    released_at      REAL                                 -- set when freed; live claim iff NULL
);

-- The unique-claim invariant: at most ONE live (released_at IS NULL) row
-- per port. Released rows drop out of the partial index, so a port can be
-- re-claimed after release. A second concurrent acquire of the same free
-- port trips this and raises IntegrityError -- the loser retries.
CREATE UNIQUE INDEX uq_port_claim_live
    ON port_claim(port)
    WHERE released_at IS NULL;

CREATE INDEX idx_port_claim_slot ON port_claim(slot_id, released_at);
