-- 014_drop_image_rows.sql  (schema version 14)
--
-- ComfyUI image models are owned by the image catalog (hal0.comfyui.catalog),
-- not by the LLM model registry. Earlier releases registered them here, so
-- existing boxes carry stale `model` rows for ComfyUI files. This deletes
-- those rows and their bookkeeping. It deletes ROWS ONLY: no file is
-- touched, and no refcount is changed.
--
-- Target set, the same predicate in every statement below:
--
--     SELECT id FROM model
--      WHERE path LIKE '%/comfyui/models/%' OR capabilities LIKE '%"image"%'
--
-- * The path substring is unavoidable in SQL (SQLite cannot call
--   hal0.comfyui.paths.is_under_models_root). It only matches rows whose
--   path still spells the default tree. A relocated or symlinked tree is
--   covered by the registry reconcile, and only for `auto-scan` rows. A
--   pulled or hand-added row under a relocated tree stays until it is
--   removed by hand; no such row can be created any more through pull,
--   POST or PUT.
-- * capabilities is a compact JSON array (`["chat","image"]`). The quotes in
--   '%"image"%' match the whole element, so `image-input` and ids such as
--   `qwen-image-understanding-gguf` are not targets.
--
-- Every statement re-evaluates the target set, which is stable until the
-- final DELETE removes the `model` rows. The children are deleted explicitly,
-- children first, so nothing depends on PRAGMA foreign_keys (the cascade is
-- off whenever a connection does not enable it). `model_file` and
-- `model_backend` are the only tables that reference model(id); the
-- `legacy_model_tuning` holding table has no FK and may already be dropped,
-- so it is deliberately not named here.

DELETE FROM model_backend
 WHERE model_id IN (SELECT id FROM model
                     WHERE path LIKE '%/comfyui/models/%' OR capabilities LIKE '%"image"%');

-- store_blob rows whose every model_file reference belongs to a target row.
-- A pulled image file has a blob (refcount == number of model_file rows
-- naming its sha256) whose blob_path is inside the ComfyUI tree. Delete the
-- blob row instead of decrementing it: at refcount 0 registry/gc.py unlinks
-- blob_path, which would delete the ComfyUI file. A blob that any surviving
-- row still references is left exactly as it is, refcount included.
DELETE FROM store_blob
 WHERE sha256 IN (SELECT sha256 FROM model_file
                   WHERE sha256 IS NOT NULL
                     AND model_id IN (SELECT id FROM model
                                       WHERE path LIKE '%/comfyui/models/%'
                                          OR capabilities LIKE '%"image"%'))
   AND NOT EXISTS (SELECT 1 FROM model_file AS keeper
                    WHERE keeper.sha256 = store_blob.sha256
                      AND keeper.model_id NOT IN (SELECT id FROM model
                                                   WHERE path LIKE '%/comfyui/models/%'
                                                      OR capabilities LIKE '%"image"%'));

DELETE FROM model_file
 WHERE model_id IN (SELECT id FROM model
                     WHERE path LIKE '%/comfyui/models/%' OR capabilities LIKE '%"image"%');

DELETE FROM model
 WHERE path LIKE '%/comfyui/models/%' OR capabilities LIKE '%"image"%';
