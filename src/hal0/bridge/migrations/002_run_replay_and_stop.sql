-- 002_run_replay_and_stop.sql  (schema version 2)
--
-- ADR-0043 decision 9, after the review of the run tools:
--
--   runs.idempotency_key  the key hal0 sent Hermes for the start
--                         (`hal0-bridge:<client>:<key>`), NULL without one. A
--                         retried start is found here before the concurrency
--                         cap is checked, so a replay of a run already
--                         admitted is never refused as one run too many.
--   runs.request_sha256   sha256 of what the start asked for (input,
--                         instructions, reasoning effort, session key): the
--                         same key with another request is a conflict, as
--                         Hermes itself answers.
--   runs.pending_stop     epoch seconds hal0 decided to stop the run (a
--                         clarification left unanswered) while the stop has
--                         not reached Hermes yet; NULL otherwise. The watcher
--                         retries it until Hermes takes it or the run ends.

ALTER TABLE runs ADD COLUMN idempotency_key TEXT;
ALTER TABLE runs ADD COLUMN request_sha256 TEXT;
ALTER TABLE runs ADD COLUMN pending_stop REAL;

CREATE INDEX runs_idempotency ON runs(client, profile, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
