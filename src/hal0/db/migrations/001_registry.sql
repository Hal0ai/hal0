-- 001_registry.sql  (schema version 1)
--
-- ML-1 registry pilot. Lossless against today's TOML-backed
-- hal0.registry.model.Model / ModelDefaults: every existing field round-
-- trips (id, name, path, size_bytes, quant, license, capabilities, hf_repo,
-- hf_filename, tags, backends, mmproj, defaults.*, metadata). model_file
-- ships EMPTY in this migration -- ML-2 (the file-set lane) is its first
-- writer. New columns not yet on Model (revision, preferred_runner,
-- architecture, mtp, jinja) are reserved for the upcoming §7.1 fields and
-- always land NULL until that lane populates them.

CREATE TABLE model (
  id               TEXT PRIMARY KEY,        -- Model.id
  -- §7.1 metadata record, reserved for a later lane ----------------------
  source_repo      TEXT,                    -- Model.hf_repo
  revision         TEXT,                    -- resolved commit sha (update detection)
  path             TEXT NOT NULL,           -- Model.path, the entry point / shard-1
  preferred_runner TEXT,                    -- key into RUNNER_IMAGES
  mmproj           TEXT,                    -- Model.mmproj, nullable
  architecture     TEXT,                    -- FAMILY_DEFAULTS keying
  context_length   INTEGER,                 -- Model.metadata["context_length"]
  mtp              INTEGER,                 -- tri-state capability flag: NULL/0/1
  jinja            INTEGER,                 -- tri-state capability flag: NULL/0/1
  -- existing Model scalars (lossless round-trip) --------------------------
  name             TEXT NOT NULL DEFAULT '',
  size_bytes       INTEGER NOT NULL DEFAULT 0,
  quant            TEXT,
  license          TEXT NOT NULL DEFAULT 'unknown',
  hf_filename      TEXT NOT NULL DEFAULT '',
  -- ModelDefaults folded onto the row --------------------------------------
  profile          TEXT,                    -- ModelDefaults.profile
  extra_args       TEXT,                    -- ModelDefaults.extra_args
  n_gpu_layers     INTEGER,                 -- ModelDefaults.n_gpu_layers
  chat_template    TEXT,                    -- ModelDefaults.chat_template
  context_size     INTEGER,                 -- ModelDefaults.context_size
  rope_freq_base   REAL,                    -- ModelDefaults.rope_freq_base
  -- lists too small to normalize for the pilot -----------------------------
  capabilities     TEXT,                    -- JSON array (Model.capabilities)
  tags             TEXT,                    -- JSON array (Model.tags)
  -- bookkeeping -------------------------------------------------------------
  sha256           TEXT,
  pulled_at        TEXT,
  created_at       TEXT,
  updated_at       TEXT,
  extra            TEXT                     -- JSON: Model.metadata minus context_length/upstream_url
);

CREATE TABLE model_file (                    -- file-SET abstraction; EMPTY in ML-1 (ML-2 first writer)
  model_id     TEXT NOT NULL REFERENCES model(id) ON DELETE CASCADE,
  rel          TEXT NOT NULL,                -- path within the repo/snapshot
  dest         TEXT,                         -- resolved on-disk dest
  size_bytes   INTEGER,
  sha256       TEXT,
  lfs          INTEGER,                      -- 0/1
  role         TEXT,                         -- model|shard|mmproj|tokenizer|config
  shard_index  INTEGER,                      -- ordered, shard_index=1 = entry point
  PRIMARY KEY (model_id, rel)
);

CREATE TABLE model_backend (                 -- replaces Model.backends JSON list, queryable
  model_id TEXT NOT NULL REFERENCES model(id) ON DELETE CASCADE,
  backend  TEXT NOT NULL,                    -- rocm|vulkan|flm|kokoro|comfyui|cpu|cuda|moonshine
  PRIMARY KEY (model_id, backend)
);

CREATE INDEX idx_model_file_role    ON model_file(model_id, role);
CREATE INDEX idx_model_backend_be   ON model_backend(backend);
