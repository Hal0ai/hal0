# hal0 Brain Model-Card Banner Replacement Design

## Goal

Replace the banner displayed at the top of the public Hugging Face model card for `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF` with the user-supplied image at `/home/mint/Downloads/Gemini_Generated_Image_kit4wrkit4wrkit4.png`, while preserving the rest of the model card and all published model artifacts.

## Asset handling

The supplied file is a 2298×1856 RGBA PNG with SHA-256 `5dcdba974e04e7bc768752c29bfc2fad3e40a80c92493290f32908b64c725ca0`. Publish its bytes unchanged as `hal0-brain-banner.png`, replacing the previous image at the same path. The README already references this path and does not need to change.

## Publication scope

Publish one atomic commit to the existing public Hugging Face repository containing only the replacement `hal0-brain-banner.png`, with bytes identical to the supplied image.

Do not change the README, three GGUF files, portable profile, model-card prose, or artifact metadata. Do not alter the old misleading Hugging Face repository.

The hal0 lifecycle catalog remains pinned to its already-verified immutable model revision because the model artifact itself is unchanged. The repository's latest revision will display the new banner.

## Verification

After publication:

1. Confirm the repository remains public.
2. Record the new immutable Hugging Face revision.
3. Confirm `hal0-brain-banner.png` exists and its remote SHA-256 equals `5dcdba974e04e7bc768752c29bfc2fad3e40a80c92493290f32908b64c725ca0`.
4. Confirm the rendered README still references `hal0-brain-banner.png` and retains the existing documentation.
5. Confirm all GGUF and profile files remain present with unchanged sizes and LFS SHA-256 hashes.
6. Update the local publication staging directory and revision record to match the new repository revision.

## Failure handling

If the upload or any remote verification fails, do not change the lifecycle catalog. Preserve the prior immutable Hugging Face revision as the known-good artifact revision and report the exact mismatch or API error.
