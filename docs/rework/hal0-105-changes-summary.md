# Handoff prompt — hal0-brain / template / profile changes on lxc105 (hal0, 10.0.1.142)

You are picking up work on **hal0** running in **Proxmox LXC 105** (host `prx` = 10.0.1.110; container hostname `hal0`, 10.0.1.142). Models live on the NFS share **`/mnt/ai-models`** (also mounted on thinMint). hal0 has **no auth**; its control API is `http://127.0.0.1:8080/api/*`. Below is everything changed this session, the current state, and the open gotchas. Verify state with `hal0 slot list`, `hal0 model list`, and `GET /api/profiles` before acting.

## 1. Brain model — trained, exported, quantized
- **Trained `hal0-brain-sft`** on thinMint (RTX 4080) with Unsloth: base **`ewinregirgojr/MiniCPM5-1B-Agentic-Tooluse-Merged-FP16`** (already-agentic MiniCPM5-1B; same `<function name=…><param>` XML tool format hal0 uses), QLoRA r16, 2 epochs, on ~2,781 grounding-verified hal0 Q/A + tool-use conversations. Dataset pipeline: `/home/mint/hal0-brain-trainingset/` (harvest→generate[MiniMax/deepseek]→verify→build). Loss 3.4→1.9.
- **Exported f16 GGUF** → `/mnt/ai-models/chat/hal0-brain-sft/model.gguf` (2.0 GB).
- **Quantized to ROCmFP4** via the parked toolbox `hal0-quant-fc` (had to recreate it **rw** — the old one mounted `/mnt/ai-models` read-only):
  - **`hal0-brain-sft-fpx4`** = `Q4_0_ROCMFP4_STRIX`, 609 MB → `.../hal0-brain-sft-fpx4-agent/model.gguf`
  - **`hal0-brain-sft-fpx8`** = `Q8_0`, 1.1 GB → `.../hal0-brain-sft-fpx8-agent/model.gguf`
  - (No fp8-ROCmFP4 type exists in the fork; Q8_0 is the 8-bit option.)
- **Registered** all three in the hal0 registry: `hal0-brain-sft` (f16), `hal0-brain-sft-fpx4`, `hal0-brain-sft-fpx8` (all capability: chat).

## 2. Profile created
- **`brain-agent-fpx`** (`POST /api/profiles`): backend `rocm`, image `ghcr.io/hal0ai/hal0-rocmfpx:c077206`, cloned_from `rocm-dense-minicpm5`, flags = `--jinja -fa on -ngl 99 -dev ROCm0 -b 512 -ub 512 --threads 16 --threads-batch 32 --no-mmap --metrics --no-webui`.
  - ⚠️ Do NOT put `--chat-template-kwargs '{"enable_thinking":false}'` in profile flags — hal0 mangles the JSON quoting (`{enable_thinking:false}`) and the slot **crashes** at startup. This flag is currently removed.

## 3. Templates
- Downloaded **`froggeric/Qwen-Fixed-Chat-Templates`** (v21.3, minijinja-safe, Qwen 3.5/3.6) → `/mnt/ai-models/chat-templates/qwen-froggeric-v21.jinja`.
- **Tested with hal0's llama.cpp**: loads clean (minijinja-safe), `enable_thinking:false` → clean content, tool-calls parse to proper `tool_calls`. Validated on `ops` (Qwen 4B, clean) and briefly on `agent`/saber (35B — answer + tool_calls work, but an empty `<think>\n\n</think>` block leaks into `content`; cosmetic, fixable with `--reasoning-format`).
- Note: other templates also present that are NOT from this session — `froggeric-qwen-fixed.jinja`, `minicpm5-1b-toolfix.jinja` (someone else iterating). Existing hal0 ones: `minicpm5-1b.jinja`, `minicpm5-1b-v2.jinja`.

## 4. Current slot state (verify live!)
| Slot | Model | Backend/Port | chat_template | Notes |
|---|---|---|---|---|
| agent | chadrock-35b-ace-saber-mtp | vulkan 8082 | **null** | froggeric change did NOT persist (reverted — Hermes reload?) |
| brain | hal0-brain-sft (f16) | rocm 8087 | auto | idle; swapped `--no-persist` |
| nano | hal0-brain-sft-fpx8 | rocm 8086 | auto | idle; the ROCmFP4 target; `--no-persist` |
| ops | qwopus3-5-4b-coder-mtp | vulkan 8091 | **qwen-froggeric-v21** | persisted |
| code | qwopus3-6-27b | rocm 8085 | — | offline (untouched) |
| hal0 | Hal0-BRAINTRAIN-1B-STRIX | vulkan | — | serving (the standing brain, untouched) |

Model swaps used `--no-persist`, so `brain`/`nano` revert to prior defaults on restart unless persisted with `hal0 slot swap <name> --model <id>` (no `--no-persist`).

## 5. Key gotchas / bugs discovered (important)
1. **`--chat-template-kwargs` quoting bug** (hal0): a slot with a **file** `chat_template` + `enable_thinking` makes hal0 emit `--chat-template-kwargs {enable_thinking:false}` (double-quotes stripped by the shell layer in `providers/container.py` — token has no space so it isn't re-quoted) → llama-server JSON parse error → **slot won't start**. Setting `chat_template: auto` avoids it. This is the root cause of the earlier nano crash.
2. **Empty-content / reasoning channel**: MiniCPM5 (and saber) are reasoning models. With `--jinja` and default reasoning-format, the answer lands in `reasoning_content` and `content` is empty unless the request sends `chat_template_kwargs:{enable_thinking:false}` (which injects the empty `<think></think>` and makes the model answer into `content`). The **froggeric** template handles this internally for Qwen without the broken hal0 flag.
3. **hal0-brain identity**: the trained model knows hal0 correctly **only when given the hal0-brain system prompt** ("You are hal0-brain … on AMD Strix Halo …"); without it, it hallucinates. Hermes supplies its own persona, so this is fine in practice.
4. **hal0's llama.cpp forks won't load newer GGUFs** — e.g. `Qwen3.5-4B-UD-Q4_K_XL.gguf` failed on both `rocmfp4-server` and `c077206`. Use models hal0 already serves.
5. Toolbox `hal0-quant-fc`: recreate with `-v /mnt/ai-models:/mnt/ai-models:rw` (default/old was `:ro`); GPU device warnings are benign (LXC quirk); quantize is CPU-only. Serving image for ROCmFP4 STRIX quants: `hal0-rocmfpx:c077206` (supports rocm+vulkan) or `amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server`.

## 6. Open decisions / next steps
- **No-think default for the brain**: still needs a real fix — either (a) fix hal0's `--chat-template-kwargs` quoting bug, or (b) ship a `minicpm5-nothink.jinja` template that defaults `enable_thinking=false` internally (like froggeric does for Qwen) so no runtime kwarg is needed.
- **Persist the brain quant**: decide `nano` = `hal0-brain-sft-fpx4` (609 MB) vs `-fpx8` (1.1 GB, currently loaded), then persist (drop `--no-persist`).
- **agent slot template**: decide whether to re-apply `qwen-froggeric-v21` (it improved tool-calling + fixed empty content, but left a cosmetic `<think>` prefix on the 35B; add `--reasoning-format` to strip). It reverted to null.
- **Deploy the trained brain to `hal0`/BRAINTRAIN slot** (the standing 1B) if it's meant to replace `Hal0-BRAINTRAIN-1B-STRIX`.

## Unrelated but done this session (host-level, for context)
- Fixed the **amdgpu-on-boot** issue on host `prx`: `amdgpu` was blacklisted so LXC 105 (onboot:1) failed to start after reboot (no `/dev/dri/renderD128`). Added `/etc/systemd/system/amdgpu-ready.service` (modprobe + wait for renderD128, `Before=pve-guests.service`) + `/etc/modules-load.d/amdgpu.conf`. hal0 now survives host reboots (not yet reboot-tested).
- Enabled **Hermes API server** (`~/.hermes/.env`: `API_SERVER_ENABLED=true`, `API_SERVER_KEY=change-me-local-dev`, `API_SERVER_HOST=0.0.0.0`) → OpenAI-compatible on `:8642`, model `hermes-agent`. Set Hermes `model.default: agent` in `/var/lib/hal0/.hermes/config.yaml` (backup made). ⚠️ It's on 0.0.0.0 with terminal backend `local` (unsandboxed) + placeholder key — harden before real use.
