# ADR-0038: Image mode lasts only while an image renders

## Status

**ACCEPTED (2026-10-25).** An operator decision. It replaces the idle window
(`[image].idle_restore_minutes`) that held image mode after the last render.

## Context

The reference hardware has one shared iGPU. The LLM slots and ComfyUI cannot
both hold their weights in its memory, so the GPU arbiter keeps the GPU in one
of two modes: LLM mode, or image mode with the LLM slots unloaded
(`docs/concepts/slots.mdx`, "The GPU arbiter").

Until now image mode ended on a timer. After the last image activity the
arbiter waited `[image].idle_restore_minutes` (60 by default) and then
restored the LLM slots. Throughout that hour a request for a GPU LLM slot was
refused with `503 gpu.image_mode`.

That was a fair price while a person at the ComfyUI editor, or the omni tool
loop, was the only way into image mode. This release makes Open WebUI's image
button render through `/v1/images/generations`, so every image made from a
chat window switched the GPU to image mode, and the chat message that follows
the image in the same turn was refused. The final review of that work flagged
it, and the operator decided that image mode should end when the render does.

## Decision

1. **Image work is what keeps image mode.** It is one of:
   - a `/v1/images/generations` request, counted from before the switch until
     its render returns;
   - a request forwarded to the img slot;
   - a render in ComfyUI's queue, read strictly from `/queue`, because the
     editor's prompts never pass through hal0.
2. **The LLM set reloads 10 seconds after the last image work.** A loop that
   ticks every 2 seconds decides, so the GPU comes back within 12 seconds.
   Back-to-back renders share one image mode.
3. **An LLM request ends the grace and waits, up to 120 seconds.** A request
   for a GPU LLM slot that arrives while image mode is on starts the restore
   at once, or waits for the render that is running first, and is served after
   the reload. All such requests share one restore. The request gets
   `503 gpu.image_mode` (`Retry-After: 15`) only when image mode is pinned,
   when ComfyUI's queue cannot be read, or when the 120 seconds run out.
4. **A render is never restored under.** The restore decision runs under the
   switch lock and checks the job count again after each await. A request that
   waited for a restore is served before a render queued behind it switches
   the GPU again.
5. **A pin holds.** It holds against the loop and against LLM demand until the
   operator unpins it or forces a switch to inference. It is taken from the
   call that enters image mode: a pin set in LLM mode is not carried into the
   next switch.
6. **Every GPU LLM slot unloads for a render.** There is no memory-budgeted
   mode in which some LLM slots stay loaded.
7. **An editor prompt gets time to reach the queue, and is refused when hal0
   cannot hand it the GPU.** After any switch into image mode the automatic
   restores leave the GPU alone for 3 seconds, so a prompt the gate node asked
   image mode for can reach ComfyUI's queue, where the restore decision sees
   it. The `hal0_gpu_gate` node waits while hal0 is switching, asks again if an
   LLM request took the GPU back, and refuses the prompt, queuing nothing,
   when hal0 rejects the switch, stops answering mid-switch, or stays
   unreachable for 5 seconds. `HAL0_COMFYUI_GATE_FAIL_OPEN=1` lets prompts
   through while hal0 is unreachable, and only then.
8. **`[image].idle_restore_minutes` is removed.** A constant of 10 seconds needs
   no setting. `ImageGenConfig` refuses the key, and a pass in the
   post-activation chain removes it from existing slot files. `hal0 update`
   and every `hal0-api` start run that pass. It is a slot-file edit beside the
   Vulkan relabel, not a database migration: it deletes the line in place and
   keeps the file's comments, and re-serialises the file only for an inline or
   dotted `image` key.

## Consequences

- Each render, or each burst of renders less than 10 seconds apart, costs one
  unload and one reload of the LLM set.
- The first chat after a render waits for the whole saved set to reload. The
  mode flips back to LLM only after the last slot is ready, because crash
  recovery relies on that order.
- A person iterating in the editor more slowly than every 10 seconds pays a
  reload each way, unless they pin image mode.
- The gate node waits for a switch that can take as long as the drain (up to
  120 seconds), so its own limit is 180 seconds. A box picks up a new gate
  node when its img container restarts.
- A ComfyUI queue that never drains keeps image mode, because the restore
  decision treats a busy queue as image work. A forced switch to inference
  ends it.
- A box rolled back to an older release reads the missing key as its old
  default of 60. Nothing breaks either way.
- Settings loses the "Idle restore" row, and `GET /api/comfyui/status` drops
  `arbiter.idle_restore_at` and gains `arbiter.switching` and
  `arbiter.image_jobs`.

## Alternatives considered

- **Keep a configurable idle window.** Rejected. The window existed to bound
  how long chat stays refused, and a chat request now ends image mode itself.
  What remains is a constant that exists to let back-to-back renders share a
  switch.
- **Refuse chat while a render runs and only restore afterwards.** Rejected.
  It is the behavior this record replaces: Open WebUI's same-turn reply fails.
- **Make the `/v1` request await the restore before responding.** Rejected.
  Back-to-back clients would thrash the GPU between modes, and the omni tool
  call times out after 120 seconds.
- **Keep one LLM slot loaded under a memory budget.** Rejected by the operator,
  who chose to unload and reload every GPU LLM slot around generation.

## References

- `src/hal0/slots/arbiter.py` (`image_job`, `make_room_for_llm`,
  `_restore_decision`, `run_restore_loop`).
- `src/hal0/api/routes/v1.py` (`images_generations`),
  `src/hal0/dispatcher/router.py` (`Dispatcher.forward`).
- `src/hal0/comfyui/assets/hal0_gpu_gate.py` (`prepare_image_mode`),
  `src/hal0/providers/comfyui.py` (the opt-out passthrough).
- `src/hal0/updater/updater.py` (`strip_retired_image_keys`),
  `src/hal0/config/schema.py` (`ImageGenConfig`).
- ADR-0037 (image models are a ComfyUI-owned catalog).
