-- 001_bridge.sql  (schema version 1)
--
-- ADR-0043 (agent bridge): the schema of bridge.db, a separate 0600 SQLite
-- file from hal0.db because it holds credential hashes and the text of
-- delegated runs.
--
--   clients     one row per bridge client (Claude Code, Codex, a CI job). Only
--               the sha256 of each key is stored (`key_hash`); the plaintext
--               is shown once at mint time. `scopes` is the sorted,
--               comma-joined grant set. A revoked client keeps its row so its
--               name cannot be re-added and its runs stay attributable.
--   runs        one row per delegated run: who asked (`client`), which agent
--               and Hermes profile ran it, its lifecycle `status`, and the
--               result. `last_seq` is the highest event sequence written.
--   run_events  the ordered event log of a run, replayable by `seq`.

CREATE TABLE clients (
  name         TEXT PRIMARY KEY,                       -- bridge client name
  key_hash     TEXT NOT NULL UNIQUE,                   -- sha256 hex of the whole key
  created_at   REAL NOT NULL,                          -- epoch seconds
  created_by   TEXT NOT NULL,                          -- who minted it (cli, api, ...)
  revoked_at   REAL,                                   -- NULL while the key is live
  last_seen_at REAL,                                   -- last successful authentication
  note         TEXT NOT NULL DEFAULT '',               -- operator-facing label
  scopes       TEXT NOT NULL DEFAULT 'agents,memory'   -- sorted, comma-joined grants
);

CREATE TABLE runs (
  run_id               TEXT PRIMARY KEY,
  client               TEXT NOT NULL,                  -- clients.name that started it
  agent                TEXT NOT NULL,
  profile              TEXT NOT NULL,                  -- Hermes profile it ran under
  session_key          TEXT,
  status               TEXT NOT NULL,
  created_at           REAL NOT NULL,
  updated_at           REAL NOT NULL,
  input_preview        TEXT NOT NULL,
  output               TEXT,
  error                TEXT,
  usage_json           TEXT,
  admission_id         TEXT,
  execution_generation INTEGER,
  pending_request_json TEXT,
  last_seq             INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX runs_client_created ON runs(client, created_at DESC);

CREATE TABLE run_events (
  run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  ts           REAL NOT NULL,
  event        TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
