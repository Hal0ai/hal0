-- 011_one_shot_conversion.sql  (schema version 11)
--
-- Markers for one-shot data conversions. A row means "this conversion has
-- already run against THIS database", so the conversion never runs twice,
-- whatever files reappear on disk later.
--
-- First user: 'registry_toml' (hal0.registry.toml_conversion). The TOML-era
-- registry used to be re-imported every time the model table was empty,
-- which brought deleted models back. The conversion now imports a leftover
-- registry.toml at most once per database, then renames the file.
--
-- A database that already holds models was initialised from registry.toml
-- by the old first-boot import (every supported release ran it on the first
-- open of an empty registry), so it is marked done here and the conversion
-- only archives the file.

CREATE TABLE one_shot_conversion (
  name         TEXT PRIMARY KEY,
  completed_at TEXT NOT NULL,
  detail       TEXT                       -- JSON outcome
);

INSERT INTO one_shot_conversion (name, completed_at, detail)
SELECT 'registry_toml',
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
       '{"outcome": "already-initialised", "by": "011"}'
WHERE EXISTS (SELECT 1 FROM model);
