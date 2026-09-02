/**
 * Filter slots by model ID, matching either default or live model.
 * @param {Array | undefined} slots - Slots array from useSlots()
 * @param {string} modelId - Model ID to match against
 * @returns {Array<{name: string}>} Filtered and deduped slots, preserving input order
 */
export function slotsUsingModel(slots, modelId) {
  if (!slots || !Array.isArray(slots)) {
    return []
  }

  const seen = new Set()
  const result = []

  for (const slot of slots) {
    if (slot.name && !seen.has(slot.name)) {
      if (slot.model_default === modelId || slot.model === modelId) {
        seen.add(slot.name)
        result.push({ name: slot.name })
      }
    }
  }

  return result
}
