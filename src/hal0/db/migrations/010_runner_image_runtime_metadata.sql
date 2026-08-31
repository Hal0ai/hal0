-- 010_runner_image_runtime_metadata.sql  (schema version 10)
--
-- Catalogue runtime metadata (feat/catalogue-runtime-metadata): images.json
-- (hal0.runner-images.v1) entries may now declare which runtime the image's
-- entrypoint serves (`runtime_family`, same vocabulary as
-- hal0.runners.RuntimeFamily) and which backends its runner binary can
-- execute (`supported_backends`, same semantics as
-- hal0.runners.Runner.supported_backends — metadata, not a selector).
-- The slot drawer's "catalogued · downloaded" pin lane gates on these
-- instead of assuming every catalogue row is a llama-server fork.
-- NULL means the manifest entry predates the fields (or no entry matched);
-- readers fall back rather than treating absence as a veto.

ALTER TABLE runner_image ADD COLUMN runtime_family TEXT;
ALTER TABLE runner_image ADD COLUMN supported_backends_json TEXT;
