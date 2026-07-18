// ─── typed settings client (R5 data seam · REWORK §K) ────────────────────────
//
// ONE hook the settings pages use to read + write hal0.toml config, its
// schema, and its reload classification — replacing the ad-hoc "import four
// hooks and re-derive the registry" pattern every split page grew
// (useSettings + useSettingsUpdate + useSettingsSchema + useApplyPlan, each
// unwrapping `applyPlanQuery.data?.registry || {}` by hand).
//
// It is a thin façade over the existing typed react-query hooks in
// `@/api/hooks/useSettings` (still the transport layer) — this module owns the
// *composition* (one call site, one merged registry, one save path, one
// reload-class lookup) so a page states intent ("save this patch", "what does
// this key require") instead of wiring the plumbing itself.
//
// The raw query/mutation objects are re-exported under stable names
// (`settings`, `update`, `reload`, `schema`, `applyPlan`) so a page keeps
// full access to react-query state (`.data`, `.isPending`, `.isError`,
// `.mutateAsync`) with no behavioural change — the consolidation is purely at
// the acquisition seam.
import { useMemo } from 'react'
import {
  useSettings,
  useSettingsUpdate,
  useSettingsReload,
  useSettingsSchema,
  useApplyPlan,
} from '@/api/hooks/useSettings'
import { mergeRegistry, reloadClassFor } from './reloadClass.js'
import { _schemaField, _getIn, _deepMergePatch } from '../shared/SchemaRow.jsx'

/**
 * The single settings client. Composes the read/write/schema/reload-class
 * surfaces into one object.
 *
 * @param {{ schema?: boolean }} [opts] - pass `{ schema: true }` on pages that
 *   render schema-driven rows (AdvRow); omitted pages skip the schema fetch.
 */
export function useSettingsClient(opts = {}) {
  const wantSchema = opts.schema === true
  const settings = useSettings()
  const update = useSettingsUpdate()
  const reload = useSettingsReload()
  // useSettingsSchema caches forever; calling it unconditionally is cheap, but
  // gating keeps a page that never renders schema rows from holding the query.
  const schema = useSettingsSchema()
  const applyPlan = useApplyPlan()

  const backendRegistry = applyPlan.data?.registry
  // The merged reload-class table: frontend fallback under the authoritative
  // backend rows. Memoised on the backend registry identity so it's stable
  // across renders (matters for ApplyBadge / effect deps downstream).
  const registry = useMemo(() => mergeRegistry(backendRegistry), [backendRegistry])

  const live = settings.data || null
  const schemaData = wantSchema ? schema.data || null : null

  return {
    // ── raw query/mutation handles (stable names, full react-query API) ──
    settings,
    update,
    reload,
    schema,
    applyPlan,

    // ── convenience façade ──────────────────────────────────────────────
    live,
    registry,
    isLoading: settings.isPending || (wantSchema && schema.isPending),
    isError: settings.isError || (wantSchema && schema.isError),
    error: settings.error || (wantSchema ? schema.error : null),

    /** Read a dotted value out of the live config (`get('slots.publish_host')`). */
    get: (dotKey) => _getIn(live, dotKey),

    /** Resolve a dotted key's field schema (types/bounds/description). */
    field: (dotKey) => _schemaField(schemaData, dotKey),

    /** Resolve a dotted key's reload class from the merged source. */
    reloadClass: (dotKey) => reloadClassFor(dotKey, registry),

    /** Deep-merge PUT a partial config patch (typed intent). */
    save: (patch) => update.mutateAsync(patch),

    /** Build a deep-merge patch object from a dotted key + value. */
    patchFor: (dotKey, value) =>
      dotKey.split('.').reverse().reduce((acc, part) => ({ [part]: acc }), value),

    _deepMergePatch,
  }
}
