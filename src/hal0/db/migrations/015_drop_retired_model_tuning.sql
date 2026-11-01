-- 015_drop_retired_model_tuning.sql  (schema version 15)
--
-- ADR-0029 (profile-centric config) moved every tune off the model row onto
-- profiles. Migration 012 copied the tuning columns to a holding table and
-- left the columns in place so a rollback to a release before ADR-0029 would
-- still find its schema. The upgrade floor is now past the release that shipped
-- the conversion (>=1.5.0, which only runs on boxes that converted under
-- 1.4.0), so nothing reads or writes any of this any more. This drops:
--
--   * the `_enable_thinking` / `_vision` keys the old model rows kept in the
--     `extra` JSON blob;
--   * the `legacy_model_tuning` holding table created by 012;
--   * the six tuning columns of `model`: profile, extra_args, n_gpu_layers,
--     context_size, rope_freq_base, preferred_runner.
--
-- None of the six columns is indexed, constrained, a key, or referenced by a
-- view, trigger or foreign key, which is what ALTER TABLE ... DROP COLUMN
-- needs (SQLite 3.35 or newer). `chat_template`, `mtp` and `jinja` are model
-- file facts and keep their columns.
--
-- Forward-only: there is no down-migration. A release before this one keeps
-- running on the reduced schema: it reads and writes the `model` table
-- through a fixed column list that never named the six columns.

UPDATE model
   SET extra = json_remove(extra, '$._enable_thinking', '$._vision')
 WHERE json_valid(extra)
   AND (json_type(extra, '$._enable_thinking') IS NOT NULL
        OR json_type(extra, '$._vision') IS NOT NULL);

DROP TABLE IF EXISTS legacy_model_tuning;

ALTER TABLE model DROP COLUMN profile;
ALTER TABLE model DROP COLUMN extra_args;
ALTER TABLE model DROP COLUMN n_gpu_layers;
ALTER TABLE model DROP COLUMN context_size;
ALTER TABLE model DROP COLUMN rope_freq_base;
ALTER TABLE model DROP COLUMN preferred_runner;
