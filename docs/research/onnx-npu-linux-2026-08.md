# ONNX text-generation on the AMD XDNA2 NPU under Linux — state of the world, August 2026

Research note feeding a hal0 ADR. Every claim below is cited to a primary source (vendor docs,
GitHub repos/issues, kernel/driver trees, release notes). Retrieved 2026-08-20.

> **Note on placement.** This repo has no existing research-note convention — `docs/` contains
> `adr/`, `concepts/`, `getting-started/`, `guides/`, `operate/`, `reference/`, `superpowers/`,
> and no `docs/research/`, `docs/.devdocs/`, or `notes/`. Per the brief, this note is filed at
> `docs/research/onnx-npu-linux-2026-08.md` and creates that directory.

---

## Executive summary

**Not viable.** ONNX-based LLM text generation on the XDNA2 NPU does **not** work on Linux today,
and there is no announced path for it. AMD's ONNX LLM stack for Ryzen AI — the Vitis AI /
`ryzenai-llm` execution provider driving ONNX Runtime GenAI (OGA) in NPU-only and hybrid modes —
is Windows-only by AMD's own documentation: the Ryzen AI Software install page states plainly
"This page covers Ryzen AI installation on Windows", the ONNX Runtime Vitis AI EP support table
lists **Windows** as the only supported OS for the Ryzen AI family, upstream `onnxruntime-genai`'s
own support matrix has no VitisAI/RyzenAI entry at all under Hardware Acceleration, and Lemonade's
FAQ says "Ryzen AI SW's implementation of NPU and hybrid inference is currently supported only on
Windows." Attempts to force the Vitis AI EP on Linux land 100% of operators on CPU because the
`voe.passes` graph-optimization wheels are not published for `linux_x86_64`. Separately, Strix Halo
(STX-H) is not even on the Ryzen AI OGA supported-processor list (Strix and Krackan Point only),
and the Ryzen AI SDK installer refuses the STX-H SKU. What *does* work on Linux — and has since
FastFlowLM 0.9.35 / Lemonade 10.0 in March 2026 — is **NPU-only LLM serving via FastFlowLM (FLM)**,
which uses its own proprietary NPU kernels and its own model format, not ONNX. hal0 already takes
that path. The NPU's realistic niche on Strix Halo is **low-power, always-on background models and
fast TTFT**, not peak throughput: community head-to-heads put an 8B-class model at ~8 t/s decode on
the NPU versus ~20 t/s on the Radeon 8060S iGPU, with the NPU winning time-to-first-token 2.3× and
drawing single-digit watts. Hybrid NPU-prefill + iGPU-decode is a Windows-only OGA feature; the
Linux equivalents are third-party speculative-decoding glue, not a supported mode.

---

## 1. `amdxdna` kernel driver and XRT userspace on Linux

**Mainline status.** The `amdxdna` DRM/accel driver was merged for **Linux 6.14**. Phoronix:
"only at the start of the 2025 calendar year when it was submitted for the mainline Linux kernel as
part of Linux 6.14"
(<https://www.phoronix.com/news/Ryzen-AI-NPU-Linux-Power-Metric>). NPU6 — the Strix Halo NPU
variant — was added in time for that same 6.14 debut
(<https://www.phoronix.com/news/Ryzen-AI-NPU6-Linux-6.14>).

**Device identity.** AMD's out-of-tree `xdna-driver` maps Strix Halo to **NPU6, PCI device
`0x17f0`**, alongside NPU1 (Phoenix, `0x1502`), NPU3 (Strix, `0x17f1/2/3`), NPU4 (Strix Point) and
NPU5 (Krackan) — see the hardware matrix derived from
`src/driver/amdxdna/amdxdna_pci_drv.c` at <https://deepwiki.com/amd/xdna-driver>. The device node
is `/dev/accel/accelN`; a live probe on Strix Halo reports "`[Linux] NPU: /dev/accel/accel0 with 8
columns`" (<https://sleepingrobots.com/dreams/lemonade-server-npu-strix-halo>).

**Firmware.** NPU firmware blobs ship in `linux-firmware` under `/lib/firmware/amdnpu/`
(<https://wiki.gentoo.org/wiki/User:Lockal/AMDXDNA>). They are **version-coupled to the driver**,
and distro pairs are frequently mismatched. Filed against AMD as
<https://github.com/amd/xdna-driver/issues/1219>: Ubuntu 25.10 ships `npu.sbin 1.0.0.166`, which is
below FastFlowLM's `>= 1.1.0.0` requirement; upgrading firmware from `linux-firmware.git` to
1.1.2.65 then breaks against the in-tree driver with

```
amdxdna 0000:c7:00.1: [drm] ERROR aie2_check_protocol: Incompatible firmware protocol major 7 minor 2
amdxdna 0000:c7:00.1: [drm] ERROR aie2_hw_start: firmware is not alive
```

The working combination in that report is the DKMS `xdna-driver` 2.23.0 built from source plus its
bundled `npu.dev.sbin` 255.0.11.71 — i.e. **build from source, not distro packages**.

**In-tree lag.** The Gentoo wiki page documents that the kernel-tree module "lags about a year"
behind AMD's GitHub tree and that "AMD's XRT runtime plugin and AI software like MLIR-AIE won't
work in this setup", recommending the out-of-tree module instead
(<https://wiki.gentoo.org/wiki/User:Lockal/AMDXDNA>). Consistent with that, Lemonade's Linux NPU
guide requires "Upstream NPU driver in the Linux **7.0+** kernel (with backports for 6.xx kernels)"
(<https://lemonade-server.ai/flm_npu_linux.html>), and Phoronix confirms "you also need to be using
the Linux 7.0 kernel or the AMDXDNA driver back-ports … due to some last minute accelerator driver
tweaks" (<https://www.phoronix.com/news/AMD-Ryzen-AI-NPUs-Linux-LLMs>).

**Distro packaging.** Two real paths exist today:

- Ubuntu 24.04/25.10 via AMD's Lemonade PPA: `add-apt-repository ppa:lemonade-team/stable` then
  `apt install libxrt-npu2 amdxdna-dkms`, reboot
  (<https://fastflowlm.com/docs/install_lin>, <https://lemonade-server.ai/flm_npu_linux.html>).
- Arch: `pacman -S xrt xrt-plugin-amdxdna` (same sources).

**Boot-time footguns**, both documented: `amd_iommu=off` on the kernel command line — a common
Strix Halo llama.cpp tweak — **disables the NPU entirely** and `/dev/accel/` never appears
(<https://lemonade-server.ai/flm_npu_linux.html>; reproduced at
<https://sleepingrobots.com/dreams/lemonade-server-npu-strix-halo>, fixed with `amd_iommu=pt`). And
`memlock` must be `unlimited` (`ulimit -l`), otherwise the runtime fails
(<https://fastflowlm.com/docs/install_lin>).

**Verdict on Q1:** kernel and firmware plumbing is real and upstream, but the supported
configuration is a moving target that in practice still wants a 7.0-class kernel or a DKMS
out-of-tree driver plus matched dev firmware.

---

## 2. `onnxruntime-genai` with VitisAI / Ryzen AI EP on Linux

**No.** ONNX LLM text-gen on the NPU is Windows-only today.

- **ONNX Runtime's own Vitis AI EP doc** lists supported OS per target family. For
  "AMD64 / Ryzen AI / AMD Ryzen processors with NPUs" the Supported OS column is **Windows**. Only
  the Arm Adaptable-SoC targets (Zynq UltraScale+, Versal) list Linux
  (<https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html>).
- **AMD's Ryzen AI Software 1.8.0 install page** opens with "This page covers Ryzen AI installation
  on Windows", and the prerequisites table requires Windows 11 build ≥ 22621.3527 and Visual Studio
  2022 (<https://ryzenai.docs.amd.com/en/latest/inst.html>).
- **The OGA flow page** — the ONNX GenAI path for LLMs, covering both hybrid and NPU-only execution
  — requires the Windows NPU driver + Ryzen AI MSI installer and Git for Windows
  (<https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html>).
- **Upstream `onnxruntime-genai`'s support matrix** lists Hardware Acceleration as
  "CPU, CUDA, DirectML, NvTensorRtRtx (TRT-RTX), OpenVINO, QNN, WebGPU", with "AMD GPU" only on the
  roadmap. **There is no VitisAI / RyzenAI NPU entry at all** — AMD's NPU OGA support is a
  downstream distribution shipped in the Ryzen AI MSI, not an upstream ORT-GenAI EP
  (<https://github.com/microsoft/onnxruntime-genai>).
- **Lemonade's FAQ**, question "Does LLM inference with the NPU only work on Windows?", answers:
  "Today, NPU-only inference on Linux is available via FastFlowLM… At the moment, Ryzen AI SW's
  implementation of NPU and hybrid inference is currently supported only on Windows."
  (<https://lemonade-server.ai/docs/guide/faq>)

**What happens if you try anyway.** `amd/RyzenAI-SW` issue #341 (Feb 2026) is the definitive
field report: on Ubuntu x86_64 with `amdxdna` loaded and `/dev/accel/accel0` accessible, ORT 1.20.1
built with the Vitis AI EP initializes the EP successfully — and then partitions **zero** operators
to the NPU:

```
[Vitis AI EP] No. of Operators : CPU 30
```

Root causes the reporter isolated: the `voe.passes` graph-optimization module is **not published
for `linux_x86_64`**, plus a pointer bug in the config parser; the prebuilt CVML Python wheel also
segfaults from C++11 ABI mismatch on Linux
(<https://github.com/amd/RyzenAI-SW/issues/341>). The identical symptom was reported a year earlier
in issue #178 — "when inferencing on Linux, onnxruntime is mapping all operators to CPU", including
after manually setting `XLNX_VART_FIRMWARE` to the Strix xclbin
(<https://github.com/amd/RyzenAI-SW/issues/178>). Note both cases are ordinary CNNs (ResNet-50);
LLM text-gen is strictly further out.

**Which release added Linux NPU support?** For the ONNX/OGA path: **none, as of Ryzen AI Software
1.8.0**. The 1.8.0 release notes mention Linux only for *quantizer compilation* ("Supported
compilation for Windows and Linux"), never for the runtime/EP
(<https://ryzenai.docs.amd.com/en/latest/relnotes.html>).

**Supported model/quant formats (on Windows, for reference).** The NPU is integer-first: AMD
documents quantizing to 8-bit integer with floating-point models internally converted to bfloat16,
and the Vitis AI EP partitions the graph so only ops it supports at that precision land on the NPU
(<https://ryzenai.docs.amd.com/en/latest/>, summarized at
<https://multigrid.ai/learn/ryzen-ai-npu-inference>). The pre-optimized OGA LLM set covers Llama-2,
Llama-3, Mistral, DeepSeek distills, Qwen-2/2.5/3, Gemma-2/3, GPT-OSS, Phi-3/3.5/4, in hybrid or
NPU-only variants (Token Fusion for long context to 16K, Full Fusion for peak performance)
(<https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html>,
<https://ryzenai.docs.amd.com/en/latest/llm/overview.html>).

**Strix Halo is doubly excluded.** The OGA flow page's Supported Configurations section reads:
"The Ryzen AI OGA flow supports **Strix and Krackan Point** processors. Phoenix (PHX) and Hawk
(HPT) processors are not supported." — Strix **Halo** is absent
(<https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html>). A Strix Halo owner filed
`amd/RyzenAI-SW` issue #366 (Apr 2026) asking AMD to add STX-H to the Linux supported-SKU list,
reporting Ryzen AI 1.7.1 install **blocked on the SKU check** with `amdxdna` loaded and the device
node enumerated: "The hardware and the kernel driver are both ready. The gap is Ryzen AI userspace."
(<https://github.com/amd/RyzenAI-SW/issues/366>). Still open.

**Verdict on Q2:** ONNX text-gen on NPU under Linux is not supported, not partially working, and
not on a published roadmap. hal0 should treat it as unavailable.

---

## 3. AMD Lemonade — Linux support matrix

The canonical repo's Supported Configurations table (<https://github.com/lemonade-sdk/lemonade>)
is the decisive citation:

| Modality | Engine | Backend | Device | OS |
|---|---|---|---|---|
| Text generation | `llamacpp` | `vulkan` | x86_64 CPU, AMD iGPU/dGPU | Windows, Linux |
| | `llamacpp` | `rocm` | AMD GPUs supported by ROCm | Windows, Linux |
| | `llamacpp` | `cpu` | x86_64 CPU | Windows, Linux |
| | **`flm`** | **`npu`** | **XDNA2 NPU** | **Windows, Linux** |
| | **`ryzenai-llm`** | **`npu`** | **XDNA2 NPU** | **Windows** |
| | `vllm` (experimental) | `rocm` | Strix Halo iGPU (gfx1151) | Linux |
| Speech-to-text | `whispercpp` | `npu` | XDNA2 NPU | Windows |

Read the two NPU rows together: **the ONNX/Ryzen-AI NPU engine (`ryzenai-llm`) is Windows-only;
the NPU works on Linux only through `flm`.** So Linux does *not* silently fall back to GPU/CPU for
NPU workloads — it has a real NPU path, just a non-ONNX one. (An older fork snapshot,
<https://github.com/Mintplex-Labs/lemonade-sdk>, still shows `flm` as Windows-only; that predates
the March 2026 Linux release and should not be cited.)

Note also `whispercpp | npu` is Windows-only in that table, while Phoronix reports Lemonade 10.0
brought "Linux NPU support for large language models **as well as Whisper**"
(<https://www.phoronix.com/news/AMD-Ryzen-AI-NPUs-Linux-LLMs>) — Whisper-on-NPU under Linux runs
through FLM's own Whisper support (<https://fastflowlm.com/docs/models>), not `whispercpp`.

Lemonade 10.0, released alongside FastFlowLM 0.9.35, is the release that turned Linux NPU on:
"Lemonade for its Linux Ryzen AI NPU support is building off FastFlowLM… FastFlowLM 0.9.35 released
this morning and it comes with official native Linux support"
(<https://www.phoronix.com/news/AMD-Ryzen-AI-NPUs-Linux-LLMs>).

---

## 4. FastFlowLM (FLM)

**What it is and where it lives.** FLM is an "NPU-first runtime built exclusively for Ryzen AI",
Ollama-shaped CLI (`flm run`, `flm serve`, `flm pull`, `flm validate`), ~16–17 MB, context to 256K
tokens. The team joined AMD (07/2026) and the repo moved into AMD's ROCm org as
`ROCm/FastFlowLM` v1.0.0 (08/11/2026) (<https://github.com/ROCm/FastFlowLM>, <https://fastflowlm.com>).

**Hardware.** "FLM supports all Ryzen AI Series chips with XDNA2 NPUs (Strix, Strix Halo, Kraken,
and Gorgon Point)" (<https://github.com/ROCm/FastFlowLM>). The Linux page's table lists Max
300-series / Strix Halo as **Supported**, and explicitly excludes XDNA1 (Ryzen AI 7000/8000/200)
(<https://fastflowlm.com/docs/install_lin>).

**Linux.** Native Linux support shipped 03/11/2026: "FLM now supports Linux 🐧"
(<https://github.com/ROCm/FastFlowLM>). Deb packages for Ubuntu/Debian, Arch instructions, and a
CMake `linux-default` preset installing to `/opt/fastflowlm` build from source
(<https://fastflowlm.com/docs/install_lin>, <https://github.com/ROCm/FastFlowLM>). Models default
to `~/.config/flm/`, overridable with `FLM_MODEL_PATH`.

**Model list and formats.** Families: LLaMA, DeepSeek, Qwen (incl. Qwen3.5 up to 9B and VL
variants), Gemma / MedGemma / TranslateGemma, gpt-oss, LiquidAI LFM, Phi, Nanbeige, Whisper,
EmbeddingGemma, SmolVLA (<https://fastflowlm.com/docs/models>). Formats are **FLM's own** —
Lemonade's model library describes "GGUF, FLM, and ONNX" as three distinct formats
(<https://github.com/lemonade-sdk/lemonade>), and FLM pulls "optimized model kernels" from
HuggingFace at `flm pull` time (<https://github.com/ROCm/FastFlowLM>). Weight quantization is
FLM-specific (benchmark pages cite e.g. `Q4_1`, "4-bit with bias" — <https://fastflowlm.com>).

**Licensing caveat.** The NPU kernels are **proprietary binaries**, free for non-commercial use
with commercial licensing terms — Lemonade labels the integration "Early Access. FLM is free for
non-commercial use, however note that commercial licensing terms apply"
(quoted at <https://news.ycombinator.com/item?id=47612724>; corroborated at
<https://lilting.ch/en/articles/amd-lemonade-local-ai-gpu-npu-server>). The llama.cpp GPU path
stays fully open.

**Coverage vs ONNX-genai.**

| | FLM | ONNX Runtime GenAI + Ryzen AI EP |
|---|---|---|
| Linux NPU | Yes (since 0.9.35, Mar 2026) | No |
| Strix Halo | Supported | Not on OGA supported-processor list |
| Model format | FLM-native (proprietary kernels) | ONNX (int4/int8 QDQ, bf16 internal) |
| Bring-your-own model | No — curated catalog only | Yes, via OGA model-prep flow for supported architectures |
| Context | up to 256K | 16K (Token Fusion NPU) / 4K default hybrid |
| Hybrid NPU+iGPU | No (NPU-only) | Yes — Windows only |
| Openness | proprietary kernels, commercial terms | AMD-distributed, Windows MSI |

The only thing ONNX-genai would buy over FLM is **arbitrary-model portability** (any supported
architecture you can quantize yourself) and **hybrid execution** — and neither is reachable on
Linux.

---

## 5. Community evidence: real numbers on Linux

**Strix Halo, NPU vs iGPU head-to-head** (Fedora 43, kernel 6.19.8, FLM via Lemonade, 8-column NPU;
<https://sleepingrobots.com/dreams/lemonade-server-npu-strix-halo>):

| Metric | GPU — Qwen3.5-9B, llama.cpp Vulkan | NPU — Qwen3-8B, FLM |
|---|---|---|
| TTFT | 5.12 s | **2.22 s** (2.3× faster) |
| Decode | **19.7 t/s** (2.4× faster) | 8.2 t/s |

Same source, Llama-3.2-1B on NPU: TTFT 0.65 s, decode **39.5 t/s**, prefill throughput 83.6 t/s.
The author's conclusion is the operational one: "NPU handles the text summarization and
classification tasks that don't need tool calling or multimodal capabilities. GPU handles
everything that does… the NPU chugs through background tasks without affecting its performance."

**Vendor numbers** (Kraken/Strix Point class, comparable to Strix Halo per AMD's own note): GPT-OSS
20B at **19 tok/s**; Qwen3 0.6B at 80 tok/s with 1,356 tok/s prefill on a 2K prompt; Gemma3 1B at
66 tok/s with 1,657 tok/s prefill on 16K; LFM2-1.2B decode 62→46 tok/s from 1K→32K context with
prefill 1,537–2,677 tok/s; LFM2-2.6B decode ~30→25 tok/s
(<https://fastflowlm.com/benchmarks>, <https://fastflowlm.com/docs/benchmarks/lfm2_results>).
Note the prefill numbers — that is where the NPU is genuinely strong.

**Power.** FLM claims "67.2× less energy per token than the integrated GPU and 222.9× less energy
per token than the CPU on the same chip while holding higher throughput"
(<https://fastflowlm.com/benchmarks>) and "Power draw (CPU + NPU) **< 2 W** … vs ~25 W GPU baseline"
for a full assistant stack (<https://fastflowlm.com>). These are vendor figures; treat the ratios as
marketing-grade but the order of magnitude (single-digit watts vs tens of watts) is consistent with
the independent reports. Kernel-side, NPU power metrics via the PMF driver are still working their
way upstream (<https://www.phoronix.com/news/Ryzen-AI-NPU-Linux-Power-Metric>), so independent
Linux-side power measurement is not yet trivial.

**Where the NPU actually wins.** Not peak throughput. On Strix Halo the Radeon 8060S is
comfortably faster for chat-sized work — community llama.cpp numbers on the same box run ~50 tok/s
for GPT-OSS-120B and 43–71 tok/s for 20–30B-class models
(<https://news.ycombinator.com/item?id=47612724>,
<https://dev.to/max_quimby/amds-lemonade-just-made-every-nvidia-only-ai-guide-obsolete-2a3l>).
The consensus from Strix Halo owners: "the NPU is used for low-powered, small models that are
'always on', so it's not a huge win for the standard chatbot use case"
(<https://news.ycombinator.com/item?id=47612724>). The NPU's wins are (a) energy per token,
(b) time-to-first-token, and (c) **being a second accelerator** — it runs background summarization,
classification, STT, and embeddings without stealing iGPU cycles or GTT bandwidth from the main
chat model.

Counterpoint worth noting: on **Krackan Point** (a much weaker iGPU than the 8060S) the NPU wins
outright — TranslateGemma-4B at ~20 t/s on NPU vs ~10 t/s on llama.cpp Vulkan, with better TTFT
(<https://dev.webonomic.nl/using-the-npu-on-krackan-point-for-llms-is-a-real-gain-2x-as-fast-as-gpu>).
The NPU-vs-iGPU verdict is platform-specific; Strix Halo has the strongest iGPU in the family, so
it is the *worst* case for NPU throughput relative to iGPU.

---

## 6. Hybrid modes (NPU prefill + iGPU decode)

**Windows only.** Hybrid execution is an OGA feature: "Hybrid execution mode: This mode uses both
the NPU and iGPU to achieve the best TTFT and TPS during the prefill and decode phases"
(<https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html>), and OGA on Ryzen AI requires the
Windows install. Lemonade's FAQ confirms the semantics ("The NPU handles prompt processing. The GPU
handles token generation.") and the OS limit in the same answer set: "Ryzen AI SW's implementation
of NPU and hybrid inference is currently supported only on Windows"
(<https://lemonade-server.ai/docs/guide/faq>).

FLM is **NPU-only** by design — there is no hybrid mode in the Linux stack.

What exists on Linux is third-party glue: `mikealanni/strix-halo-pipeline` runs FLM (Qwen3-1.7B) on
the NPU as a **draft model** and llama.cpp (Qwen3-8B) on the iGPU as verifier behind one
OpenAI-compatible endpoint, on Ryzen AI Max+ 395 / Ubuntu 26.10 / kernel 7.0-rc3. Its own README is
candid: "For a single request, execution is overlapped but sequential — NPU generates first, GPU
continues. True simultaneous same-token generation would require shared KV cache between FLM and
llama.cpp, which doesn't exist."
(<https://github.com/mikealanni/strix-halo-pipeline>). Interesting prior art, not a supported mode.

---

## Concrete blockers (why ONNX-on-NPU-on-Linux is dead today)

1. **No Linux Ryzen AI runtime.** Ryzen AI Software 1.8.0 installs on Windows only
   (<https://ryzenai.docs.amd.com/en/latest/inst.html>).
2. **Vitis AI EP declares Windows-only for Ryzen AI targets**
   (<https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html>).
3. **`voe.passes` graph-optimization wheels are not published for `linux_x86_64`**, so even a
   hand-built EP partitions 100% of ops to CPU (<https://github.com/amd/RyzenAI-SW/issues/341>).
4. **Prebuilt Linux Python wheels have C++11 ABI mismatches** causing segfaults (same issue).
5. **Strix Halo is not an OGA-supported processor**, and the SDK installer rejects the STX-H SKU
   (<https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html>,
   <https://github.com/amd/RyzenAI-SW/issues/366>).
6. **Upstream `onnxruntime-genai` has no NPU EP entry**, so there is no vendor-independent
   fallback path (<https://github.com/microsoft/onnxruntime-genai>).
7. **Driver/firmware version coupling** still requires DKMS + dev firmware on mainstream distros
   (<https://github.com/amd/xdna-driver/issues/1219>).
8. **Hybrid NPU+iGPU is Windows-only** (<https://lemonade-server.ai/docs/guide/faq>).

---

## Trigger conditions to revisit

Revisit this ADR if **any** of the following becomes true:

1. **AMD publishes Linux `voe` / Vitis AI EP artifacts** — a `voe*-linux_x86_64` wheel or a
   Ryzen AI Linux install page appears at <https://ryzenai.docs.amd.com/en/latest/inst.html>.
   Watch <https://github.com/amd/RyzenAI-SW/issues/341> for closure.
2. **The ORT Vitis AI EP support table adds Linux to the Ryzen AI row**
   (<https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html>).
3. **`onnxruntime-genai` upstream adds a VitisAI/RyzenAI/XDNA entry** to its Hardware Acceleration
   support matrix (<https://github.com/microsoft/onnxruntime-genai>).
4. **Strix Halo (STX-H) appears on the Ryzen AI OGA supported-processor list** — track
   <https://github.com/amd/RyzenAI-SW/issues/366>.
5. **Lemonade's Supported Configurations table flips `ryzenai-llm | npu` to "Windows, Linux"**
   (<https://github.com/lemonade-sdk/lemonade>) — this is the single cheapest signal to poll.
6. **Lemonade's FAQ answer to "Does LLM inference with the NPU only work on Windows?" changes**
   (<https://lemonade-server.ai/docs/guide/faq>) — likewise for the hybrid-mode sentence, which
   would be the trigger for hybrid NPU-prefill/iGPU-decode on Linux.
7. **An open-source XDNA2 LLM backend lands** — e.g. `iree-amd-aie` / `mlir-aie` gaining an
   end-to-end LLM path, or llama.cpp acquiring an XDNA backend — which would make the FLM
   proprietary-kernel licensing question moot.
8. **FLM licensing changes** (the ROCm-org move at
   <https://github.com/ROCm/FastFlowLM> makes an eventual open-sourcing of the kernels plausible)
   — relevant to hal0's redistribution story even though it does not change the ONNX answer.
9. **Distro-shipped matched driver+firmware pairs** land (Ubuntu 26.04 LTS is the candidate), which
   would remove the DKMS/build-from-source requirement — track
   <https://github.com/amd/xdna-driver/issues/1219>.

---

## Source table

| URL | What it establishes | Credibility |
|---|---|---|
| <https://ryzenai.docs.amd.com/en/latest/inst.html> | "This page covers Ryzen AI installation on Windows"; Windows 11 + VS2022 prerequisites | Primary (AMD docs) |
| <https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html> | OGA hybrid + NPU-only modes; Windows requirements; "supports Strix and Krackan Point processors" | Primary (AMD docs) |
| <https://ryzenai.docs.amd.com/en/latest/llm/overview.html> | OGA is the NPU-only/hybrid LLM path; supported LLM architectures | Primary (AMD docs) |
| <https://ryzenai.docs.amd.com/en/latest/relnotes.html> | 1.8.0 notes; Linux mentioned only for quantizer compilation | Primary (AMD docs) |
| <https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html> | Ryzen AI family → Supported OS = Windows | Primary (ORT docs) |
| <https://github.com/microsoft/onnxruntime-genai> | Support matrix: no VitisAI/RyzenAI hardware-acceleration entry | Primary (source repo) |
| <https://github.com/amd/RyzenAI-SW/issues/341> | Linux Vitis AI EP: 0 ops offloaded, missing `voe.passes` for linux_x86_64, ABI segfaults | Primary (vendor issue tracker) |
| <https://github.com/amd/RyzenAI-SW/issues/178> | Same all-CPU-fallback symptom, Ryzen AI 1.4, Ubuntu | Primary (vendor issue tracker) |
| <https://github.com/amd/RyzenAI-SW/issues/366> | Strix Halo SKU rejected by Ryzen AI 1.7.1 installer on Linux; open | Primary (vendor issue tracker) |
| <https://github.com/amd/xdna-driver/issues/1219> | Driver/firmware version coupling; exact `aie2_check_protocol` error; DKMS fix | Primary (vendor issue tracker) |
| <https://deepwiki.com/amd/xdna-driver> | NPU6 = Strix Halo, PCI 0x17f0; device matrix from `amdxdna_pci_drv.c` | Secondary (source-derived) |
| <https://www.phoronix.com/news/Ryzen-AI-NPU6-Linux-6.14> | NPU6/Strix Halo support added for the 6.14 debut | Secondary (reliable trade press) |
| <https://www.phoronix.com/news/Ryzen-AI-NPU-Linux-Power-Metric> | amdxdna submitted for mainline as part of Linux 6.14; PMF power metrics still in review | Secondary |
| <https://www.phoronix.com/news/AMD-Ryzen-AI-NPUs-Linux-LLMs> | Lemonade 10.0 + FLM 0.9.35 = Linux NPU LLM; needs kernel 7.0 or backports | Secondary |
| <https://github.com/lemonade-sdk/lemonade> | Supported Configurations table: `flm/npu` Windows+Linux, `ryzenai-llm/npu` Windows | Primary (source repo) |
| <https://lemonade-server.ai/docs/guide/faq> | "Ryzen AI SW's implementation of NPU and hybrid inference is currently supported only on Windows" | Primary (vendor docs) |
| <https://lemonade-server.ai/flm_npu_linux.html> | Linux NPU stack: kernel 7.0+/backports, IRON compiler, FLM, Lemonade; PPA packages; `amd_iommu=off` warning | Primary (vendor docs) |
| <https://fastflowlm.com/docs/install_lin> | XDNA2-only support table incl. Strix Halo; Ubuntu/Arch install; memlock | Primary (vendor docs) |
| <https://github.com/ROCm/FastFlowLM> | Now in AMD ROCm org (v1.0.0, 08/2026); Linux since 03/11/2026; all XDNA2 chips; model paths | Primary (source repo) |
| <https://fastflowlm.com/docs/models> | Model family catalog incl. Whisper, EmbeddingGemma, SmolVLA | Primary (vendor docs) |
| <https://fastflowlm.com/benchmarks>, <https://fastflowlm.com/docs/benchmarks/lfm2_results> | GPT-OSS 20B @ 19 tps; LFM2 decode/prefill tables; 67.2× energy claim | Primary (vendor, self-reported) |
| <https://fastflowlm.com> | "< 2 W (CPU+NPU)" vs "~25 W GPU baseline"; 20–80 tok/s range | Primary (vendor, marketing) |
| <https://sleepingrobots.com/dreams/lemonade-server-npu-strix-halo> | Independent Strix Halo Linux run: `flm validate` output, NPU vs GPU TTFT/decode table, `amd_iommu=pt` fix | Secondary (independent practitioner, reproducible detail) |
| <https://github.com/mikealanni/strix-halo-pipeline> | Linux NPU-draft/iGPU-verify speculative pipeline; no shared KV cache | Primary (source repo) |
| <https://dev.webonomic.nl/using-the-npu-on-krackan-point-for-llms-is-a-real-gain-2x-as-fast-as-gpu> | Krackan Point counterpoint: NPU 2× the Vulkan iGPU | Secondary (independent) |
| <https://news.ycombinator.com/item?id=47612724> | Strix Halo owners: FLM is "the only way to utilize NPU on Ryzen AI CPUs on linux"; NPU for always-on small models; GPT-OSS-120B ~50 t/s on iGPU; FLM licensing quote | Tertiary (community, corroborated) |
| <https://wiki.gentoo.org/wiki/User:Lockal/AMDXDNA> | In-tree module lags AMD's tree ~1 year; firmware in `/lib/firmware/amdnpu/` | Secondary (distro wiki) |
| <https://lilting.ch/en/articles/amd-lemonade-local-ai-gpu-npu-server> | Lemonade backend matrix incl. `ryzenai-llm` = Windows; FLM proprietary kernels | Secondary |
| <https://multigrid.ai/learn/ryzen-ai-npu-inference> | NPU is integer-first; fp models internally bf16; EP partitions graph | Secondary (summarizes AMD docs) |

## Open questions

- **`amd_iommu=off` interaction with hal0's own tuning.** hal0 documents Strix Halo GTT tuning; if
  any hal0 guide or installer recommends `amd_iommu=off` for iGPU throughput, that silently kills
  the NPU. Worth an audit of `docs/` and the installer.
- **Independent power measurement on Linux.** The 67.2×/222.9× energy figures are vendor
  self-reported and the kernel PMF power-metric path is not yet upstream, so hal0 cannot currently
  verify NPU wattage from its own hardware-metrics panel with confidence.
- **FLM commercial licensing** and what it means for hal0 redistribution — outside this note's
  scope but decision-relevant.
