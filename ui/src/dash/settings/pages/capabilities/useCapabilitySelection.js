// Shared per-(group, child) capability-selection editing over
// GET /api/capabilities + POST /api/capabilities/{group}/{child}.
// Every panel on the AI Capabilities page composes this instead of the
// ~40-line useState/useEffect/dirty loop VoicePage and ImageGenPage each
// hand-rolled (P3-ui consolidation).
import { useState, useEffect } from 'react'
import { useCapabilities, useCapabilityApply } from '@/api/hooks/useCapabilities'
import { resolveProvider } from './selection-pure.js'

export function useCapabilitySelection(group, child, { withProvider = false } = {}) {
  const capsQuery = useCapabilities()
  const applyCapability = useCapabilityApply()
  const caps = capsQuery.data
  const selection = caps?.selections?.[group]?.[child] || {}
  const catalogItems = caps?.catalogs?.[group]?.[child] || []

  const [model, setModel] = useState("")
  const [enabled, setEnabled] = useState(false)
  const [provider, setProvider] = useState("")

  useEffect(() => {
    if (selection.model != null) setModel(selection.model || "")
    if (selection.enabled != null) setEnabled(!!selection.enabled)
    if (withProvider && selection.provider != null) setProvider(selection.provider || "")
  }, [selection.model, selection.enabled, selection.provider, withProvider])

  const dirty = model !== (selection.model || "")
    || enabled !== !!selection.enabled
    || (withProvider && provider !== (selection.provider || ""))

  const reset = () => {
    setModel(selection.model || "")
    setEnabled(!!selection.enabled)
    if (withProvider) setProvider(selection.provider || "")
  }

  // Persist via capability apply. `extraBody` lets a panel piggyback fields
  // (none today); provider rides only when the panel opted in and set one.
  const save = async (extraBody = {}) => {
    const body = { model, enabled, ...extraBody }
    if (withProvider && provider) body.provider = provider
    await applyCapability.mutateAsync({ slot: group, child, body })
  }

  return {
    capsQuery, applyCapability, selection, catalogItems,
    model, setModel, enabled, setEnabled, provider, setProvider,
    dirty, reset, save,
    status: selection.status || "offline",
    resolvedProvider: resolveProvider(catalogItems, model, selection),
    loading: capsQuery.isLoading,
    // #1467: gate Save on isError, not just isLoading — a failed probe must
    // not allow saving against unknown live state.
    errored: capsQuery.isError,
  }
}
