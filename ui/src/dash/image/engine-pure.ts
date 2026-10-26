// The Engine tab's facts and fixed words. Pure, so they are tested without rendering
// (__tests__/engine-pure.test.ts).
//
// The tab draws /status as given: the engine state, the gate, the queue counts, the device
// readings. Memory is not on it. It is the top bar ruler's and the fit meters', and the old
// pane's GTT and RAM gauges are gone.

import type {
  ComfyuiCatalogModel,
  ComfyuiGate,
  ComfyuiStatus,
  ComfyuiWorkflow,
} from '@/api/hooks/useComfyui'

// ── fixed copy ───────────────────────────────────────────────────────────────

export const STOPPED_HEAD = 'ComfyUI is stopped'
export const STOPPED_BODY =
  'Generate still works: the next render starts it (a cold start can take up to 2 min).'
export const UNREACHABLE_HEAD = "ComfyUI isn't answering"
export const QUEUE_EMPTY =
  'Nothing queued. Renders from Generate, Open WebUI, the API and the editor all show here.'
export const EDITOR_NOTE =
  "Prompts from the editor switch the GPU the same way. The GPU gate refuses one that hal0 can't hand the GPU to."
// ComfyUI does not open a workflow from a link (the old pane's `?workflow=` was ignored): the
// chips open the editor, and the workflow is picked there.
export const WORKFLOW_HINT =
  "ComfyUI can't open a workflow from a link. Open the editor, then pick the workflow in its Workflows sidebar."

// ── the editor card ──────────────────────────────────────────────────────────

export interface WorkflowChip {
  id: string
  label: string
  /** A workflow file no catalog model owns: the operator's own. */
  custom: boolean
}

/**
 * What the editor card lists: every installed model (its workflow is provisioned as
 * `<model id>.json`), then the files in the workflows folders that no catalog model owns. A
 * model that is not installed has nothing to list, even when a partial install already left its
 * file on disk. With the catalog unknown every file would look custom, so nothing is listed.
 */
export function workflowChips(
  models: readonly ComfyuiCatalogModel[] | undefined,
  workflows: readonly ComfyuiWorkflow[] | undefined,
): WorkflowChip[] {
  if (!models) return []
  const owned = new Set(models.map((m) => m.id))
  const installed: WorkflowChip[] = models
    .filter((m) => m.state === 'complete')
    .map((m) => ({ id: m.id, label: m.label, custom: false }))
  const custom: WorkflowChip[] = (workflows ?? [])
    .filter((w) => w.name && !owned.has(w.name))
    .map((w) => ({ id: w.name, label: w.name, custom: true }))
  return [...installed, ...custom]
}

// ── the engine card ──────────────────────────────────────────────────────────

/**
 * The notice the engine card adds to its state, if any. `starting` with `reachable == false`
 * is the container being up while ComfyUI does not answer on its port: the backend reports it
 * that way both while the engine boots and when it is wedged.
 */
export function engineNotice(status: ComfyuiStatus | null | undefined): 'stopped' | 'unreachable' | null {
  if (!status) return null
  if (status.engine === 'stopped') return 'stopped'
  if (status.engine === 'starting' && status.reachable === false) return 'unreachable'
  return null
}

export interface GateRow {
  label: string
  tone: 'ok' | 'warn' | 'err' | 'muted'
  hint: string | null
}

// The GPU gate in the img container, in the four states /status reports (ComfyuiGate).
const GATE_ROWS: Record<ComfyuiGate['state'], GateRow> = {
  current: { label: 'current', tone: 'ok', hint: null },
  stale: {
    label: 'stale',
    tone: 'warn',
    hint: 'An older gate is running. Restart the engine to load the current one.',
  },
  unknown: { label: 'unknown', tone: 'muted', hint: 'Not heard from since hal0-api started.' },
  no_credential: {
    label: 'no credential',
    tone: 'err',
    hint: 'hal0 requires sign-in and the gate has none for it yet, so image generation is blocked.',
  },
}

export function gateRow(gate: ComfyuiGate | null | undefined): GateRow | null {
  return gate ? (GATE_ROWS[gate.state] ?? null) : null
}

// ── the queue and device cards ───────────────────────────────────────────────

// A count the body left out reads as none: the line never says "undefined".
export function queueSummary(queue: { running: number; pending: number }): { line: string; empty: boolean } {
  const running = queue.running ?? 0
  const pending = queue.pending ?? 0
  return { line: `${running} running · ${pending} waiting`, empty: running + pending === 0 }
}

/** ComfyUI is running a prompt (hal0's or the editor's). */
export function isRendering(s: Pick<ComfyuiStatus, 'queue'>): boolean {
  return (s.queue?.running ?? 0) > 0
}

/**
 * The device card's readings. Utilisation is read only while a render runs: the backend reports
 * it that way (the busy counter reads high at idle on the target GPU), so out of a render it is
 * not a reading. A figure the box could not take stays null.
 */
export function deviceCells(
  s: Pick<ComfyuiStatus, 'temp' | 'clock' | 'util' | 'queue'>,
): { temp: number | null; clock: number | null; util: number | null } {
  return { temp: s.temp ?? null, clock: s.clock ?? null, util: isRendering(s) ? (s.util ?? null) : null }
}
