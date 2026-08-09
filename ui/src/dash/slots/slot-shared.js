// hal0 dashboard — shared helpers for the decomposed slot surfaces (D2).
//
// The post-R3 rework splits slot-modals.jsx's create/rename/delete surfaces into
// focused modules under dash/slots/. These pure helpers are the seam they share:
// a device-flavour derivation (device now RIDES the model, so the slot reads it
// from the model rather than owning a picker) and a local-model list for the
// create picker (which picks the model first, so it can't pre-filter by type).

import { normalizeApiModel, isUpstreamModel } from '@/lib/normalizeApiModel'
import { devKind } from '@/lib/deviceMeta'
import { isNpuShadowSlot } from '../npu-modality.js'

// Local (non-upstream) models a slot can bind — a slot binds a local file path,
// so upstream-advertised rows (no local path) are excluded. Unlike
// compatibleModels(), this does NOT filter by slot type: the create flow picks
// the model first and derives the type/device from it.
export function localModels(models) {
  return (models ?? []).map(normalizeApiModel).filter((m) => !isUpstreamModel(m));
}

// Device token derived from a model's backends/device — the model's stamped tune
// is device-flavoured, so the slot's device is READ from the model, never chosen
// on the slot. Returns one of: gpu-rocm | gpu-vulkan | npu | cpu.
export function deviceFromModel(model) {
  const backends = Array.isArray(model?.backends) ? model.backends : [];
  const dev = String(model?.device || "").toLowerCase();
  const has = (b) => backends.includes(b) || dev.includes(b);
  if (has("rocm")) return "gpu-rocm";
  if (has("vulkan")) return "gpu-vulkan";
  if (has("flm") || dev.includes("npu")) return "npu";
  return "cpu";
}

// ─── slot → bound model row ────────────────────────────────────────────────
// The id a slot is bound to. `model_id` is the live-reconciled id from
// /api/slots; `model` is the display/label fallback carried by the bare
// /api/status union entry (and by HAL0_DATA seeds). Empty string = unbound.
export function slotModelId(slot) {
  return slot?.model_id || slot?.model || "";
}

// The FULL normalized model row a slot is bound to, resolved out of a
// useModels() list (which already maps through normalizeApiModel). Returns
// null when the slot is unbound or the row isn't in the list yet — callers
// must treat null as "no model editor available", because ModelDrawer needs
// the row, not an id, and renders nothing for a null model.
//
// One copy on purpose: the slot edit drawer (slot-modals.jsx) and the slot
// card (slots.jsx / inference-pane.jsx) both need this exact lookup, and the
// `model_id || model` precedence is easy to get subtly wrong twice.
//
// Falls back to `m.aliases` (#1656): pre-#1629 slots can still be bound to
// the legacy generated `<tag>-FLM` id, which the catalog's dedupe skip no
// longer emits as its own row — the surviving registry row carries that id
// in `aliases` instead so the lookup doesn't go dangling.
export function slotModelRow(slot, models) {
  const id = slotModelId(slot);
  if (!id) return null;
  const rows = models ?? [];
  const exact = rows.find((m) => m?.id === id);
  if (exact) return exact;
  return rows.find((m) => Array.isArray(m?.aliases) && m.aliases.includes(id)) || null;
}

// The NPU trio's anchor slot, resolved STRUCTURALLY — the non-shadow NPU
// llm slot — never by stripping a `-stt`/`-embed` suffix off a display
// name. #1637 hardened the occupancy card's pills this way ("on a renamed
// or legacy-named anchor every pill click silently no-op'd"); #1662 gives
// the shadow drawer's "open anchor" link the same structural resolution so
// a renamed anchor ('flm' → 'npu') doesn't leave old shadows pointing at a
// name-derived link to a slot that no longer exists (reconcile_trio_slots
// names NEW shadows after the anchor but never renames pre-existing ones).
// Returns null when no anchor is present in the list (e.g. still loading).
export function npuAnchorSlot(slots) {
  const npuSlots = (slots ?? []).filter(
    (s) => s?.device_class === 'npu' || devKind(s?.device) === 'npu',
  );
  return (
    npuSlots.find((s) => !isNpuShadowSlot(s) && s.type === 'llm') ||
    npuSlots.find((s) => !isNpuShadowSlot(s)) ||
    null
  );
}

// The short device label + hue token for a device string (chip / teach copy).
export function deviceHue(deviceToken) {
  const t = String(deviceToken || "").toLowerCase();
  if (t.includes("rocm")) return { label: "rocm", cssVar: "--dev-rocm" };
  if (t.includes("vulkan")) return { label: "vulkan", cssVar: "--dev-vulkan" };
  if (t.includes("npu") || t.includes("flm")) return { label: "npu", cssVar: "--dev-npu" };
  return { label: "cpu", cssVar: "--dev-cpu" };
}
