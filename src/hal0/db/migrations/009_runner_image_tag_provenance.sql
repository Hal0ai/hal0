-- 009_runner_image_tag_provenance.sql  (schema version 9)
--
-- Per-tag llama.cpp build provenance (h0/runner-provenance): the sync
-- probe now reads the image CONFIG BLOB's OCI labels
-- (org.opencontainers.image.source / .revision, dev.hal0.runner.patches)
-- so an operator can tell an upstream Vulkan/ROCm build apart from the
-- ROCmFPX one. Stored as a JSON object
-- {"source_repo": ..., "revision": ..., "patch_count": ...}; NULL means
-- the blob probe failed, hasn't run, or the image carries no such labels.

ALTER TABLE runner_image_tag ADD COLUMN provenance_json TEXT;
