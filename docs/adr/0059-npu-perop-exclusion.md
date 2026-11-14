# ADR-0059: A per-op xdna slot and the FLM anchor exclude each other on the NPU, with confirm-and-unload

## Status

**ACCEPTED (2026-11-14).** An operator decision, tracked as #3666. Amends
ADR-0057 decision 5 (NPU coexistence with `flm`) for per-op models. ADR-0003
(FLM is the default NPU lane) and ADR-0054 (the guard shape this follows)
stand.

## Context

ADR-0057 decided that an `xdna` slot and the FLM anchor share the one NPU and
that hal0 warns instead of refusing, because the spike measured near-zero
impact from an idle FLM slot holding a hardware context while `xdna` served a
request.

Testing on real XDNA2 hardware (Strix Halo, `amdxdna` driver, FLM 1.0.12, xdna
runner image `72f6dfa-rf1a3931`) showed that this holds only for a fused model:

- A **per-op** xdna slot and FLM cannot both hold the NPU. Whichever starts
  second fails with `DRM_IOCTL_AMDXDNA_CREATE_HWCTX IOCTL failed (err=-22)`
  (EINVAL), in either start order.
- A **fused** xdna slot (Qwen3.5-0.8B with `ssm_out` requantized to Q5_K)
  does coexist with FLM. The two time-slice the NPU and hal0 reports the
  shared warning.

A warning therefore explains the crash after the fact but does not prevent it:
the second slot goes to ERROR and the operator has to work out why.

## Decision

1. **A per-op xdna slot and the FLM anchor are mutually exclusive.** The rule
   (`src/hal0/slots/npu_conflict.py`) conflicts only for the pair
   {FLM anchor, per-op xdna slot}. Fused xdna plus FLM stays allowed with
   today's shared warning. The FLM anchor's `-stt` and `-embed` shadows are
   part of the anchor, not separate holders. Whether an xdna slot is fused or
   per-op follows its launch environment (a `GGML_XDNA_FUSED_LAYER` set in
   `[xdna].env` or `[server].env` wins over the model fit), and a model the
   registry cannot resolve counts as per-op, so a doubt fails safe.

2. **Refuse the conflicting load unless the caller opts in.** A load that
   would put the pair on the NPU is refused with `409 slot.npu_conflict`. The
   details carry `slot`, `holders`, `holder_engine` (`"flm"` or `"xdna"`),
   `unloads` (the holders plus an FLM anchor's shadows), `pinned`, `pending`
   and `reason`. A refused slot goes back to OFFLINE, not ERROR, and no load
   failure is recorded, the same handling as the GPU fabric-clock refusal in
   ADR-0054.

3. **The opt-in is `unload_npu_holder`.** It is a JSON body field on
   `POST /api/slots/{name}/load` and `POST /api/slots/{name}/restart`, the
   `--unload-npu-holder` flag on `hal0 slot load` and `hal0 slot restart`, and
   the `unload_npu_holder` argument of the MCP `slot_load` and `slot_restart`
   tools. With it, the load unloads `unloads` first and then proceeds. The
   dashboard asks for confirmation on a 409 and retries with the flag.

4. **A pin does not protect a holder from the opt-in.** A pin guards
   automatic eviction only. The opt-in is the same operator intent as
   `unload?force=true`, so a pinned holder is unloaded; `details.pinned`
   tells the dialog to say so. The FLM anchor named `npu` is pinned by
   default, so without this ruling the opt-in would never work for the common
   case.

5. **A holder whose load is still in flight is never unloaded.** A holder
   that is pulling, starting, warming, or reserved by a load that has not
   published a state yet makes the conflict `pending: true`, and the opt-in
   cannot override it. Two opposite loads clicked together therefore let
   exactly one proceed and the other gets the 409; neither deadlocks and both
   are never started.

6. **Traffic never unloads.** The dispatcher, `/v1` and arbiter loads never
   pass the flag, so a request that triggers a lazy load gets the 409 instead
   of evicting the other lane.

7. **Restart and swap check before tearing anything down.** `SlotManager.restart`
   and `SlotManager.swap` (which takes `unload_npu_holder` and checks the new
   model's NPU role before it persists the new default) refuse first, so a
   refused restart or swap leaves the slot exactly as it was.

8. **Autoload is refused at config-write time.** systemd starts autoload units
   at boot without passing the load guard, so a config write that would make a
   per-op xdna slot and the FLM anchor both autoload is refused with the same
   `slot.npu_conflict` code and `reason: "autoload"`
   (`check_npu_autoload_conflict` in `src/hal0/slots/config_write.py`).

## Alternatives considered

- **Warn only (ADR-0057's original stance).** Rejected: it explains the
  crash but does not prevent it, and the evidence above shows the crash is
  deterministic for per-op models.
- **Refuse without an opt-in.** Rejected: it is safe but forces the operator
  to stop the holder by hand before every switch between the lanes, which is
  the exact click the dashboard can offer to do after one confirmation.

## Consequences

- Switching the NPU between FLM and a per-op model is one confirmed click in
  the NPU tab (or one flag on the CLI), and nothing starts and fails any more.
- A pinned FLM anchor can now be unloaded by an explicit opt-in. The
  confirmation dialog names it as pinned.
- `docs/guides/run-xdna-npu.mdx` describes the operator-facing behaviour, and
  the occupancy shared warning now applies to fused coexistence only.

## Open points

- FLM serving chat only (no speech-to-text or embeddings) beside a per-op slot
  is untested. The rule assumes it conflicts. If it coexists, narrow the rule.
- Two xdna slots on the NPU at once are untested, so the rule does not
  restrict them.

## See also

- ADR-0057 (the optional xdna runner; decision 5 is amended here for per-op
  models).
- ADR-0054 (the GPU fabric-clock guard whose refuse-and-leave-OFFLINE handling
  this reuses).
- `docs/guides/run-xdna-npu.mdx`: the operator guide.
