-- 013_model_draft.sql  (schema version 13)
--
-- ADR-0031 (speculative-decoding drafters): a drafter GGUF is a file fact of
-- the model it speeds up, the way `mmproj` is. `draft` holds the absolute path
-- of the paired drafter file; `draft_kind` holds what the drafter is
-- (dflash | eagle3 | mtp | simple), classified from its GGUF header when the
-- pairing is written. The rest of the drafter's facts (variant, block size,
-- size) ride in the `extra` JSON blob under `draft`.
--
-- Both columns are nullable and nothing reads them but the draft screens, so a
-- rollback to an earlier release simply ignores them.

ALTER TABLE model ADD COLUMN draft TEXT;
ALTER TABLE model ADD COLUMN draft_kind TEXT;
