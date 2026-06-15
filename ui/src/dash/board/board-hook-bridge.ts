// hal0 dashboard — window-globals bridge for Operator Board hooks.
//
// board/*.jsx are window-globals prototype files (no ES imports across dash/*).
// This bridge republishes every TanStack-Query board hook under
// `window.__hal0Use*` so board JSX finds them at runtime.
//
// Template: ui/src/dash/agents/memory-tab-hook-bridge.ts

import {
  boardKey,
  boardEventsWsUrl,
  useBoardView,
  useBoardTask,
  useBoards,
  useBoardProfiles,
  useBoardAssignees,
  useBoardStats,
  useBoardConfig,
  useBoardOrchestration,
  useBoardWorkersActive,
  useBoardRun,
  useBoardTaskLog,
  useCreateTask,
  useUpdateTask,
  useDeleteTask,
  useAddComment,
  useAddLink,
  useRemoveLink,
  useBulkTasks,
  useReassignTask,
  useSpecifyTask,
  useDecomposeTask,
  useReclaimTask,
  useCreateBoard,
  useUpdateBoard,
  useDeleteBoard,
  useSwitchBoard,
  useUpdateOrchestration,
  useNudgeDispatch,
  useBoardEventsStream,
  useBoardChat,
} from '@/api/hooks/useBoard'

// Declare window extension type for TS
declare global {
  interface Window {
    __hal0BoardKey?: typeof boardKey
    __hal0BoardEventsWsUrl?: typeof boardEventsWsUrl
    __hal0UseBoardView?: typeof useBoardView
    __hal0UseBoardTask?: typeof useBoardTask
    __hal0UseBoards?: typeof useBoards
    __hal0UseBoardProfiles?: typeof useBoardProfiles
    __hal0UseBoardAssignees?: typeof useBoardAssignees
    __hal0UseBoardStats?: typeof useBoardStats
    __hal0UseBoardConfig?: typeof useBoardConfig
    __hal0UseBoardOrchestration?: typeof useBoardOrchestration
    __hal0UseBoardWorkersActive?: typeof useBoardWorkersActive
    __hal0UseBoardRun?: typeof useBoardRun
    __hal0UseBoardTaskLog?: typeof useBoardTaskLog
    __hal0UseCreateTask?: typeof useCreateTask
    __hal0UseUpdateTask?: typeof useUpdateTask
    __hal0UseDeleteTask?: typeof useDeleteTask
    __hal0UseAddComment?: typeof useAddComment
    __hal0UseAddLink?: typeof useAddLink
    __hal0UseRemoveLink?: typeof useRemoveLink
    __hal0UseBulkTasks?: typeof useBulkTasks
    __hal0UseReassignTask?: typeof useReassignTask
    __hal0UseSpecifyTask?: typeof useSpecifyTask
    __hal0UseDecomposeTask?: typeof useDecomposeTask
    __hal0UseReclaimTask?: typeof useReclaimTask
    __hal0UseCreateBoard?: typeof useCreateBoard
    __hal0UseUpdateBoard?: typeof useUpdateBoard
    __hal0UseDeleteBoard?: typeof useDeleteBoard
    __hal0UseSwitchBoard?: typeof useSwitchBoard
    __hal0UseUpdateOrchestration?: typeof useUpdateOrchestration
    __hal0UseNudgeDispatch?: typeof useNudgeDispatch
    __hal0UseBoardEventsStream?: typeof useBoardEventsStream
    __hal0UseBoardChat?: typeof useBoardChat
  }
}

window.__hal0BoardKey = boardKey
window.__hal0BoardEventsWsUrl = boardEventsWsUrl
window.__hal0UseBoardView = useBoardView
window.__hal0UseBoardTask = useBoardTask
window.__hal0UseBoards = useBoards
window.__hal0UseBoardProfiles = useBoardProfiles
window.__hal0UseBoardAssignees = useBoardAssignees
window.__hal0UseBoardStats = useBoardStats
window.__hal0UseBoardConfig = useBoardConfig
window.__hal0UseBoardOrchestration = useBoardOrchestration
window.__hal0UseBoardWorkersActive = useBoardWorkersActive
window.__hal0UseBoardRun = useBoardRun
window.__hal0UseBoardTaskLog = useBoardTaskLog
window.__hal0UseCreateTask = useCreateTask
window.__hal0UseUpdateTask = useUpdateTask
window.__hal0UseDeleteTask = useDeleteTask
window.__hal0UseAddComment = useAddComment
window.__hal0UseAddLink = useAddLink
window.__hal0UseRemoveLink = useRemoveLink
window.__hal0UseBulkTasks = useBulkTasks
window.__hal0UseReassignTask = useReassignTask
window.__hal0UseSpecifyTask = useSpecifyTask
window.__hal0UseDecomposeTask = useDecomposeTask
window.__hal0UseReclaimTask = useReclaimTask
window.__hal0UseCreateBoard = useCreateBoard
window.__hal0UseUpdateBoard = useUpdateBoard
window.__hal0UseDeleteBoard = useDeleteBoard
window.__hal0UseSwitchBoard = useSwitchBoard
window.__hal0UseUpdateOrchestration = useUpdateOrchestration
window.__hal0UseNudgeDispatch = useNudgeDispatch
window.__hal0UseBoardEventsStream = useBoardEventsStream
window.__hal0UseBoardChat = useBoardChat
