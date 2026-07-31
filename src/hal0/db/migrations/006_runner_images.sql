-- 006_runner_images.sql  (schema version 6)
--
-- Runner Image catalogue (feat/runner-image-catalogue). One row per GHCR
-- package under ghcr.io/Hal0ai/hal0-runner-images, discovered by the sync
-- job (hal0.registry.runner_image_sync) via anonymous GHCR tag/digest probes
-- merged against the source repo's images.json manifest
-- (hal0.runner-images.v1 schema). A package with no matching images.json
-- entry still gets a row (tag/digest/size only); a malformed/unreachable
-- images.json degrades the merge, never the sync.

CREATE TABLE runner_image (
  id               TEXT PRIMARY KEY,   -- stable key: the GHCR repo path
                                        -- ("hal0ai/hal0-toolbox-cpu")
  image            TEXT NOT NULL,      -- full ref, e.g. "ghcr.io/hal0ai/hal0-toolbox-cpu"
  tag              TEXT NOT NULL DEFAULT 'latest',
  digest           TEXT,               -- Docker-Content-Digest of `tag`, if resolved
  size_bytes        INTEGER,           -- advertised manifest size, if known
  -- images.json fields (hal0.runner-images.v1) — NULL when no entry matched
  manifest_key     TEXT,
  ownership        TEXT,               -- "owned" | "referenced"
  publish          TEXT,               -- "ci" | "external" | "manual"
  notes            TEXT,               -- free-text description (images.json "notes")
  build_json       TEXT,               -- JSON blob: images.json "build" object verbatim
  -- local download state (hal0.registry.runner_pull) --------------------
  local_path       TEXT,               -- non-NULL once an image has been pulled locally
  downloaded_at    TEXT,
  -- bookkeeping -----------------------------------------------------------
  discovered_at    TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  extra            TEXT                -- JSON: anything else worth keeping, forward-compat
);

CREATE INDEX idx_runner_image_ownership ON runner_image(ownership);
