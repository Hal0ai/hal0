# ADR-0056: ggml-xdna is an optional NPU runner, kernels baked at image build

## Status

**ACCEPTED (2026-11-05).** An operator decision. Builds on ADR-0027 (runner
tiers), ADR-0028 (runner system, pin table), ADR-0047 (the closest analogue:
a closed external-image optional runner). ADR-0003 (FLM is the default NPU
lane) stands; this adds a second, opt-in NPU lane beside it.

## Context

hal0's one NPU lane today is FLM: a closed engine-side model list on the
Ryzen AI NPU. Tether's `qvac-fabric-llm.cpp` fork of llama.cpp
(`github.com/tetherto/qvac-fabric-llm.cpp` @ `72f6dfa`, backend
`ggml/src/ggml-xdna/`) runs plain GGUF checkpoints on the same AMD XDNA2
NPU hardware through open MLIR-AIE kernels, instead of a closed model list.
A spike (`/var/lib/hal0/work/notes/npu-ggml-xdna-spike-2026-11-05.md`)
proved it works, found and worked around a correctness bug, and measured
throughput:

- Host stack: in-tree `amdxdna` kernel driver, NPU firmware >= 1.1.2.65, XRT
  2.25.0, `/dev/accel/accel0`, `iommu=pt amd_iommu=on`, fabric clock held
  high (ADR-0054's guard already exists for the GPU side of this), memlock
  unlimited, and `xrt-smi configure --pmode turbo` run on the host (+15%
  pp, +20% tg over default pmode; this is a host-side knob, not something
  hal0's container can set).
- Kernel toolchain is pinned hard: `mlir_aie==1.4.3`,
  `llvm-aie==22.0.0.2026090701+3e93bf7b`. A newer `llvm-aie` overflows the
  fused kernel's program memory and the model fails to load, not just runs
  slower. Kernels are built once (`-DGGML_XDNA_BUILD_KERNELS=ON`, ~10
  minutes, 15 xclbins with a sha256 manifest) and baked into the runner
  image, not built per-host.
- **Correctness bug:** the fused SwiGLU-to-down-projection kernel path
  (`GGML_XDNA_PGEMM_GLU_A=1`, the ggml-xdna default) corrupts prefill
  logits. The fix is `GGML_XDNA_PGEMM_GLU_A=0`, which the spike's bisection
  confirmed restores top-1 agreement with the CPU reference across all nine
  tested prompt lengths (M=2..96).
- The fused single-kernel SSM decode path only applies to one geometry:
  Qwen3.5's GDN shape (embedding length 1024, 0.8B), and only once the
  `blk.0.ssm_out.weight` tensor is requantized to Q5_K (`llama-quantize
  --tensor-type ssm_out=q5_K <bf16-in> <out> Q4_K_M`). Any other model, or
  this one without the requant, falls back to a correct but slower per-op
  path — nothing refuses to load, it is just not fused.
- Reference numbers (Qwen3.5-0.8B-Q4_K_M-so5, `GLU_A=0`, pmode turbo, idle
  NPU): pp512 ~1330 t/s, tg128 ~44 t/s.
- The NPU is one piece of silicon. Running `xdna` (llama.cpp on the NPU)
  beside `flm` (the closed engine, also on the NPU) is coexistence on
  shared hardware, not two independent devices — the two arbiters must
  know about each other, not just about the box's memory budget.

The fork is a third party's and is not upstream llama.cpp: hal0 builds and
ships the image itself (unlike halogen's vendor-pinned image, ADR-0047),
but still cannot fix the fork's bugs faster than Tether merges them, and
carries the toolchain pins as hal0's own maintenance burden.

## Decision

1. **`xdna` is an optional-tier runner hal0 builds, not an external image.**
   Registry key `xdna`, `runtime_family = "llama-server"` (it is still a
   llama.cpp binary, just one built against `ggml-xdna` instead of stock
   ggml), `device_class = "npu"`, `backend = "npu"`, `format_arch = "gguf"`,
   title **NPU (ggml-xdna)**. Tier `optional`: not default, not returned by
   `runner_for_backend` unless a profile or slot names it explicitly, same
   shape as halogen (ADR-0047 decision 1) but hal0-built rather than
   vendor-pinned. A box that never creates an `xdna` slot never pulls the
   image.

2. **Kernels are baked at image build, pinned exactly.** The runner image
   (`runners/xdna/` in hal0-runner-images) builds `ggml-xdna` against
   `mlir_aie==1.4.3` and `llvm-aie==22.0.0.2026090701+3e93bf7b`, with
   `-DGGML_XDNA_BUILD_KERNELS=ON`, and ships the resulting xclbins plus
   their sha256 manifest baked in (`GGML_XDNA_KERNEL_DIR`). A host never
   builds a kernel: the pinned toolchain versions are a correctness
   constraint (the program-memory overflow above), not a performance
   tune, so there is no "use whatever mlir-aie the host has" escape hatch.
   A toolchain bump is a new image build plus a hardware-gate re-run,
   exactly like a halogen digest bump (ADR-0047 consequence).

3. **`GGML_XDNA_PGEMM_GLU_A=0` is a mandatory image-level default, not a
   tunable.** It is set in the image's entrypoint environment, ahead of
   anything a profile's free-text flags could override, because the `=1`
   default corrupts prefill logits silently (wrong tokens, not a crash).
   This is the same shape as halogen's hal0-managed env layer winning over
   profile flags (ADR-0047 decision 3): a correctness fact is not a knob an
   operator can accidentally flip back on. The other `GGML_XDNA_*` knobs
   the fork's README documents stay profile-settable free text, because
   the spike's env-knob sweep found the fork's own defaults were already
   optimal for the rest (`OMP_WAIT_POLICY=PASSIVE` ships as a default
   alongside `GLU_A=0`; `-t 8` and `TOKEN=0` were measured worse and are
   not set).

4. **Model fit is fused-vs-per-op, computed from GGUF metadata, never
   refused outright.** `xdna_model_fit` (`src/hal0/slots/model_fit.py`)
   reads the GGUF header and tensor-info block: `fused` requires
   `general.architecture == "qwen35"`, `embedding_length == 1024`, and the
   `blk.0.ssm_out.weight` tensor's ggml type to be Q5_K; anything else is
   `per-op` — correct, just without the fused single-kernel decode path,
   same as the spike measured. A non-GGUF model (most obviously a halogen
   `.hgn` checkpoint) is refused outright, because xdna fit is a
   GGUF-metadata question and has nothing to say about another format;
   that refusal is a plain `ValueError`, not a new slot-write error code,
   because nothing routes a non-GGUF model at an `xdna` slot today (the
   `format_arch = "gguf"` runner declaration already steers the model
   picker).

5. **NPU coexistence with `flm` is warn-and-time-slice, not exclusion.**
   Both runtimes share one NPU; the spike measured near-zero perf impact
   from an idle `flm` slot holding a hardware context while `xdna` runs,
   so hal0 does not refuse a load because the other NPU lane is up. It
   surfaces the sharing the same way the GTT-leak and fabric-clock guards
   do elsewhere (ADR-0047 decision 9, ADR-0054): a warning plus the
   existing NPU occupancy signal, left to the sibling argv/coexistence
   work to wire into the load path. This ADR does not add a new refusal
   class; `npu_occupancy` and the argv/quadlet wiring are explicitly out of
   this worktree's scope (see the plan doc) and are tracked separately.

6. **pmode turbo is host-side and out of the container's control.**
   `xrt-smi configure --pmode turbo` runs on the host that owns the NPU,
   the same shape as halogen's `/host/sys` fabric-clock read and ADR-0054's
   host-side guard script: hal0's container can check or report a state it
   cannot set. Docs tell the operator to run it; hal0 does not attempt to
   set it from inside the container sandbox.

## Consequences

- hal0 now maintains a second llama.cpp fork and its own exact toolchain
  pin, on top of upstream llama.cpp for the default runners. A pin bump
  (ggml-xdna commit, or the mlir-aie/llvm-aie toolchain) is a runner-image
  rebuild plus a hardware gate run on real XDNA2 hardware, not a config
  change.
- Most models never get the fused decode path: it is one geometry
  (Qwen3.5 0.8B's GDN shape) and needs an explicit requant
  (`llama-quantize --tensor-type ssm_out=q5_K`). Every other GGUF on `xdna`
  runs correctly, just through the slower per-op path — `xdna_model_fit`
  never refuses a model, it only reports which path it gets.
- The correctness bug (`GLU_A=1` corrupting prefill) is carried as a
  baked-in image default rather than fixed upstream; if Tether ships a fix,
  the image's default must be revisited (and re-gated) rather than assumed
  safe to flip.
- Running `xdna` and `flm` together is allowed and warned about, not
  exclusive; a correctness or performance regression from true concurrent
  NPU use (as opposed to one side idle) is a risk this ADR accepts and
  leaves to the coexistence/occupancy work to quantify further.
- Host setup (`iommu=pt amd_iommu=on`, pmode turbo, fabric clock,
  memlock) is a one-time manual step per host, same shape as halogen's host
  requirements table — nothing here automates host kernel-parameter or
  firmware changes.

## Alternatives considered

- **Build kernels at container start, per host.** Rejected: ~10 minutes per
  cold start, and the toolchain pin is a correctness constraint (program
  memory overflow on a newer `llvm-aie`), not something safe to leave to
  whatever happens to be on the host's PATH.
- **Default `GGML_XDNA_PGEMM_GLU_A` to the fork's `=1` and document the
  workaround.** Rejected: a silent logits-corrupting default that an
  operator must know to override is exactly the defect ADR-0045's
  "never silently ignore" posture and ADR-0047's "hal0 owns launch facts"
  layering both exist to prevent.
- **Refuse any model that is not the fused Qwen3.5 geometry.** Rejected:
  per-op is a real, correct, supported mode (just slower); refusing it
  would make `xdna` a single-model runner for no correctness reason, and
  the GGUF-metadata check already classifies without needing to refuse.
- **Ship `xdna` as a vendor-pinned external image, like halogen.** Rejected:
  `ggml-xdna` is a source fork hal0 builds against a pinned open toolchain,
  not a closed vendor binary; hal0 already builds every other llama.cpp
  variant (ROCm, CUDA, Vulkan) from source, and the same pattern fits here.

## See also

- `docs/guides/run-xdna-npu.mdx` — host setup, model requant, env knobs.
- `docs/superpowers/plans/xdna-runner.md` — the working plan this ADR
  formalizes decisions from.
- ADR-0003 (NPU lane is FLM), ADR-0027 (runner tiers), ADR-0028 (runner
  system, pin table), ADR-0047 (halogen, the closest external/optional
  runner analogue), ADR-0054 (GPU fabric-clock guard, the host-side-knob
  precedent).
