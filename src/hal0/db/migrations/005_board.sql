-- 005_board.sql  (schema version 5)
--
-- KB-4 board lane (rework R4 §Agents-and-brain: "hal0-owned board state").
-- hal0 becomes authoritative for the Operator Board; Hermes kanban is an
-- OPTIONAL executor, never the store. The FE↔BE wire contract (ui/CONTRACTS.md
-- "Operator Board", SPEC §4) is FROZEN — these tables exist to reproduce that
-- contract's shapes (lanes keyed by status, the canonical card envelope,
-- profiles/assignees/stats/orchestration) from local SQLite instead of a
-- proxy forward.
--
-- Numbered 005 (001=registry, 002=metrics, 003=store, 004=slots/ports already
-- allocated — board-protocol §Hard rules: "next lane = 005"). The forward-only
-- runner (hal0.db.migrate) applies it on top of those; every INSERT below is a
-- one-shot seed that only ever runs on the transaction that first lands v5.

-- Boards. slug is the stable key the FE threads as ?board=<slug>. Exactly one
-- row carries is_current=1 (the /boards/{slug}/switch target); reads with no
-- ?board= resolve to it. revision is the board-scoped concurrency token bumped
-- on structural board changes (rename, delete-of-a-card, etc.).
CREATE TABLE board (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    is_current  INTEGER NOT NULL DEFAULT 0,          -- at most one row = 1
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    revision    INTEGER NOT NULL DEFAULT 1
);

-- Canonical lane/column set. The frozen contract fixes VALID_STATUSES (9) and
-- the visible-lane order (8 + archived-on-demand), identical for every board,
-- so columns are GLOBAL, not per-board. `position` drives lane order; `visible`
-- gates `archived` out of the default /board response. card.status FKs here so
-- an unknown lane can never be written.
CREATE TABLE board_column (
    status   TEXT PRIMARY KEY,
    label    TEXT NOT NULL,                          -- UI label (running -> "in-progress")
    position INTEGER NOT NULL,
    visible  INTEGER NOT NULL DEFAULT 1
);

-- Cards (the canonical task/drawer shape). id keeps the prototype's t_<hex>
-- form. revision is the PER-CARD ETag token bumped on every mutation (KB-6
-- optimistic concurrency). Timestamps are epoch seconds (REAL) — the FE
-- normaliser already handles epoch-second wire values.
CREATE TABLE card (
    id           TEXT PRIMARY KEY,
    board_slug   TEXT NOT NULL REFERENCES board(slug) ON DELETE CASCADE,
    title        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL REFERENCES board_column(status),
    assignee     TEXT,
    tenant       TEXT,
    priority     INTEGER NOT NULL DEFAULT 0,
    workspace    TEXT,
    created_by   TEXT,
    body         TEXT,
    block_reason TEXT,
    schedule     TEXT,
    summary      TEXT,
    created_at   REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at   REAL NOT NULL DEFAULT (strftime('%s','now')),
    revision     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_card_board_status ON card(board_slug, status);
CREATE INDEX idx_card_status ON card(status);

-- Comments on a card ({author, at, body} in the drawer envelope).
CREATE TABLE card_comment (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    author  TEXT,
    body    TEXT NOT NULL DEFAULT '',
    at      REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX idx_card_comment_card ON card_comment(card_id, at);

-- Dependency edges (deps:{parents,children}). A parent->child edge; a card's
-- parents are rows where child_id=card, its children rows where parent_id=card.
CREATE TABLE card_link (
    parent_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    child_id  TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, child_id)
);

CREATE INDEX idx_card_link_child ON card_link(child_id);

-- Worker runs (runs:[{state, profile, dur, at, msg}]) — the executor-detail
-- ledger. Written by the dispatch-seam writeback (KB-5) when an executor is
-- wired; empty otherwise.
CREATE TABLE card_run (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    state   TEXT NOT NULL,
    profile TEXT,
    dur     TEXT,
    msg     TEXT,
    at      REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX idx_card_run_card ON card_run(card_id, at);

-- Live-events log. `cursor` is the monotonic id the /api/board/events WS
-- streams as the `cursor` field of each {"events":[...], "cursor": N} frame;
-- the browser passes the last value back as ?since= to resume. Every board
-- mutation appends one row here, so the WS reflects operator, agent-chat, and
-- worker writes through the one transport (frozen contract).
CREATE TABLE card_event (
    cursor     INTEGER PRIMARY KEY AUTOINCREMENT,
    board_slug TEXT,
    card_id    TEXT,
    kind       TEXT NOT NULL,
    json       TEXT,
    at         REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX idx_card_event_board ON card_event(board_slug, cursor);

-- Orchestration singleton (id=1). The 4 editable knobs (PUT /orchestration)
-- plus the 4 read-only config knobs (GET /config) live on one row so a board
-- with no cards still answers both reads with real (not stubbed) values.
CREATE TABLE board_orchestration (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    orchestrator_profile  TEXT,
    default_assignee      TEXT,
    auto_decompose        INTEGER NOT NULL DEFAULT 0,
    auto_promote_children INTEGER NOT NULL DEFAULT 0,
    tick_interval         INTEGER NOT NULL DEFAULT 5,
    failure_limit         INTEGER NOT NULL DEFAULT 3,
    claim_ttl             INTEGER NOT NULL DEFAULT 600,
    max_in_flight         INTEGER NOT NULL DEFAULT 4
);

-- Profile registry (PATCH /profiles/{name}). Assignee/profile counts are
-- DERIVED from live cards at read time, not stored here, so a row is only the
-- editable label/description; a profile referenced only by cards still shows up
-- in the derived list.
CREATE TABLE board_profile (
    name        TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    description TEXT
);

-- Seed the canonical lanes (frozen order; archived hidden by default).
INSERT INTO board_column (status, label, position, visible) VALUES
    ('triage',    'triage',      0, 1),
    ('todo',      'todo',        1, 1),
    ('scheduled', 'scheduled',   2, 1),
    ('ready',     'ready',       3, 1),
    ('running',   'in-progress', 4, 1),
    ('blocked',   'blocked',     5, 1),
    ('review',    'review',      6, 1),
    ('done',      'done',        7, 1),
    ('archived',  'archived',    8, 0);

-- Seed the orchestration singleton with the frozen contract defaults.
INSERT INTO board_orchestration
    (id, orchestrator_profile, default_assignee, auto_decompose, auto_promote_children,
     tick_interval, failure_limit, claim_ttl, max_in_flight)
VALUES (1, NULL, NULL, 0, 0, 5, 3, 600, 4);
