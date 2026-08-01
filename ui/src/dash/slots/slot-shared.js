// hal0 dashboard — shared helpers for the decomposed slot surfaces (D2).
//
// The post-R3 rework splits slot-modals.jsx's create/rename/delete surfaces into
// focused modules under dash/slots/. These pure helpers are the seam they share:
// a device-flavour derivation (device now RIDES the model, so the slot reads it
// from the model rather than owning a picker) and a local-model list for the
// create picker (which picks the model first, so it can't pre-filter by type).

import { normalizeApiModel, isUpstreamModel } from '@/lib/normalizeApiModel'

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
export function slotModelRow(slot, models) {
  const id = slotModelId(slot);
  if (!id) return null;
  return (models ?? []).find((m) => m?.id === id) || null;
}

// The short device label + hue token for a device string (chip / teach copy).
export function deviceHue(deviceToken) {
  const t = String(deviceToken || "").toLowerCase();
  if (t.includes("rocm")) return { label: "rocm", cssVar: "--dev-rocm" };
  if (t.includes("vulkan")) return { label: "vulkan", cssVar: "--dev-vulkan" };
  if (t.includes("npu") || t.includes("flm")) return { label: "npu", cssVar: "--dev-npu" };
  return { label: "cpu", cssVar: "--dev-cpu" };
}
