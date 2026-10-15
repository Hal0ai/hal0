# ADR-0030: Kokoro launches from the pulled store bundle, and its profile names no path

## Status

**ACCEPTED (2026-10-15).** Release-lead decision for 1.4.0 (#2739). It
amends ADR-0029 decision 2 for kokoro only. Moonshine (ADR-0001, #2473) and
qwen3-tts keep their bundle path in the profile.

## Context

ADR-0029 kept `--model_path` in the profile of the three self-managed
engines (kokoro, moonshine, qwen3-tts) because their bundle had no model
row. For kokoro that premise was wrong:

- The curated `kokoro-v1` row exists and `hal0 model pull kokoro-v1` pulls
  it. The capability-grouped pull renamed every file `model.gguf`, so the
  kokoro ONNX model landed at `<store>/tts/kokoro-v1/model.gguf` without its
  voices pack. The runner globs `kokoro-*.onnx` and `voices*.bin` under
  `--model_path`, so it could not have loaded that copy.
- The seeded `kokoro` profile passed
  `--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx`. A fresh
  install's store is `/var/lib/hal0/models`, and only the store is mounted
  into the tts container, so that path never exists there.
- With no bundle at `--model_path`, the runner downloads about 354 MB from
  GitHub into the container's own filesystem. The unit runs with `--rm`, so
  the download repeated on every tts start, and a box without egress could
  not run tts at all.

## Decision

1. **The bundle is the registry pull.** The curated `kokoro-v1` row names
   the ONNX model as `hf_file` and the voices pack in a new `extra_files`
   list. A capability-grouped pull keeps the canonical `model.gguf` name for
   GGUF files only; every other format keeps its upstream basename. The
   bundle therefore lands as `<store>/tts/kokoro-v1/kokoro-v1.0.onnx` plus
   `voices-v1.0.bin`. The row id stays `kokoro-v1`.
2. **The provider supplies `--model_path`.** `KokoroProvider` passes
   `hal0.config.store.capability_model_dir("tts", "kokoro-v1")`, the same
   directory the pull writes, or the directory of a registry-bound bundle
   when the slot names one. The store is already mounted read-only at the
   same path. The seeded `kokoro` profile carries no path. A `--model_path`
   in any kokoro profile is dropped with a warning, and the profile-centric
   converter drops it rather than forking a profile to keep it.
3. **A missing bundle fails the slot.** Before rendering the unit, the
   provider checks for both files and raises `slot.weights_missing`, naming
   `hal0 model pull kokoro-v1`. The runner's GitHub download is never
   reached. No persistent download cache is added: a pulled bundle in the
   store is the one supported source.
4. **The upgrade fetches the bundle once.** A box that pulled before 1.4.0
   holds only `model.gguf`; a box whose operator hand-staged a bundle holds
   nothing in the store. A post-activation migration pass
   (`hal0.config.migrations.kokoro_bundle`, run by `hal0 update` and the
   installer's re-run path) pulls `kokoro-v1` when kokoro is in use (an
   enabled capability selection binds `kokoro-v1`, or a slot resolves to the
   kokoro provider) and the store directory holds no loadable bundle. The
   pull installs both files, removes the mislabelled `model.gguf`, and
   re-points the store blob. The outcome is reported in the `hal0 update`
   output (`model_bundles` in the convergence report). The updater relaying
   that report is the outgoing release's code, so the first upgrade from a
   release without the field logs the outcome to the journal instead; the
   pull itself runs either way, because the pass runs in the new tree. A
   failed pull (an offline box) does not fail the update: it is
   reported with the `hal0 model pull kokoro-v1` remedy, and the slot keeps
   refusing to start with the same remedy. The boot-time migration safety
   net never downloads; it only logs the remedy. This is a one-time
   converter step: it reads only the configured store and fetches only the
   curated HF coordinates.

## Consequences

- A fresh install's tts slot starts offline once the bundle is pulled, and
  nothing is downloaded at slot start.
- A box whose operator staged a bundle under `<store>/local/kokoro-v1/` no
  longer launches it; the upgrade pulls the registry bundle instead, and an
  offline box is told to run the pull.
- `capability_model_dir` and `extra_files` are generic. Moonshine and
  qwen3-tts can adopt them if their bundles become registry pulls. Until
  then the seed check in `tests/slots/test_seed_profiles.py` exempts those
  two by name.
