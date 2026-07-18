-- 003_store.sql  (schema version 3)
--
-- ML-3 store lane. NOTE: this is intentionally "003", not "002" — OBS-1's
-- metrics lane owns migration 002 (in flight on a sibling branch) and this
-- lane must never collide with it (plan §23.3b calls out store_blob as its
-- own migration, separate from 001_registry.sql's model_file column).
--
-- store_blob is the refcounted content-addressed dedup table: a file
-- installed by the ML-2 file-set puller checks here BEFORE landing bytes
-- (hardlink instead of re-download when the sha256 already has a blob).
-- model_file.sha256 -> store_blob.sha256 is the ref edge; refcount is NOT
-- a column on model_file itself (plan §23.3b) so one blob can be shared
-- (hardlinked) across many model_file rows / many models' snapshots
-- without model_file needing to know about sharing.

CREATE TABLE store_blob (
  sha256     TEXT PRIMARY KEY,     -- LFS oid / computed digest
  size_bytes INTEGER NOT NULL,
  blob_path  TEXT NOT NULL,        -- canonical on-disk file (hardlink target)
  refcount   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT
);

CREATE INDEX idx_store_blob_refcount ON store_blob(refcount);
