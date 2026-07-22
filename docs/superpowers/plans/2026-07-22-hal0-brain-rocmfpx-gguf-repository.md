# Hal0 Brain ROCmFPX GGUF Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF` with verified F16, ROCmFP4 Agent, ROCmFP8 Agent, portable hal0 profile, and branded model-card banner, then repin hal0's ROCmFPX brain catalog entry.

**Architecture:** Build every quant independently from the immutable local F16 source and stage all large artifacts outside git. Gate publication on tensor inspection and live runner behavior, publish the complete HF repository, remotely verify its immutable revision, then update the authored lifecycle catalog and regenerate canonical JSON.

**Tech Stack:** ROCmFPX `llama-quantize`, ROCmFPX-aware `gguf-py`, Pillow/ImageMagick, Podman, Hugging Face Hub Python API/CLI, pytest, TOML, canonical JSON.

## Global Constraints

- Public HF repository: `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF`.
- Source GGUF: `/mnt/ai-models/chat/hal0-brain-sft/model.gguf`, SHA-256 `ed9d28c4eac1d7c291bc80d9410c243a3d28e655921ccaf90f2b6619aa24d2c3`, size `2166552096`.
- Publish model filenames exactly as `hal0-brain-sft-F16.gguf`, `hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf`, and `hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf`.
- Quantize Q4 and Q8 independently from F16 using ROCmFPX commit `61f2f2d7bc4955e9bca821095ef69125837133b5` unless live loading proves that revision incompatible; never alter GGUF metadata to bypass a loader error.
- Publish `chat-long-context.hal0profile.json` and `hal0-brain-banner.png` alongside the models.
- Preserve `/home/mint/Downloads/Gemini_Generated_Image_vi6t5bvi6t5bvi6t.png` byte-for-byte.
- Do not overwrite or delete `Hal0ai/hal0-brain-sft-fpx8-agent`.
- Do not update the hal0 catalog until the final HF revision, Q8 filename, size, and digest are verified remotely.
- Keep ROCmFPX models restricted to `rocmfpx` and `vulkanfpx`; stock `vulkan`, `cpu`, and `cuda` runners remain incompatible.
- Stop before publication if either custom quant fails a live ROCmFPX load.

---

### Task 1: Stage the source, profile, and banner outside git

**Files:**
- Read: `/mnt/ai-models/chat/hal0-brain-sft/model.gguf`
- Read: `/home/mint/Downloads/chat-long-context.hal0profile.json`
- Read: `/home/mint/Downloads/Gemini_Generated_Image_vi6t5bvi6t5bvi6t.png`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-F16.gguf`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/chat-long-context.hal0profile.json`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-banner.png`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/source-image.sha256`

**Interfaces:**
- Consumes: approved source/profile/image paths.
- Produces: immutable staging directory used by every later task.

- [ ] **Step 1: Verify and stage the F16 source**

```bash
set -euo pipefail
STAGE=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
mkdir -p "$STAGE"
test "$(stat -c %s /mnt/ai-models/chat/hal0-brain-sft/model.gguf)" = 2166552096
echo 'ed9d28c4eac1d7c291bc80d9410c243a3d28e655921ccaf90f2b6619aa24d2c3  /mnt/ai-models/chat/hal0-brain-sft/model.gguf' | sha256sum -c -
cp --reflink=auto /mnt/ai-models/chat/hal0-brain-sft/model.gguf "$STAGE/hal0-brain-sft-F16.gguf"
```

Expected: checksum reports `OK`; staged F16 is `2166552096` bytes.

- [ ] **Step 2: Verify and stage the portable profile**

```bash
python3 - <<'PY'
import json
from pathlib import Path
from hal0.profiles.portable import parse_envelope, verify_checksum
src = Path('/home/mint/Downloads/chat-long-context.hal0profile.json')
dst = Path('/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/chat-long-context.hal0profile.json')
doc = json.loads(src.read_text())
env = parse_envelope(doc)
assert env.kind == 'hal0.profile'
assert env.schema_version == 1
assert env.name == 'chat-long-context'
assert verify_checksum(doc)
dst.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')
print(env.name, doc['checksum'])
PY
```

Run from `/home/mint/hal0/.worktrees/v1-rc-critical-path` with `uv run python` if plain Python cannot import hal0. Expected checksum: `sha256:241af4cd2636ac1da32a8a7ca0d856724445242cfcde88a208b702b155bdee47`.

- [ ] **Step 3: Record the source image checksum and create the branded banner**

```bash
SOURCE=/home/mint/Downloads/Gemini_Generated_Image_vi6t5bvi6t5bvi6t.png
STAGE=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
sha256sum "$SOURCE" > "$STAGE/source-image.sha256"
python3 - <<'PY'
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
src = Path('/home/mint/Downloads/Gemini_Generated_Image_vi6t5bvi6t5bvi6t.png')
dst = Path('/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-banner.png')
im = Image.open(src).convert('RGBA')
overlay = Image.new('RGBA', im.size, (0, 0, 0, 0))
d = ImageDraw.Draw(overlay)
for x in range(0, 1450):
    alpha = max(0, int(150 * (1 - x / 1450)))
    d.line((x, 0, x, 720), fill=(12, 5, 20, alpha))
title = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 132)
sub = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 43)
body = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 36)
d.text((120, 105), 'HAL0 BRAIN', font=title, fill=(255, 242, 220, 255), stroke_width=3, stroke_fill=(45, 10, 20, 230))
d.text((126, 275), 'Advanced reasoning · Tool calling · Platform management', font=sub, fill=(255, 205, 135, 255), stroke_width=2, stroke_fill=(35, 8, 18, 220))
d.text((126, 350), 'Your mini-agent administrator—trained on hal0 inside and out', font=body, fill=(245, 238, 245, 255), stroke_width=2, stroke_fill=(35, 8, 18, 220))
out = Image.alpha_composite(im, overlay)
out.save(dst, format='PNG', optimize=True)
print(dst, out.size)
PY
```

Expected: output dimensions remain `2298x1856`; all three lines are legible in the upper-left without covering the character's face.

- [ ] **Step 4: Prove the original image is unchanged**

```bash
cd /home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
sha256sum -c source-image.sha256
identify hal0-brain-banner.png
```

Expected: source image `OK`; banner is `2298x1856` PNG.

---

### Task 2: Produce and inspect the Q4/Q8 Agent GGUFs

**Files:**
- Read: `/home/mint/ROCmFPX/scripts/quantize-rocmfpx-agent.sh`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/artifacts.json`

**Interfaces:**
- Consumes: staged F16 from Task 1 and ROCmFPX quantizer commit `61f2f2d7`.
- Produces: two custom GGUFs plus a machine-readable manifest consumed by card generation and catalog repinning.

- [ ] **Step 1: Pin and verify ROCmFPX tooling**

```bash
set -euo pipefail
test "$(git -C /home/mint/ROCmFPX rev-parse HEAD)" = 61f2f2d7bc4955e9bca821095ef69125837133b5
test -x /home/mint/ROCmFPX/build-cpu/bin/llama-quantize
```

Expected: both commands exit zero. If the binary is absent, build only `llama-quantize`:

```bash
cmake -S /home/mint/ROCmFPX -B /home/mint/ROCmFPX/build-cpu \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DGGML_HIP=OFF \
  -DGGML_VULKAN=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=OFF
cmake --build /home/mint/ROCmFPX/build-cpu --target llama-quantize -j"$(nproc)"
```

- [ ] **Step 2: Quantize Q4 Agent directly from F16**

```bash
cd /home/mint/ROCmFPX
SRC=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-F16.gguf \
OUT=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf \
FORMAT=rocmfp4 PROFILE=agent KEEP_SPLIT=0 NTHREADS="$(nproc)" \
QUANTIZE_BIN=/home/mint/ROCmFPX/build-cpu/bin/llama-quantize \
scripts/quantize-rocmfpx-agent.sh
```

Expected wrapper report: `Preset: Q4_0_ROCMFP4_COHERENT` and one output file.

- [ ] **Step 3: Quantize Q8 Agent directly from F16**

```bash
cd /home/mint/ROCmFPX
SRC=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-F16.gguf \
OUT=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf \
FORMAT=rocmfp8 PROFILE=agent KEEP_SPLIT=0 NTHREADS="$(nproc)" \
QUANTIZE_BIN=/home/mint/ROCmFPX/build-cpu/bin/llama-quantize \
scripts/quantize-rocmfpx-agent.sh
```

Expected wrapper report: `Preset: Q8_0_ROCMFPX_AGENT` and one output file.

- [ ] **Step 4: Inspect all tensor types and write `artifacts.json`**

```bash
PYTHONPATH=/home/mint/ROCmFPX/gguf-py python3 - <<'PY'
import hashlib, json
from collections import Counter
from pathlib import Path
from gguf import GGUFReader
root = Path('/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF')
expected = {
    'hal0-brain-sft-F16.gguf': (1, 'F16'),
    'hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf': (102, 'Q4_0_ROCMFP4'),
    'hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf': (115, 'Q8_0_ROCMFPX'),
}
rows = []
for name, (ftype, required_type) in expected.items():
    path = root / name
    reader = GGUFReader(str(path), 'r')
    actual_ftype = int(reader.fields['general.file_type'].contents())
    counts = dict(sorted(Counter(t.tensor_type.name for t in reader.tensors).items()))
    assert actual_ftype == ftype, (name, actual_ftype, ftype)
    assert counts.get(required_type, 0) > 0, (name, counts)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    rows.append({'filename': name, 'size_bytes': path.stat().st_size, 'sha256': digest,
                 'general_file_type': actual_ftype, 'tensor_types': counts})
(root / 'artifacts.json').write_text(json.dumps({'artifacts': rows}, indent=2) + '\n')
print(json.dumps(rows, indent=2))
PY
```

Expected: F16 file type `1`, Q4 Agent file type `102`, Q8 Agent file type `115`; each custom file contains its requested ROCmFPX tensor type.

---

### Task 3: Gate publication on live compatible/incompatible runner behavior

**Files:**
- Read: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/artifacts.json`
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/runner-validation.log`

**Interfaces:**
- Consumes: inspected Q4/Q8 artifacts from Task 2.
- Produces: explicit load/rejection evidence required by the model card and publication gate.

- [ ] **Step 1: Pull the catalog-declared ROCmFPX runner image**

```bash
ROCM_IMAGE='ghcr.io/hal0ai/hal0-rocmfpx@sha256:fd6b02a720e633e402e929e19eedefff52aeec18e5de8f43e525689e523985f3'
podman pull "$ROCM_IMAGE"
podman run --rm --entrypoint sh "$ROCM_IMAGE" -lc 'command -v llama-cli || find / -type f -name llama-cli -perm -111 2>/dev/null | head -1'
```

Expected: image pulls by immutable digest and prints an executable `llama-cli` path. If it does not, inspect the image's executable paths but do not substitute a stock runner.

- [ ] **Step 2: Load both custom models with the ROCmFPX runner**

```bash
set -euo pipefail
ROCM_IMAGE='ghcr.io/hal0ai/hal0-rocmfpx@sha256:fd6b02a720e633e402e929e19eedefff52aeec18e5de8f43e525689e523985f3'
STAGE=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
: > "$STAGE/runner-validation.log"
for name in hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf; do
  podman run --rm -v "$STAGE:/models:ro" --entrypoint sh "$ROCM_IMAGE" -lc '
    CLI=$(command -v llama-cli || find / -type f -name llama-cli -perm -111 2>/dev/null | head -1)
    test -n "$CLI"
    "$CLI" -m "/models/'"$name"'" -p "Reply OK" -n 1 -c 512 -t 8 --no-warmup
  ' 2>&1 | tee -a "$STAGE/runner-validation.log"
done
```

Expected: both commands exit zero after loading all tensors. The observed source tokenizer metadata must be accepted. Any `unknown pre-tokenizer type` or unrecognized custom tensor error blocks publication.

- [ ] **Step 3: Confirm stock llama.cpp rejects the custom tensors**

```bash
set -euo pipefail
STOCK_IMAGE='ghcr.io/ggml-org/llama.cpp@sha256:c1ddeb6d30932ddd9ddff962cb62dbc5450cd99d8e82c8c20de2fd1f99fde85b'
STAGE=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
podman pull "$STOCK_IMAGE"
for name in hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf; do
  if podman run --rm -v "$STAGE:/models:ro" --entrypoint sh "$STOCK_IMAGE" -lc '
    CLI=$(command -v llama-cli || find / -type f -name llama-cli -perm -111 2>/dev/null | head -1)
    test -n "$CLI"
    "$CLI" -m "/models/'"$name"'" -p test -n 1 -c 256 --no-warmup
  ' >> "$STAGE/runner-validation.log" 2>&1; then
    echo "stock runner unexpectedly accepted $name" >&2
    exit 1
  fi
done
```

Expected: both stock loads return non-zero for unsupported custom tensor types. Preserve exact error text in `runner-validation.log`.

---

### Task 4: Build the model card and publish one complete HF revision

**Files:**
- Create outside git: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/README.md`
- Read: `/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF/artifacts.json`
- Publish: all staged GGUFs, `README.md`, `hal0-brain-banner.png`, and `chat-long-context.hal0profile.json`

**Interfaces:**
- Consumes: verified manifest and runner evidence.
- Produces: public HF repository and immutable revision consumed by Task 5.

- [ ] **Step 1: Generate the card from verified manifest values**

Write `README.md` with these required sections and no inferred metadata:

```markdown
---
license: apache-2.0
pipeline_tag: text-generation
base_model: Hal0ai/hal0-brain-sft
base_model_relation: quantized
tags: [hal0, hal0-brain, gguf, rocmfpx, rocmfp4, rocmfp8, agent, tool-use]
language: [en]
---

![HAL0 BRAIN — advanced reasoning, tool calling, and platform management](hal0-brain-banner.png)

# HAL0 BRAIN — ROCmFPX GGUF

HAL0 BRAIN is positioned as an advanced reasoning, tool-calling, platform-managing mini-agent administrator trained on the hal0 system. This repository packages one verified F16 reference and two agent-oriented ROCmFPX quants.
```

Follow with:
- An artifact table populated exclusively from `artifacts.json`.
- Source revision `Hal0ai/hal0-brain-sft-GGUF@6b190df6e816cc806f7fa7ae3de7248f5551e00b` and quantizer revision `charlie12345/ROCmFPX@61f2f2d7bc4955e9bca821095ef69125837133b5`.
- Tensor counts and `general.file_type` for every GGUF.
- Compatibility matrix: F16 supports stock and ROCmFPX runners; Q4/Q8 require ROCmFPX.
- Exact `hf download Hal0ai/hal0-brain-sft-ROCmFPX-GGUF <filename> --local-dir .` commands for every artifact and profile.
- ROCmFPX `llama-cli` and `llama-server` examples using the exact Q4/Q8 filenames.
- A portable-profile section explaining Dashboard → Profiles → Import and these API commands:

```bash
PROFILE=chat-long-context.hal0profile.json
jq -n --slurpfile envelope "$PROFILE" \
  '{envelope:$envelope[0],name:"chat-long-context",dry_run:true}' |
  curl --fail-with-body -sS http://127.0.0.1:8080/api/profiles/import \
    -H 'content-type: application/json' --data-binary @-

jq -n --slurpfile envelope "$PROFILE" \
  '{envelope:$envelope[0],name:"chat-long-context",dry_run:false}' |
  curl --fail-with-body -sS http://127.0.0.1:8080/api/profiles/import \
    -H 'content-type: application/json' --data-binary @-
```

Also state that the profile uses flash attention, Q8 K/V cache, `--no-mmap`, disabled context shift, batch `2048`, and unified batch `512`; users should choose a different name on collision and tune settings for their hardware.

- [ ] **Step 2: Create the public repository without overwriting any existing repo**

```bash
python3 - <<'PY'
from huggingface_hub import HfApi
api = HfApi()
repo = 'Hal0ai/hal0-brain-sft-ROCmFPX-GGUF'
api.create_repo(repo_id=repo, repo_type='model', private=False, exist_ok=False)
print(f'https://huggingface.co/{repo}')
PY
```

Expected: new public repository URL. If the repository already exists, stop and inspect ownership/content; do not silently reuse it.

- [ ] **Step 3: Publish all artifacts in one commit**

```bash
python3 - <<'PY'
from pathlib import Path
from huggingface_hub import CommitOperationAdd, HfApi
root = Path('/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF')
names = [
    'README.md',
    'hal0-brain-banner.png',
    'chat-long-context.hal0profile.json',
    'hal0-brain-sft-F16.gguf',
    'hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf',
    'hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf',
]
info = HfApi().create_commit(
    repo_id='Hal0ai/hal0-brain-sft-ROCmFPX-GGUF',
    repo_type='model',
    commit_message='Publish verified F16 and ROCmFPX agent quants',
    operations=[CommitOperationAdd(path_in_repo=n, path_or_fileobj=root / n) for n in names],
)
(root / 'hf-revision.txt').write_text(info.oid + '\n')
print(info.oid, info.commit_url)
PY
```

Expected: a 40-character immutable commit OID saved to `hf-revision.txt`.

- [ ] **Step 4: Verify the published revision remotely**

```bash
STAGE=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
REV=$(cat "$STAGE/hf-revision.txt")
hf repo-files Hal0ai/hal0-brain-sft-ROCmFPX-GGUF --revision "$REV"
hf download Hal0ai/hal0-brain-sft-ROCmFPX-GGUF README.md chat-long-context.hal0profile.json --revision "$REV" --local-dir "$STAGE/remote-check"
```

Use `HfApi().model_info(..., revision=REV, files_metadata=True)` if the installed `hf` CLI lacks `repo-files`. Expected: all six published files exist at `REV`, LFS sizes match `artifacts.json`, and the profile checksum still verifies.

---

### Task 5: Repin and verify the hal0 lifecycle catalog

**Files:**
- Modify: `tests/lifecycle/test_catalog_models.py`
- Modify: `src/hal0/lifecycle/data/models.toml`
- Regenerate: `src/hal0/lifecycle/data/catalog.json`

**Interfaces:**
- Consumes: final HF revision and verified Q8 row from `artifacts.json`.
- Produces: tested runtime catalog pin for the consolidated Q8 Agent artifact.

- [ ] **Step 1: Write the failing catalog pin test**

Add to `tests/lifecycle/test_catalog_models.py`:

```python
def test_rocmfpx_brain_model_pins_consolidated_agent_artifact(catalog) -> None:
    model = catalog.model("hal0-brain-rocmfpx-agent")
    assert model.source == "Hal0ai/hal0-brain-sft-ROCmFPX-GGUF"
    assert model.runners == ("rocmfpx", "vulkanfpx")
    assert len(model.files) == 1
    artifact = model.files[0]
    assert artifact.filename == "hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf"
    assert artifact.format == "rocmfpx-gguf"
    assert artifact.quantization == "q8_0_rocmfpx_agent"
```

- [ ] **Step 2: Run the test and verify RED**

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
uv run pytest tests/lifecycle/test_catalog_models.py::test_rocmfpx_brain_model_pins_consolidated_agent_artifact -v
```

Expected: FAIL because the current source is `Hal0ai/hal0-brain-sft-fpx8-agent` and filename is `model.gguf`.

- [ ] **Step 3: Update `models.toml` from verified publication metadata**

Set only the first model's source, revision, and file fields. Obtain values with:

```bash
STAGE=/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF
REV=$(cat "$STAGE/hf-revision.txt")
python3 - <<'PY'
import json
from pathlib import Path
root = Path('/home/mint/model-publish/hal0-brain-sft-ROCmFPX-GGUF')
row = next(x for x in json.loads((root/'artifacts.json').read_text())['artifacts']
           if x['filename'] == 'hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf')
print(row['filename'], row['sha256'], row['size_bytes'])
PY
printf 'revision=%s\n' "$REV"
```

The resulting TOML shape must be:

```toml
source = "Hal0ai/hal0-brain-sft-ROCmFPX-GGUF"
revision = "<the exact 40-character value printed from hf-revision.txt>"

[[models.files]]
filename = "hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf"
sha256 = "<the exact 64-character Q8 digest printed from artifacts.json>"
size_bytes = <the exact integer Q8 size printed from artifacts.json>
format = "rocmfpx-gguf"
quantization = "q8_0_rocmfpx_agent"
```

The angle-bracketed values are command outputs, not guessed or manually transcribed values.

- [ ] **Step 4: Regenerate canonical catalog JSON and verify GREEN**

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
uv run python scripts/compile-lifecycle-catalog.py --write
uv run pytest tests/lifecycle/test_catalog_models.py::test_rocmfpx_brain_model_pins_consolidated_agent_artifact -v
uv run pytest tests/lifecycle -v
uv run python scripts/compile-lifecycle-catalog.py --check
bash scripts/release-check.sh --local
```

Expected: all commands exit zero; lifecycle suite has zero failures; compiled catalog reports current; local release catalog gate passes.

- [ ] **Step 5: Confirm stock-runner exclusion remains enforced**

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
uv run pytest tests/lifecycle/test_catalog_validate.py::test_rocmfpx_model_cannot_use_stock_llama -v
```

Expected: PASS with compatibility reason `model_format.unsupported`.

- [ ] **Step 6: Refresh graphify and review the exact diff**

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
graphify update .
git diff --check
git diff -- tests/lifecycle/test_catalog_models.py src/hal0/lifecycle/data/models.toml src/hal0/lifecycle/data/catalog.json
```

Expected: only the approved test and catalog pin/generated JSON change; model binaries, profile JSON, and banner are absent from git status.

- [ ] **Step 7: Commit the tested catalog repin**

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
git add tests/lifecycle/test_catalog_models.py src/hal0/lifecycle/data/models.toml src/hal0/lifecycle/data/catalog.json
git commit -m "fix: repin brain to consolidated ROCmFPX repository"
```

Expected: one commit containing only the test, authored TOML, and generated catalog JSON.

---

### Task 6: Final publication and repository audit

**Files:**
- Read: HF repository at immutable revision.
- Read: current git diff/history.

**Interfaces:**
- Consumes: published HF revision and committed catalog repin.
- Produces: completion evidence with residual risks.

- [ ] **Step 1: Re-download headers from the immutable revision and compare digests**

Use `hf download` at the exact `hf-revision.txt` revision for all three GGUFs, allowing the HF cache to reuse local LFS blobs. Run `sha256sum` and the ROCmFPX-aware tensor-count script from Task 2 against those resolved paths.

Expected: remote F16/Q4/Q8 sizes, SHA-256 digests, `general.file_type`, and tensor counts exactly match `artifacts.json`.

- [ ] **Step 2: Audit git side effects with Shepherd and git**

```bash
cd /home/mint/hal0/.worktrees/v1-rc-critical-path
git status --short --branch
git log -3 --oneline
```

Run `shepherd_review_changes` for the current run before claiming completion. Expected: no model files or card assets are tracked; only approved documentation/catalog/test commits are present, excluding known harness-owned `.pi` and graphify updates.

- [ ] **Step 3: Report evidence**

Report:
- Public repository URL and immutable 40-character HF revision.
- All three filenames, sizes, digests, file types, and tensor counts.
- Portable profile checksum and import commands.
- Banner filename and dimensions.
- ROCmFPX live-load and stock-rejection outcomes.
- Git commit IDs and exact verification commands.
- Any unresolved hardware-specific performance or profile-tuning risk.
