// Which slot a model row's "Load now" should swap the model into.

export interface LoadSlot {
  name: string
  type?: string
  model_id?: string
  model?: { default?: string } | null
}

export type LoadTarget<S extends LoadSlot = LoadSlot> =
  | { kind: 'none' }
  | { kind: 'target'; slot: S }
  | { kind: 'choose'; options: S[] }

export function resolveLoadTarget<S extends LoadSlot>(
  slots: S[],
  model: { id: string; type?: string },
): LoadTarget<S> {
  const compatible = slots.filter(s => s.type === model.type)
  if (compatible.length === 0) return { kind: 'none' }
  const owning = compatible.find(s => (s.model_id || s.model?.default) === model.id)
  if (owning) return { kind: 'target', slot: owning }
  if (compatible.length === 1) return { kind: 'target', slot: compatible[0] }
  return { kind: 'choose', options: compatible }
}
