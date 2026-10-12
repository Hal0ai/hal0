-- 012_model_tuning_holding.sql  (schema version 12)
--
-- ADR-0029 (profile-centric config): a model row holds facts about its file
-- only; every tune lives on a profile. This moves the tuning columns (and the
-- two tuning keys of the `extra` JSON blob) off `model` into a holding table.
-- The profile-centric conversion (hal0.config.migrations.profile_centric)
-- reads the holding table to build each slot's profile and drops it once the
-- whole install has converted. Holding rather than dropping outright means
-- the order of "the database was opened" and "the conversion ran" can never
-- lose an operator's tuning.

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

UPDATE model
SET extra = json_remove(extra, '$._enable_thinking', '$._vision')
WHERE json_valid(extra);

ALTER TABLE model DROP COLUMN profile;
ALTER TABLE model DROP COLUMN extra_args;
ALTER TABLE model DROP COLUMN n_gpu_layers;
ALTER TABLE model DROP COLUMN context_size;
ALTER TABLE model DROP COLUMN rope_freq_base;
ALTER TABLE model DROP COLUMN preferred_runner;
