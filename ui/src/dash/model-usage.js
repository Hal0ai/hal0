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

/**
 * Build the footer summary text for staged model changes.
 * Maps field changes to display names, dedupes, and formats with slot restart info.
 * @param {Object} changes - Changes object from deriveModelChanges with boolean fields
 * @param {Array<{name: string}>} usingSlots - Slots using this model
 * @returns {string} Footer text: "no changes", or "N changes — {names} [⟳ restarts ...]"
 */
export function footSummary(changes, usingSlots) {
  if (!changes || !changes.any) {
    return 'no changes'
  }

  // Display name map per task brief
  const fieldMap = {
    name: 'name',
    provider: 'engine',
    mmproj: 'mmproj',
    hfRepo: 'source',
    hfFilename: 'source',
    extra: 'flags',
    profile: 'profile',
    ctx: 'context',
    chatTemplate: 'template',
    mtp: 'overrides',
    thinking: 'overrides',
    jinja: 'overrides',
    vision: 'overrides',
  }

  // Collect changed field display names, preserving order and deduping
  const displayNamesSet = new Set()
  const displayNames = []
  const fieldOrder = [
    'name',
    'provider',
    'mmproj',
    'hfRepo',
    'hfFilename',
    'extra',
    'profile',
    'ctx',
    'chatTemplate',
    'mtp',
    'thinking',
    'jinja',
    'vision',
  ]

  for (const field of fieldOrder) {
    if (changes[field] && field in fieldMap) {
      const displayName = fieldMap[field]
      if (!displayNamesSet.has(displayName)) {
        displayNamesSet.add(displayName)
        displayNames.push(displayName)
      }
    }
  }

  const count = displayNames.length
  let summary = `${count} changes — ${displayNames.join(' · ')}`

  // Add restart clause if any slots are using this model
  if (usingSlots && usingSlots.length > 0) {
    const slotNames = usingSlots.map((s) => s.name).join(' · ')
    summary += ` ⟳ restarts ${usingSlots.length} slots: ${slotNames}`
  }

  return summary
}
