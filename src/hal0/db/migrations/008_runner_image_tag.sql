-- 008_runner_image_tag.sql  (schema version 8)
--
-- Runner-image catalogue v3 (spec 2026-08-30-runner-images-v3-design.md §1):
-- one row per catalogued (image, tag) with its resolved digest, so downloaded
-- state and "newer" comparisons are digest facts, not tag-name heuristics.
-- ord preserves the sync's newest-first ordering. Rows are fully replaced by
-- each successful sync (set_tags); no partial updates.

CREATE TABLE runner_image_tag (
    image_id   TEXT NOT NULL REFERENCES runner_image(id) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    digest     TEXT,
    size_bytes INTEGER,
    last_seen  TEXT,
    ord        INTEGER NOT NULL,
    PRIMARY KEY (image_id, tag)
);
