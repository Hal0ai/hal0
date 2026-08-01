// hal0 v3 dashboard — Operator Board hooks (feat/operator-board).
//
// Covers the full `/api/board/*` surface: queries, mutations, WS event
// stream, and SSE chat. Mirrors the useAgents.ts / useLogs.ts patterns:
// TanStack Query for REST, manual state for streaming transports.
//
// Contract: FROZEN in ui/CONTRACTS.md §"Operator Board (#board)" +
// SPEC §3 §4. Do not diverge.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, apiGet, apiPost, apiPatch, apiPut, apiDelete, readErrorEnvelope } from '../client'
import { ENDPOINTS } from '../endpoints'
import { normaliseAssignee, normaliseProfile } from './boardActors.js'

// ── Query key helper (exported so bridge + specs can use it) ──────────

export function boardKey(board?: string | null) {
  return board ? ['board', 'view', board] : ['board', 'view']
}

// ── Wire shapes (snake_case from the API) ────────────────────────────

export type TaskStatus =
  | 'triage'
  | 'todo'
  | 'scheduled'
  | 'ready'
  | 'running'
  | 'blocked'
  | 'review'
  | 'done'
  | 'archived'

export const VISIBLE_LANES: TaskStatus[] = [
  'triage',
  'todo',
  'scheduled',
  'ready',
  'running',
  'blocked',
  'review',
  'done',
]

export interface TaskComment {
  author: string
  at: string
  body: string
}

export interface TaskEvent {
  kind: string
  at: string
  json?: string
}

export interface TaskRun {
  state: string
  profile: string
  dur: string
  at: string
  msg: string
}

export interface TaskDeps {
  parents: string[]
  children: string[]
}

/** Normalised task shape (camelCase) consumed by the board UI. */
export interface BoardTask {
  id: string
  title: string
  status: TaskStatus
  assignee: string | null
  tenant?: string
  priority?: number
  workspace?: string
  createdBy: string | null
  created?: string
  body: string | null
  blockReason: string | null
  schedule?: string
  summary?: string
  deps: TaskDeps
  comments: TaskComment[]
  events: TaskEvent[]
  runs: TaskRun[]
  commentCount: number
  depCount: string | null
}

/** Normalised board view with tasks bucketed into lanes. */
export interface BoardView {
  tasks: BoardTask[]
  lanes: Record<TaskStatus, BoardTask[]>
}

export interface BoardRecord {
  slug: string
  name: string
  icon?: string
  count?: number
  desc?: string
}

export interface BoardProfile {
  id: string
  label?: string
  count?: number
  /** Any extra fields from the server */
  [k: string]: unknown
}

export interface BoardAssignee {
  id: string
  label?: string
  [k: string]: unknown
}

export interface BoardStats {
  [k: string]: unknown
}

export interface BoardConfig {
  tick_interval?: number
  failure_limit?: number
  claim_ttl?: number
  max_in_flight?: number
  [k: string]: unknown
}

export interface BoardOrchestration {
  orchestrator_profile?: string
  default_assignee?: string
  auto_decompose?: boolean
  auto_promote_children?: boolean
  [k: string]: unknown
}

export interface WorkerActive {
  id: string
  [k: string]: unknown
}

export interface BoardRun {
  id: string
  state?: string
  [k: string]: unknown
}

export interface TaskLogEntry {
  ts?: string
  msg?: string
  [k: string]: unknown
}

// ── Body types ────────────────────────────────────────────────────────

export interface CreateTaskBody {
  title: string
  status?: TaskStatus
  assignee?: string | null
  tenant?: string
  priority?: number
  body?: string
  [k: string]: unknown
}

export interface UpdateTaskBody {
  status?: TaskStatus
  assignee?: string | null
  priority?: number
  title?: string
  body?: string
  result?: string
  block_reason?: string | null
  summary?: string
  metadata?: Record<string, unknown>
  [k: string]: unknown
}

export interface LinkBody {
  parent_id: string
  child_id: string
}

export interface BulkTasksBody {
  ids: string[]
  update: Partial<UpdateTaskBody>
  [k: string]: unknown
}

export interface CreateBoardBody {
  slug: string
  name: string
  desc?: string
  icon?: string
}

// ── Wire-shape helpers ────────────────────────────────────────────────
//
// The Hermes kanban plugin speaks snake_case and returns TWO shapes:
//   • GET /board embeds a FLAT task row in each column.
//   • GET /tasks/{id} WRAPS the row in an envelope:
//        {task, comments, events, attachments, links, runs}
// Timestamps are unix-epoch SECONDS; event/run/comment items use their own
// field names (created_at, payload, outcome, summary…) that differ from what
// the drawer renders (at, json, state, msg). These helpers bridge both wire
// shapes — plus the already-normalised camelCase objects the optimistic-update
// path feeds back through normaliseTask — into the UI's camelCase contract.

const _isObj = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v)

/** Unix-epoch (sec or ms) or pre-formatted string → short "20m ago" label. */
function relTime(v: unknown): string | undefined {
  if (v == null || v === '') return undefined
  if (typeof v === 'string') return v // already a display string / ISO
  if (typeof v !== 'number' || !Number.isFinite(v)) return undefined
  const ms = v < 1e12 ? v * 1000 : v // < 1e12 ⇒ epoch seconds
  const delta = Date.now() - ms
  if (delta < 0) return 'just now'
  const s = Math.floor(delta / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

/** started/ended epoch seconds → "1m 57s" duration (open runs measure to now). */
function durLabel(start: unknown, end: unknown): string | undefined {
  if (typeof start !== 'number') return undefined
  const endS = typeof end === 'number' ? end : Math.floor(Date.now() / 1000)
  let s = Math.max(0, Math.floor(endS - start))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  s = s % 60
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

function normaliseComment(c: Record<string, unknown>): TaskComment {
  return {
    author: String(c.author ?? c.created_by ?? ''),
    at: (c.at as string) ?? relTime(c.created_at) ?? '',
    body: String(c.body ?? ''),
  }
}

function normaliseEvent(e: Record<string, unknown>): TaskEvent {
  const payload = e.payload
  return {
    kind: String(e.kind ?? ''),
    at: (e.at as string) ?? relTime(e.created_at) ?? '',
    json:
      (e.json as string) ??
      (payload != null ? JSON.stringify(payload) : undefined),
  }
}

function normaliseRun(r: Record<string, unknown>): TaskRun {
  // Upstream carries both status ("running"/"done") and outcome ("completed").
  // The drawer keys its row colour off state ∈ {active, completed, review}.
  const rawState = String(r.state ?? r.outcome ?? r.status ?? '')
  return {
    state: rawState === 'running' ? 'active' : rawState,
    profile: String(r.profile ?? r.assignee ?? ''),
    dur: (r.dur as string) ?? durLabel(r.started_at, r.ended_at) ?? '',
    at: (r.at as string) ?? relTime(r.ended_at ?? r.started_at) ?? '',
    msg: String(r.msg ?? r.summary ?? ''),
  }
}

// ── Wire-to-normalised task transform ─────────────────────────────────

function normaliseTask(raw: Record<string, unknown>): BoardTask {
  // Unwrap the /tasks/{id} envelope: read scalars off the inner row, but pull
  // collections (comments/events/runs) and links off the envelope top level.
  // GET /board rows and optimistic-update objects are flat, so fall back to the
  // row itself for every field.
  const env = raw
  const t = _isObj(raw.task) ? (raw.task as Record<string, unknown>) : raw

  const assignee =
    (t.assignee ?? t.profile ?? null) as string | null
  const createdBy =
    (t.created_by ?? t.createdBy ?? null) as string | null
  const blockReason =
    (t.block_reason ?? t.blockReason ?? null) as string | null
  const body = (t.body ?? t.desc ?? null) as string | null

  const pickArr = (k: string): Record<string, unknown>[] =>
    Array.isArray(env[k])
      ? (env[k] as Record<string, unknown>[])
      : Array.isArray(t[k])
        ? (t[k] as Record<string, unknown>[])
        : []
  const rawComments = pickArr('comments')
  const rawEvents = pickArr('events')
  const rawRuns = pickArr('runs')

  const commentCount =
    typeof (t.comment_count ?? t.commentCount) === 'number'
      ? ((t.comment_count ?? t.commentCount) as number)
      : rawComments.length
  const depCount =
    (t.dep_count ?? t.depCount ?? null) as string | null

  // deps: normalised {parents,children}, or the upstream `links` (same shape).
  const rawDeps = (env.deps ?? env.links ?? t.deps ?? t.links ?? {}) as {
    parents?: string[]
    children?: string[]
  }
  const deps: TaskDeps = {
    parents: Array.isArray(rawDeps.parents) ? rawDeps.parents : [],
    children: Array.isArray(rawDeps.children) ? rawDeps.children : [],
  }

  return {
    id: String(t.id ?? ''),
    title: String(t.title ?? ''),
    status: (t.status ?? 'triage') as TaskStatus,
    assignee,
    tenant: (t.tenant ?? undefined) as string | undefined,
    priority: t.priority as number | undefined,
    workspace: (t.workspace ?? t.workspace_path) as string | undefined,
    createdBy,
    created: (t.created as string | undefined) ?? relTime(t.created_at),
    body,
    blockReason,
    schedule: t.schedule as string | undefined,
    summary: (t.summary ?? t.latest_summary) as string | undefined,
    deps,
    comments: rawComments.map(normaliseComment),
    events: rawEvents.map(normaliseEvent),
    runs: rawRuns.map(normaliseRun),
    commentCount,
    depCount,
  }
}

// ── Board response normaliser ─────────────────────────────────────────
//
// Handles four wire shapes the server may return:
//   1. {lanes: {status: [task, ...]}}
//   2. {tasks: [...]}
//   3. [task, ...]  (bare array)
//   4. {columns: [{name, tasks: [...]}]}  (what Hermes kanban GET /board emits)

function normaliseBoardResponse(
  raw: unknown,
  includeArchived = false,
): BoardView {
  let flatTasks: BoardTask[] = []

  if (Array.isArray(raw)) {
    flatTasks = (raw as Record<string, unknown>[]).map(normaliseTask)
  } else if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>
    if (obj.lanes && typeof obj.lanes === 'object') {
      const lanes = obj.lanes as Record<string, unknown[]>
      for (const [_status, tasks] of Object.entries(lanes)) {
        if (Array.isArray(tasks)) {
          flatTasks.push(
            ...(tasks as Record<string, unknown>[]).map(normaliseTask),
          )
        }
      }
    } else if (Array.isArray(obj.tasks)) {
      flatTasks = (obj.tasks as Record<string, unknown>[]).map(normaliseTask)
    } else if (Array.isArray(obj.columns)) {
      // Hermes kanban GET /board returns {columns: [{name, tasks: [...]}]}.
      // Flatten every column's tasks; lane bucketing below re-groups by status.
      for (const col of obj.columns as Record<string, unknown>[]) {
        const colTasks = (col as { tasks?: unknown }).tasks
        if (Array.isArray(colTasks)) {
          flatTasks.push(
            ...(colTasks as Record<string, unknown>[]).map(normaliseTask),
          )
        }
      }
    }
  }

  // Filter archived unless requested
  const visible = includeArchived
    ? flatTasks
    : flatTasks.filter((t) => t.status !== 'archived')

  // Bucket into lanes (8 visible + optional archived)
  const lanes: Record<string, BoardTask[]> = {}
  const lanesToBuild: TaskStatus[] = includeArchived
    ? [...VISIBLE_LANES, 'archived']
    : VISIBLE_LANES
  for (const lane of lanesToBuild) {
    lanes[lane] = []
  }
  for (const task of visible) {
    if (lanes[task.status]) {
      lanes[task.status].push(task)
    } else if (task.status === 'archived' && includeArchived) {
      lanes['archived'].push(task)
    } else if (task.status !== 'archived') {
      // Unknown status (upstream enum drift): surface the card in triage
      // rather than letting it vanish while still counting toward "N tasks".
      lanes['triage'].push(task)
    }
  }

  return { tasks: flatTasks, lanes: lanes as Record<TaskStatus, BoardTask[]> }
}

// ── Queries ──────────────────────────────────────────────────────────

export interface UseBoardViewOptions {
  board?: string
  tenant?: string
  includeArchived?: boolean
  workflowTemplateId?: string
}

export function useBoardView(
  opts: UseBoardViewOptions = {},
): UseQueryResult<BoardView> {
  const {
    board,
    tenant,
    includeArchived = false,
    workflowTemplateId,
  } = opts
  return useQuery<BoardView>({
    queryKey: [...boardKey(board), { tenant, includeArchived, workflowTemplateId }],
    queryFn: async () => {
      const params = new URLSearchParams()
      if (board) params.set('board', board)
      if (tenant) params.set('tenant', tenant)
      if (includeArchived) params.set('include_archived', 'true')
      if (workflowTemplateId)
        params.set('workflow_template_id', workflowTemplateId)
      const qs = params.toString() ? `?${params}` : ''
      const raw = await apiGet<unknown>(`${ENDPOINTS.board}${qs}`)
      return normaliseBoardResponse(raw, includeArchived)
    },
    refetchOnWindowFocus: true,
  })
}

export function useBoardTask(id: string): UseQueryResult<BoardTask> {
  return useQuery<BoardTask>({
    queryKey: ['board', 'task', id],
    queryFn: async () => {
      const raw = await apiGet<Record<string, unknown>>(ENDPOINTS.boardTask(id))
      return normaliseTask(raw)
    },
    enabled: !!id,
  })
}

export function useBoards(): UseQueryResult<BoardRecord[]> {
  return useQuery<BoardRecord[]>({
    queryKey: ['board', 'boards'],
    queryFn: async () => {
      const raw = await apiGet<BoardRecord[] | { boards: BoardRecord[] }>(
        ENDPOINTS.boards,
      )
      if (Array.isArray(raw)) return raw
      if (raw && Array.isArray((raw as { boards: BoardRecord[] }).boards))
        return (raw as { boards: BoardRecord[] }).boards
      return []
    },
  })
}

export function useBoardProfiles(): UseQueryResult<BoardProfile[]> {
  return useQuery<BoardProfile[]>({
    queryKey: ['board', 'profiles'],
    queryFn: async () => {
      const raw = await apiGet<
        BoardProfile[] | { profiles: BoardProfile[] }
      >(ENDPOINTS.boardProfiles)
      const list = Array.isArray(raw)
        ? raw
        : raw && Array.isArray((raw as { profiles: BoardProfile[] }).profiles)
          ? (raw as { profiles: BoardProfile[] }).profiles
          : []
      return list.map(normaliseProfile) as BoardProfile[]
    },
  })
}

export function useBoardAssignees(board?: string): UseQueryResult<BoardAssignee[]> {
  return useQuery<BoardAssignee[]>({
    queryKey: ['board', 'assignees', board],
    queryFn: async () => {
      const qs = board ? `?board=${encodeURIComponent(board)}` : ''
      const raw = await apiGet<
        BoardAssignee[] | { assignees: BoardAssignee[] }
      >(`${ENDPOINTS.boardAssignees}${qs}`)
      const list = Array.isArray(raw)
        ? raw
        : raw && Array.isArray((raw as { assignees: BoardAssignee[] }).assignees)
          ? (raw as { assignees: BoardAssignee[] }).assignees
          : []
      return list.map(normaliseAssignee) as BoardAssignee[]
    },
  })
}

export function useBoardStats(board?: string): UseQueryResult<BoardStats> {
  return useQuery<BoardStats>({
    queryKey: ['board', 'stats', board],
    queryFn: async () => {
      const qs = board ? `?board=${encodeURIComponent(board)}` : ''
      return apiGet<BoardStats>(`${ENDPOINTS.boardStats}${qs}`)
    },
  })
}

export function useBoardConfig(): UseQueryResult<BoardConfig> {
  return useQuery<BoardConfig>({
    queryKey: ['board', 'config'],
    queryFn: () => apiGet<BoardConfig>(ENDPOINTS.boardConfig),
    staleTime: 60_000,
  })
}

export function useBoardOrchestration(): UseQueryResult<BoardOrchestration> {
  return useQuery<BoardOrchestration>({
    queryKey: ['board', 'orchestration'],
    queryFn: () => apiGet<BoardOrchestration>(ENDPOINTS.boardOrchestration),
  })
}

export function useBoardWorkersActive(): UseQueryResult<WorkerActive[]> {
  return useQuery<WorkerActive[]>({
    queryKey: ['board', 'workers', 'active'],
    queryFn: async () => {
      const raw = await apiGet<WorkerActive[] | { workers: WorkerActive[] }>(
        ENDPOINTS.boardWorkersActive,
      )
      if (Array.isArray(raw)) return raw
      if (raw && Array.isArray((raw as { workers: WorkerActive[] }).workers))
        return (raw as { workers: WorkerActive[] }).workers
      return []
    },
    refetchInterval: 5_000,
  })
}

export function useBoardRun(id: string): UseQueryResult<BoardRun> {
  return useQuery<BoardRun>({
    queryKey: ['board', 'run', id],
    queryFn: () => apiGet<BoardRun>(ENDPOINTS.boardRun(id)),
    enabled: !!id,
  })
}

export function useBoardTaskLog(
  id: string,
  tail?: number,
): UseQueryResult<TaskLogEntry[]> {
  return useQuery<TaskLogEntry[]>({
    queryKey: ['board', 'task', id, 'log', tail],
    queryFn: async () => {
      const qs = tail != null ? `?tail=${tail}` : ''
      const raw = await apiGet<
        TaskLogEntry[] | { entries?: TaskLogEntry[]; content?: string }
      >(`${ENDPOINTS.boardTaskLog(id)}${qs}`)
      if (Array.isArray(raw)) return raw
      if (raw && Array.isArray((raw as { entries: TaskLogEntry[] }).entries))
        return (raw as { entries: TaskLogEntry[] }).entries
      // Hermes kanban returns {task_id, path, exists, size_bytes, content}
      // where `content` is the raw log text — split into per-line entries so
      // the drawer (which joins e.line with "\n") renders it.
      const content = (raw as { content?: unknown })?.content
      if (typeof content === 'string')
        return content.length
          ? content.split('\n').map((line) => ({ line }))
          : []
      return []
    },
    enabled: !!id,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

// ── Mutations ─────────────────────────────────────────────────────────

export function useCreateTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateTaskBody) =>
      apiPost<BoardTask>(
        board
          ? `${ENDPOINTS.boardTasks}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTasks,
        body as unknown as Record<string, unknown>,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useUpdateTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateTaskBody }) =>
      apiPatch<BoardTask>(
        board
          ? `${ENDPOINTS.boardTask(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTask(id),
        body as unknown as Record<string, unknown>,
      ),
    onMutate: async ({ id, body }) => {
      // Optimistic: cancel in-flight board queries, snapshot, patch locally
      await qc.cancelQueries({ queryKey: boardKey(board) })
      const snapshot = qc.getQueryData<BoardView>(boardKey(board))
      if (snapshot && body.status) {
        // Rebuild a patched view
        const updatedTasks = snapshot.tasks.map((t) =>
          t.id === id ? { ...t, ...body, status: body.status as TaskStatus } : t,
        )
        const patched = normaliseBoardResponse(updatedTasks)
        qc.setQueryData<BoardView>(boardKey(board), patched)
      }
      return { snapshot }
    },
    onError: (_err, _vars, ctx) => {
      const c = ctx as { snapshot?: BoardView } | undefined
      if (c?.snapshot) {
        qc.setQueryData<BoardView>(boardKey(board), c.snapshot)
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
      // Also invalidate individual task cache
      qc.invalidateQueries({ queryKey: ['board', 'task'] })
    },
  })
}

export function useDeleteTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      apiDelete<unknown>(
        board
          ? `${ENDPOINTS.boardTask(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTask(id),
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useAddComment(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: string }) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardTaskComments(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTaskComments(id),
        { body },
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useAddLink(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (linkBody: LinkBody) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardLinks}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardLinks,
        linkBody as unknown as Record<string, unknown>,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useRemoveLink(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    // DELETE /links takes parent_id/child_id as QUERY params — the backend
    // forwards the query string verbatim and sends NO body on this route
    // (board.py remove_link), so ids in a JSON body are silently dropped
    // upstream and the dependency is never removed.
    mutationFn: (linkBody: LinkBody) => {
      const params = new URLSearchParams({
        parent_id: linkBody.parent_id,
        child_id: linkBody.child_id,
      })
      if (board) params.set('board', board)
      return api<unknown>(`${ENDPOINTS.boardLinks}?${params}`, {
        method: 'DELETE',
      })
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useBulkTasks(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BulkTasksBody) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardTasksBulk}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTasksBulk,
        body as unknown as Record<string, unknown>,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useReassignTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { assignee: string } }) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardTaskReassign(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTaskReassign(id),
        body as unknown as Record<string, unknown>,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useSpecifyTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardTaskSpecify(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTaskSpecify(id),
        body,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useDecomposeTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardTaskDecompose(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTaskDecompose(id),
        body,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useReclaimTask(board?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string
      body?: Record<string, unknown>
    }) =>
      apiPost<unknown>(
        board
          ? `${ENDPOINTS.boardTaskReclaim(id)}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardTaskReclaim(id),
        body ?? {},
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: boardKey(board) })
    },
  })
}

export function useCreateBoard() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateBoardBody) =>
      apiPost<BoardRecord>(ENDPOINTS.boards, body as unknown as Record<string, unknown>),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['board', 'boards'] })
    },
  })
}

export function useUpdateBoard() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      slug,
      body,
    }: {
      slug: string
      body: Partial<CreateBoardBody>
    }) =>
      apiPatch<BoardRecord>(
        ENDPOINTS.boardBySlug(slug),
        body as unknown as Record<string, unknown>,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['board', 'boards'] })
    },
  })
}

export function useDeleteBoard() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (slug: string) =>
      apiDelete<unknown>(`${ENDPOINTS.boardBySlug(slug)}?delete=true`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['board', 'boards'] })
    },
  })
}

export function useSwitchBoard() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (slug: string) =>
      apiPost<unknown>(ENDPOINTS.boardSwitch(slug)),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['board'] })
    },
  })
}

export function useUpdateOrchestration() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Partial<BoardOrchestration>) =>
      apiPut<BoardOrchestration>(
        ENDPOINTS.boardOrchestration,
        body as unknown as Record<string, unknown>,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['board', 'orchestration'] })
    },
  })
}

export function useNudgeDispatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ max }: { max?: number } = {}) => {
      const qs = max != null ? `?max=${max}` : ''
      return apiPost<unknown>(`${ENDPOINTS.boardDispatch}${qs}`)
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['board'] })
    },
  })
}

// ── WS events stream ─────────────────────────────────────────────────

/**
 * One frame from the events WS. The upstream kanban WS polls task_events and
 * pushes BATCH frames — `{"events": [...], "cursor": N}` — not single events.
 * `cursor` is the resume point: threaded back as `?since=` on reconnect so
 * the gap between a drop and the reconnect is replayed, not lost.
 */
export interface BoardEvent {
  events?: unknown[]
  cursor?: number
  [k: string]: unknown
}

export interface UseBoardEventsStreamOptions {
  board?: string
  tenant?: string
  since?: number
  follow?: boolean
}

export interface UseBoardEventsStreamResult {
  connected: boolean
  lastEvent: BoardEvent | null
}

/** Build the WS URL from the current page origin. */
export function boardEventsWsUrl(opts: {
  board?: string
  tenant?: string
  since?: number
} = {}): string {
  if (typeof window === 'undefined') return ''
  const wsBase = window.location.origin.replace(/^http/, 'ws')
  const params = new URLSearchParams()
  if (opts.board) params.set('board', opts.board)
  if (opts.tenant) params.set('tenant', opts.tenant)
  if (opts.since != null) params.set('since', String(opts.since))
  const qs = params.toString() ? `?${params}` : ''
  return `${wsBase}${ENDPOINTS.boardEvents}${qs}`
}

const WS_MAX_BACKOFF_MS = 16_000
// Hermes polls task_events every 300ms and pushes a frame per batch; without
// a debounce a busy run triggers a full GET /board refetch per frame.
const WS_INVALIDATE_DEBOUNCE_MS = 300

export function useBoardEventsStream(
  opts: UseBoardEventsStreamOptions = {},
): UseBoardEventsStreamResult {
  const { board, tenant, since, follow = true } = opts
  const [connected, setConnected] = useState(false)
  const [lastEvent, setLastEvent] = useState<BoardEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const errorCountRef = useRef(0)
  // Last frame cursor — used as `since` on reconnect to replay the gap.
  const cursorRef = useRef<number | null>(null)
  const qc = useQueryClient()

  useEffect(() => {
    if (typeof window === 'undefined' || !follow) {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
        setConnected(false)
      }
      return
    }

    let cancelled = false
    let backoffTimer: ReturnType<typeof setTimeout> | null = null
    let invalidateTimer: ReturnType<typeof setTimeout> | null = null

    const scheduleInvalidate = () => {
      if (invalidateTimer) return // trailing-edge: one refetch per window
      invalidateTimer = setTimeout(() => {
        invalidateTimer = null
        qc.invalidateQueries({ queryKey: boardKey(board) })
      }, WS_INVALIDATE_DEBOUNCE_MS)
    }

    const scheduleReconnect = () => {
      if (cancelled || backoffTimer) return
      errorCountRef.current += 1
      const delay = Math.min(
        1000 * 2 ** Math.min(errorCountRef.current - 1, 4),
        WS_MAX_BACKOFF_MS,
      )
      backoffTimer = setTimeout(() => {
        backoffTimer = null
        connect()
      }, delay)
    }

    const connect = () => {
      if (cancelled) return
      try {
        const url = boardEventsWsUrl({
          board,
          tenant,
          since: cursorRef.current ?? since,
        })
        wsRef.current = new WebSocket(url)
      } catch {
        setConnected(false)
        return
      }
      const ws = wsRef.current
      if (!ws) return

      ws.onopen = () => {
        setConnected(true)
        errorCountRef.current = 0
      }

      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(String(evt.data)) as BoardEvent
          if (typeof data.cursor === 'number') cursorRef.current = data.cursor
          setLastEvent(data)
          scheduleInvalidate()
        } catch {
          // ignore malformed
        }
      }

      // Reconnect is driven from `close` ONLY. The proxy ends the browser WS
      // with a close frame (1011 on upstream connect failure, normal close
      // when the upstream drops, e.g. a Hermes restart) — that fires just
      // `close` in browsers, never `error`. And every transport `error` is
      // always followed by `close`, so scheduling from both would double-book
      // the backoff timer.
      ws.onerror = () => {
        setConnected(false)
      }

      ws.onclose = () => {
        setConnected(false)
        if (wsRef.current === ws) wsRef.current = null
        scheduleReconnect()
      }
    }

    connect()

    return () => {
      cancelled = true
      if (backoffTimer) clearTimeout(backoffTimer)
      if (invalidateTimer) clearTimeout(invalidateTimer)
      if (wsRef.current) {
        // `cancelled` is set, so the close event this fires won't reconnect.
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [follow, board, tenant, since, qc])

  return { connected, lastEvent }
}

// ── Board chat (SSE via fetch) ────────────────────────────────────────

export interface ChatToolCall {
  name?: string
  arguments?: unknown
  id?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool'
  body: string
  at?: string
  refs?: string[]
  streaming?: boolean
  tool_call?: ChatToolCall
  /** Model reasoning (`thinking` SSE frames / `<think>` blocks) — folded away by the UI. */
  thinking?: string
  /** Tool messages: the matched tool_result payload once it lands. */
  result?: unknown
  /** Tool messages: running → done | error | pending (parked on operator approval) → approved | denied. */
  status?: 'running' | 'done' | 'error' | 'pending' | 'approved' | 'denied'
  /** Tool messages: the ApprovalQueue id when the call is gated (status=pending). */
  approval_id?: string
  /** Internal streaming marker: which assistant segment of which turn this bubble is. */
  seg?: string
  /** True for a bubble surfacing a chat-level failure (pre-stream HTTP error,
   * network failure, or in-stream SSE `error` frame) — lets the UI offer a
   * Retry affordance instead of just rendering the message. */
  error?: boolean
  /** Set alongside `error`: the operator's original composed text, so Retry
   * can resend it verbatim instead of making them retype it. */
  retryText?: string
}

export interface UseBoardChatResult {
  messages: ChatMessage[]
  send: (text: string) => void
  streaming: boolean
  /** Approve/deny a gated tool call inline (same endpoints as the top-bar bell). */
  resolveApproval: (approvalId: string, verdict: 'approve' | 'deny') => void
  /** Start a fresh session: abort any in-flight stream and clear the thread.
   * The history is rebuilt from `messages` on every send, so clearing them is
   * the only way to unstick a thread whose context has gone bad. */
  reset: () => void
  /** Abort the in-flight turn, keeping the thread. */
  stop: () => void
  /** Session-scoped auto-approve for gated tool calls (default off). When on,
   * approval_required frames are approved immediately — the paused turn then
   * continues with the executed result. Applies to EVERY gated tool,
   * including deletes; deliberately not persisted. */
  autoApprove: boolean
  setAutoApprove: (on: boolean) => void
}

// Client-side guard for reasoning models that inline `<think>…</think>` in
// content: the backend already splits these into `thinking` frames, but an
// older backend streaming raw think-tags must never show them in the bubble.
function splitThink(text: string): { thinking: string; visible: string } {
  if (!text.includes('<think>')) {
    // R1-style templates prefill the opening tag, so content may carry only
    // the closing tag — everything before it is reasoning.
    const close = text.indexOf('</think>')
    if (close !== -1) {
      return {
        thinking: text.slice(0, close).trim(),
        visible: text.slice(close + '</think>'.length).replace(/^\s+/, ''),
      }
    }
    return { thinking: '', visible: text }
  }
  const parts: string[] = []
  let visible = text.replace(/<think>([\s\S]*?)<\/think>/g, (_m, inner: string) => {
    if (inner.trim()) parts.push(inner.trim())
    return ''
  })
  const open = visible.indexOf('<think>')
  if (open !== -1) {
    // Unterminated trailing block — everything after the tag is reasoning.
    const inner = visible.slice(open + '<think>'.length)
    if (inner.trim()) parts.push(inner.trim())
    visible = visible.slice(0, open)
  }
  return { thinking: parts.join('\n'), visible }
}

export function useBoardChat(board?: string): UseBoardChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [autoApprove, setAutoApprove] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  // Read at frame-handling time (the SSE reader closure outlives renders).
  const autoApproveRef = useRef(autoApprove)
  autoApproveRef.current = autoApprove
  // Mirror `messages` in a ref so `send` can build the request history without
  // a stale closure (and without re-creating the callback every render).
  const messagesRef = useRef<ChatMessage[]>([])
  messagesRef.current = messages
  const qc = useQueryClient()

  const send = (text: string) => {
    // Cancel any in-flight SSE
    if (abortRef.current) {
      abortRef.current.abort()
    }
    abortRef.current = new AbortController()
    const signal = abortRef.current.signal

    // Build the OpenAI-style conversation the backend expects: prior turns
    // INCLUDING tool activity. Tool cards are rebuilt as a valid pair —
    // assistant tool_calls message + matching tool result — because a
    // history where the assistant says "let me check" and then answers
    // with no tool call in between teaches the model to hallucinate
    // results instead of calling tools (observed: long threads stopped
    // emitting tool calls entirely and invented slot lists).
    const history: Record<string, unknown>[] = []
    for (const m of messagesRef.current) {
      if ((m.role === 'user' || m.role === 'assistant') && (m.body ?? '').trim().length > 0) {
        history.push({ role: m.role, content: m.body })
      } else if (m.role === 'tool' && m.tool_call?.name) {
        const callId = m.tool_call.id || `call_${history.length}`
        history.push({
          role: 'assistant',
          content: null,
          tool_calls: [
            {
              id: callId,
              type: 'function',
              function: {
                name: m.tool_call.name,
                arguments: JSON.stringify(m.tool_call.arguments ?? {}),
              },
            },
          ],
        })
        let content: string
        try {
          content = JSON.stringify(m.result ?? { status: m.status ?? 'unknown' })
        } catch {
          content = String(m.result)
        }
        // Big reads (list_slots dumps ~10 KB per slot) would swamp the
        // context replayed on every send — the head is enough signal.
        if (content.length > 4000) content = content.slice(0, 4000) + '…(truncated)'
        history.push({
          role: 'tool',
          tool_call_id: callId,
          name: m.tool_call.name,
          content,
        })
      }
    }
    const outbound = [...history, { role: 'user', content: text }]

    // Append user message immediately
    setMessages((prev) => [
      ...prev,
      { role: 'user', body: text, at: new Date().toISOString() },
    ])
    setStreaming(true)

    const url = board
      ? `${ENDPOINTS.boardChat}?board=${encodeURIComponent(board)}`
      : ENDPOINTS.boardChat

    // Open SSE stream via fetch POST. Contract (see board_chat.py): the body
    // carries `messages` (OpenAI format) and optional `board`; the response
    // is SSE frames `{type: token|tool_call|tool_result|done|error}`.
    // `model` is deliberately OMITTED: board_chat.py resolves
    // `payload.get("model") or cfg.model or default_model`, so an explicit
    // client-sent model always wins and permanently defeats the
    // `[brain_chat].model` config override and the persona's
    // `preferred_model` (GH #1469). Leaving it unset lets that server-side
    // precedence chain do its job.
    const body: Record<string, unknown> = { messages: outbound }
    if (board) body.board = board
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(body),
      signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          // Pre-stream failure (503 slot.loading while warming, 502 on a
          // crash-looped backend, 401, gateway down, …). Lift the backend's
          // structured envelope — same helper client.ts's api() uses — and
          // surface it exactly like the in-stream SSE `error` frame below,
          // instead of silently dropping the turn (issue #1452).
          const err = await readErrorEnvelope(res)
          const retryAfterS = err.details?.retry_after_s
          const hint =
            typeof retryAfterS === 'number' && retryAfterS > 0
              ? ` — retry in ${retryAfterS}s`
              : ''
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              body: `⚠ ${err.message}${hint}`,
              at: new Date().toISOString(),
              error: true,
              retryText: text,
            },
          ])
          setStreaming(false)
          return
        }
        if (!res.body) {
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              body: '⚠ chat response had no stream body',
              at: new Date().toISOString(),
              error: true,
              retryText: text,
            },
          ])
          setStreaming(false)
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let accBody = ''
        let accThinking = ''
        let buf = ''
        // Assistant "segment" tracking. Tool calls split the assistant text
        // into separate bubbles (before/after the call). React batches the
        // queued setMessages updaters, so the updater must not read shared
        // mutable stream state — everything it needs is captured BY VALUE at
        // queue time and the target bubble is found by its segment tag.
        const turnTag = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
        let segment = 0

        const upsertAssistant = () => {
          // Re-split on every delta: think-tags can close in a later chunk.
          const { thinking, visible } = splitThink(accBody)
          const allThinking = [accThinking, thinking].filter(Boolean).join('\n')
          const seg = `${turnTag}:${segment}`
          setMessages((prev) => {
            const next = [...prev]
            const msg: ChatMessage = {
              role: 'assistant',
              body: visible,
              thinking: allThinking || undefined,
              streaming: true,
              seg,
            }
            const idx = next.findIndex((m) => m.role === 'assistant' && m.seg === seg)
            if (idx === -1) next.push(msg)
            else next[idx] = { ...next[idx], ...msg }
            return next
          })
        }

        const appendAssistant = (delta: string) => {
          accBody += delta
          upsertAssistant()
        }

        const appendThinking = (delta: string) => {
          accThinking = accThinking ? `${accThinking}\n${delta}` : delta
          upsertAssistant()
        }

        const finaliseAssistant = () => {
          setMessages((prev) =>
            prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
          )
        }

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buf += decoder.decode(value, { stream: true })
          const lines = buf.split('\n')
          buf = lines.pop() ?? ''

          for (const line of lines) {
            if (!line.startsWith('data:')) continue
            const payload = line.slice(5).trim()
            if (!payload) continue
            // Back-compat: some proxies still terminate with a bare [DONE].
            if (payload === '[DONE]') {
              finaliseAssistant()
              setStreaming(false)
              return
            }
            let frame: {
              type?: string
              text?: string
              name?: string
              arguments?: unknown
              result?: unknown
              id?: string
              message?: string
              approval_id?: string
            }
            try {
              frame = JSON.parse(payload)
            } catch {
              continue // ignore malformed
            }
            switch (frame.type) {
              case 'token':
                // Assistant text delta (backend sends per-round content).
                if (frame.text) appendAssistant(frame.text)
                break
              case 'thinking':
                // Model reasoning — kept off the reply body, folded in the UI.
                if (frame.text) appendThinking(frame.text)
                break
              case 'tool_call':
                // The steward is invoking a platform/board tool. Any assistant
                // text that follows belongs to a NEW bubble, not the one
                // preceding the call.
                accBody = ''
                accThinking = ''
                segment += 1
                setMessages((prev) => [
                  ...prev,
                  {
                    role: 'tool',
                    body: frame.name ?? 'tool',
                    tool_call: { name: frame.name, arguments: frame.arguments, id: frame.id },
                    status: 'running',
                  },
                ])
                break
              case 'tool_result': {
                // Attach the result to its tool message (matched by call id,
                // falling back to the last unresolved call with that name),
                // then refresh the board so mutations show live.
                const rFrame = frame
                setMessages((prev) => {
                  const next = [...prev]
                  for (let i = next.length - 1; i >= 0; i--) {
                    const m = next[i]
                    if (m.role !== 'tool') continue
                    // running = first result; pending/approved = the follow-up
                    // result the backend streams once a paused gated call is
                    // decided (executed / denied / failed).
                    if (!(m.status === 'running' || m.status === 'pending' || m.status === 'approved')) continue
                    const idMatch = rFrame.id && m.tool_call?.id === rFrame.id
                    const nameMatch = !rFrame.id && m.tool_call?.name === rFrame.name
                    if (idMatch || nameMatch) {
                      const res = rFrame.result as Record<string, unknown> | null
                      const isObj = !!res && typeof res === 'object'
                      // Truthy check, not key presence: job-shaped results
                      // (pull status, approvals) carry `error: null` when fine.
                      const isErr = isObj && !!res.error
                      // Gated tools park on the ApprovalQueue: surface the
                      // gate in the thread (also announced by a follow-up
                      // approval_required frame) instead of reading "done".
                      const isPending = isObj && res.status === 'pending_approval'
                      const isDenied = isObj && res.status === 'denied'
                      next[i] = {
                        ...m,
                        result: rFrame.result,
                        status: isErr ? 'error' : isPending ? 'pending' : isDenied ? 'denied' : 'done',
                        approval_id: isPending
                          ? String(res.approval_id ?? '') || undefined
                          : m.approval_id,
                      }
                      break
                    }
                  }
                  return next
                })
                qc.invalidateQueries({ queryKey: boardKey(board) })
                break
              }
              case 'approval_required': {
                // Explicit gate announcement (backend emits it right after the
                // pending_approval tool_result) — idempotent with the shape
                // detection above; also covers a future backend that skips the
                // tool_result frame for gated calls.
                const aFrame = frame
                setMessages((prev) =>
                  prev.map((m) =>
                    m.role === 'tool' &&
                    (aFrame.id ? m.tool_call?.id === aFrame.id : m.tool_call?.name === aFrame.name) &&
                    (m.status === 'running' || m.status === 'pending')
                      ? { ...m, status: 'pending', approval_id: aFrame.approval_id ?? m.approval_id }
                      : m,
                  ),
                )
                // Session auto-approve: unblock the paused turn immediately.
                if (autoApproveRef.current && aFrame.approval_id) {
                  resolveApproval(aFrame.approval_id, 'approve')
                }
                break
              }
              case 'error':
                setMessages((prev) => [
                  ...prev,
                  {
                    role: 'assistant',
                    body: `⚠ ${frame.message ?? 'chat error'}`,
                    at: new Date().toISOString(),
                  },
                ])
                break
              case 'done':
                finaliseAssistant()
                setStreaming(false)
                return
              default:
                break
            }
          }
        }

        finaliseAssistant()
        setStreaming(false)
      })
      .catch((err: unknown) => {
        const isAbort =
          err instanceof Error && err.name === 'AbortError'
        if (isAbort) return // operator hit Stop — intentional, not a failure
        // Network-level failure (gateway down, DNS, connection reset, …) —
        // same treatment as the pre-stream HTTP-error path above (#1452).
        const message =
          err instanceof Error && err.message ? err.message : 'chat request failed — network error'
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            body: `⚠ ${message}`,
            at: new Date().toISOString(),
            error: true,
            retryText: text,
          },
        ])
        setStreaming(false)
      })
  }

  const resolveApproval = (approvalId: string, verdict: 'approve' | 'deny') => {
    const url =
      verdict === 'approve'
        ? ENDPOINTS.agentApprovalApprove(approvalId)
        : ENDPOINTS.agentApprovalDeny(approvalId)
    fetch(url, { method: 'POST' })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        setMessages((prev) =>
          prev.map((m) =>
            m.approval_id === approvalId
              ? { ...m, status: verdict === 'approve' ? 'approved' : 'denied' }
              : m,
          ),
        )
        // The executor ran (or was dropped) server-side — refresh board state
        // and the bell's pending list.
        qc.invalidateQueries({ queryKey: boardKey(board) })
        qc.invalidateQueries({ queryKey: ['agents', 'approvals'] })
      })
      .catch(() => {
        /* leave the card pending — the bell remains the fallback path */
      })
  }

  const stop = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setStreaming(false)
    // Freeze the thread as-is: half-streamed bubbles stop pulsing and
    // unresolved tool cards read as stopped rather than spinning forever.
    setMessages((prev) =>
      prev.map((m) => {
        if (m.streaming) return { ...m, streaming: false }
        if (m.role === 'tool' && m.status === 'running') return { ...m, status: 'error' }
        return m
      }),
    )
  }

  const reset = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setMessages([])
    setStreaming(false)
  }

  return { messages, send, streaming, resolveApproval, reset, stop, autoApprove, setAutoApprove }
}
