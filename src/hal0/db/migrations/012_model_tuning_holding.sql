-- 012_model_tuning_holding.sql  (schema version 12)
--
-- ADR-0029 (profile-centric config): a model row holds facts about its file
-- only; every tune lives on a profile. This copies the tuning columns (and the
-- two tuning keys of the `extra` JSON blob) into a holding table. The
-- profile-centric conversion (hal0.config.migrations.profile_centric) reads
-- ONLY the holding table once this migration has run, builds each slot's
-- profile from it, and drops it once the whole install has converted.
--
-- The `model` columns themselves are left in place and nothing new reads or
-- writes them, so a rollback to a release before ADR-0029 still finds the
-- schema it expects. A later release drops them with its own migration, once
-- the upgrade floor is past the release that ships this one.

CREATE TABLE legacy_model_tuning (
  id               TEXT PRIMARY KEY,
  profile          TEXT,
  extra_args       TEXT,
  n_gpu_layers     INTEGER,
  context_size     INTEGER,
  rope_freq_base   REAL,
  preferred_runner TEXT,
  enable_thinking  INTEGER,
  vision           INTEGER
);

INSERT INTO legacy_model_tuning
  (id, profile, extra_args, n_gpu_layers, context_size, rope_freq_base,
   preferred_runner, enable_thinking, vision)
SELECT id, profile, extra_args, n_gpu_layers, context_size, rope_freq_base,
       preferred_runner,
       CASE WHEN json_valid(extra) THEN json_extract(extra, '$._enable_thinking') END,
       CASE WHEN json_valid(extra) THEN json_extract(extra, '$._vision') END
FROM model
WHERE profile IS NOT NULL
   OR extra_args IS NOT NULL
   OR n_gpu_layers IS NOT NULL
   OR context_size IS NOT NULL
   OR rope_freq_base IS NOT NULL
   OR preferred_runner IS NOT NULL
   OR (json_valid(extra) AND json_extract(extra, '$._enable_thinking') IS NOT NULL)
   OR (json_valid(extra) AND json_extract(extra, '$._vision') IS NOT NULL);
