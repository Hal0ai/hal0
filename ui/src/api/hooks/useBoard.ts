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
import { api, apiGet, apiPost, apiPatch, apiPut, apiDelete } from '../client'
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

// ── Wire-to-normalised task transform ─────────────────────────────────
//
// The server may send either camelCase or snake_case for legacy compat;
// normalise everything to camelCase for the UI.

function normaliseTask(raw: Record<string, unknown>): BoardTask {
  const assignee =
    (raw.assignee ?? raw.profile ?? null) as string | null
  const createdBy =
    (raw.created_by ?? raw.createdBy ?? null) as string | null
  const blockReason =
    (raw.block_reason ?? raw.blockReason ?? null) as string | null
  const body = (raw.body ?? raw.desc ?? null) as string | null
  const commentCount =
    typeof (raw.comment_count ?? raw.commentCount) === 'number'
      ? (raw.comment_count ?? raw.commentCount) as number
      : 0
  const depCount =
    (raw.dep_count ?? raw.depCount ?? null) as string | null

  const rawDeps = (raw.deps ?? {}) as {
    parents?: string[]
    children?: string[]
  }
  const deps: TaskDeps = {
    parents: Array.isArray(rawDeps.parents) ? rawDeps.parents : [],
    children: Array.isArray(rawDeps.children) ? rawDeps.children : [],
  }

  return {
    id: String(raw.id ?? ''),
    title: String(raw.title ?? ''),
    status: (raw.status ?? 'triage') as TaskStatus,
    assignee,
    tenant: raw.tenant as string | undefined,
    priority: raw.priority as number | undefined,
    workspace: raw.workspace as string | undefined,
    createdBy,
    created: raw.created as string | undefined,
    body,
    blockReason,
    schedule: raw.schedule as string | undefined,
    summary: raw.summary as string | undefined,
    deps,
    comments: Array.isArray(raw.comments)
      ? (raw.comments as TaskComment[])
      : [],
    events: Array.isArray(raw.events) ? (raw.events as TaskEvent[]) : [],
    runs: Array.isArray(raw.runs) ? (raw.runs as TaskRun[]) : [],
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
        TaskLogEntry[] | { entries: TaskLogEntry[] }
      >(`${ENDPOINTS.boardTaskLog(id)}${qs}`)
      if (Array.isArray(raw)) return raw
      if (raw && Array.isArray((raw as { entries: TaskLogEntry[] }).entries))
        return (raw as { entries: TaskLogEntry[] }).entries
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
    mutationFn: (linkBody: LinkBody) =>
      api<unknown>(
        board
          ? `${ENDPOINTS.boardLinks}?board=${encodeURIComponent(board)}`
          : ENDPOINTS.boardLinks,
        {
          method: 'DELETE',
          body: linkBody as unknown as Record<string, unknown>,
        },
      ),
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

export interface BoardEvent {
  kind?: string
  task_id?: string
  at?: string
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

export function useBoardEventsStream(
  opts: UseBoardEventsStreamOptions = {},
): UseBoardEventsStreamResult {
  const { board, tenant, since, follow = true } = opts
  const [connected, setConnected] = useState(false)
  const [lastEvent, setLastEvent] = useState<BoardEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const errorCountRef = useRef(0)
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

    const connect = () => {
      if (cancelled) return
      try {
        const url = boardEventsWsUrl({ board, tenant, since })
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
          setLastEvent(data)
          // Invalidate board query so cards refresh live
          qc.invalidateQueries({ queryKey: boardKey(board) })
        } catch {
          // ignore malformed
        }
      }

      ws.onerror = () => {
        setConnected(false)
        errorCountRef.current += 1
        if (wsRef.current) {
          wsRef.current.close()
          wsRef.current = null
        }
        const delay = Math.min(
          1000 * 2 ** Math.min(errorCountRef.current - 1, 4),
          WS_MAX_BACKOFF_MS,
        )
        backoffTimer = setTimeout(connect, delay)
      }

      ws.onclose = () => {
        setConnected(false)
      }
    }

    connect()

    return () => {
      cancelled = true
      if (backoffTimer) clearTimeout(backoffTimer)
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [follow, board, tenant, since, qc])

  return { connected, lastEvent }
}

// ── Board chat (SSE via fetch) ────────────────────────────────────────

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool'
  body: string
  at?: string
  refs?: string[]
  streaming?: boolean
  tool_call?: unknown
}

export interface UseBoardChatResult {
  messages: ChatMessage[]
  send: (text: string) => void
  streaming: boolean
}

// Board chat runs on the `agent` slot (the tool-calling orchestrator model),
// not the conversational `chat` slot. Sent explicitly so it routes correctly
// regardless of the backend's PRIMARY_SLOT_MODEL default.
const BOARD_CHAT_MODEL = 'hal0/agent'

export function useBoardChat(board?: string): UseBoardChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
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

    // Build the OpenAI-style conversation the backend expects: prior
    // user/assistant turns + this new user message. Tool frames are UI-only
    // and intentionally omitted (sending bare tool messages without their
    // originating assistant tool_calls is malformed for the LLM).
    const history = messagesRef.current
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .filter((m) => (m.body ?? '').trim().length > 0)
      .map((m) => ({ role: m.role, content: m.body }))
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
    // carries `messages` (OpenAI format), optional `board`, and `model`; the
    // response is SSE frames `{type: token|tool_call|tool_result|done|error}`.
    // `model` is sent explicitly so board chat runs on the `agent` slot (the
    // tool-calling orchestrator model) — board_chat.py honours payload.model
    // over its default, so this routes correctly without a backend restart.
    const body: Record<string, unknown> = { messages: outbound, model: BOARD_CHAT_MODEL }
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
        if (!res.ok || !res.body) {
          setStreaming(false)
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let accBody = ''
        let assistantIdx = -1
        let buf = ''

        const appendAssistant = (delta: string) => {
          accBody += delta
          setMessages((prev) => {
            const next = [...prev]
            if (assistantIdx === -1 || assistantIdx >= next.length) {
              next.push({
                role: 'assistant',
                body: accBody,
                streaming: true,
              })
              assistantIdx = next.length - 1
            } else {
              next[assistantIdx] = {
                ...next[assistantIdx],
                body: accBody,
                streaming: true,
              }
            }
            return next
          })
        }

        const finaliseAssistant = () => {
          setMessages((prev) => {
            const next = [...prev]
            if (assistantIdx >= 0 && assistantIdx < next.length) {
              next[assistantIdx] = { ...next[assistantIdx], streaming: false }
            }
            return next
          })
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
              case 'tool_call':
                // The orchestrator is invoking an audited board mutation.
                setMessages((prev) => [
                  ...prev,
                  {
                    role: 'tool',
                    body: `→ ${frame.name ?? 'tool'}(${JSON.stringify(frame.arguments ?? {})})`,
                    tool_call: { name: frame.name, arguments: frame.arguments, id: frame.id },
                  },
                ])
                break
              case 'tool_result':
                // Mutation landed — refresh the board so the change shows live.
                qc.invalidateQueries({ queryKey: boardKey(board) })
                break
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
        if (!isAbort) {
          setStreaming(false)
        }
      })
  }

  return { messages, send, streaming }
}
