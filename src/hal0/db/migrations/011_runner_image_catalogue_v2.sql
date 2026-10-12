-- 011_runner_image_catalogue_v2.sql  (schema version 11)
--
-- Fresh runner system (ADR-0028). The runner-image catalogue is now one row
-- per hal0 runner, keyed by the runner key ("standard", "cpu", ...), synced
-- from the runner-images repo's images.json schema hal0.runner-images.v2
-- (id, title, summary, image, tag, digest, backends, lanes_gated, family,
-- tier). The v1 columns (manifest_key, ownership, publish, notes, build_json,
-- runtime_family, supported_backends_json) and the repo-path row ids are
-- gone. The table is a cache of the remote catalogue, so it is recreated
-- empty; the next sync (API boot, `hal0 update`, or the Sync button) refills
-- it. Images already pulled into podman's store are unaffected: local images
-- are a separate fact from catalogue rows.

DROP TABLE IF EXISTS runner_image_tag;
DROP INDEX IF EXISTS idx_runner_image_ownership;
DROP TABLE IF EXISTS runner_image;

CREATE TABLE runner_image (
  id                  TEXT PRIMARY KEY,   -- hal0 runner key
  image               TEXT NOT NULL,      -- registry repo, e.g. "ghcr.io/hal0ai/hal0-runner-cpu"
  tag                 TEXT NOT NULL,      -- the catalogue's gated tag
  digest              TEXT,               -- the catalogue's gated digest
  size_bytes          INTEGER,            -- compressed image size, if probed
  title               TEXT,
  summary             TEXT,
  family              TEXT,               -- runtime family (hal0.runners.RuntimeFamily)
  tier                TEXT,               -- "default" | "optional"
  backends_json       TEXT,               -- JSON list of backends the image runs on
  lanes_gated_json    TEXT,               -- JSON list of lanes the gate passed
  available_tags_json TEXT,               -- JSON list, newest first
  local_path          TEXT,
  downloaded_at       TEXT,
  discovered_at       TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  extra               TEXT
);

CREATE TABLE runner_image_tag (
    image_id        TEXT NOT NULL REFERENCES runner_image(id) ON DELETE CASCADE,
    tag             TEXT NOT NULL,
    digest          TEXT,
    size_bytes      INTEGER,
    last_seen       TEXT,
    ord             INTEGER NOT NULL,
    provenance_json TEXT,
    PRIMARY KEY (image_id, tag)
);
