# hal0 Brain Model-Card Banner Replacement Design

## Goal

Replace the banner displayed at the top of the public Hugging Face model card for `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF` with the user-supplied image at `/home/mint/Downloads/Gemini_Generated_Image_kit4wrkit4wrkit4.png`, while preserving the rest of the model card and all published model artifacts.

## Asset handling

The supplied file contains JPEG data despite its `.png` filename. Publish its bytes unchanged as `hal0-brain-banner.jpg` so the repository filename matches the actual media type. Remove the previous `hal0-brain-banner.png` and change only the README banner reference from the old filename to the new filename.

## Publication scope

Publish one atomic commit to the existing public Hugging Face repository containing:

- deletion of `hal0-brain-banner.png`;
- addition of `hal0-brain-banner.jpg` with bytes identical to the supplied image; and
- the README banner-reference update.

Do not change the three GGUF files, portable profile, model-card prose, or artifact metadata. Do not alter the old misleading Hugging Face repository.

The hal0 lifecycle catalog remains pinned to its already-verified immutable model revision because the model artifact itself is unchanged. The repository's latest revision will display the new banner.

## Verification

After publication:

1. Confirm the repository remains public.
2. Record the new immutable Hugging Face revision.
3. Confirm `hal0-brain-banner.jpg` exists and its remote SHA-256 matches the supplied file.
4. Confirm `hal0-brain-banner.png` is absent from the new revision.
5. Confirm the rendered README references `hal0-brain-banner.jpg` and retains the existing documentation.
6. Confirm all GGUF and profile files remain present with unchanged sizes and LFS SHA-256 hashes.
7. Update the local publication staging directory and revision record to match the new repository revision.

## Failure handling

If the upload or any remote verification fails, do not change the lifecycle catalog. Preserve the prior immutable Hugging Face revision as the known-good artifact revision and report the exact mismatch or API error.
