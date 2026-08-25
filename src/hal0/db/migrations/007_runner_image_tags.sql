-- 007_runner_image_tags.sql  (schema version 7)
--
-- Runner-image catalogue v2 tag tracking (spec
-- docs/superpowers/specs/2026-08-24-runner-image-catalogue-v2-design.md):
-- every sync now stores the FULL tags/list result per row, newest first
-- (hal0.registry.runner_image_sync.sort_tags_newest_first), so the UI can
-- offer a per-row tag picker and surface freshly pushed tags without an
-- images.json edit. NULL/absent means "probe failed or not yet run" and
-- reads back as [] — the headline tag column keeps its own semantics.

ALTER TABLE runner_image ADD COLUMN available_tags_json TEXT;
