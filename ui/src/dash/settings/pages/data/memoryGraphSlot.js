export function normalizeMemoryGraphSlot(value, availableSlots = []) {
  const raw = String(value || "").trim();
  if (!raw) return "";

  const prefix = "hal0/";
  if (!raw.startsWith(prefix)) return raw;

  const slotName = raw.slice(prefix.length);
  return availableSlots.includes(slotName) ? slotName : raw;
}
