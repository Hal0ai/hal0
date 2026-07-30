// hal0 dashboard — window-globals bridge for the Hindsight Memory view.
//
// MemoryView is a .jsx prototype file (no ES imports across dash/*).
// This bridge republishes the TanStack-Query Hindsight hooks under
// `window.__hal0Use*` so memory.jsx finds them at runtime. Must be
// imported in main.tsx BEFORE dash/memory.jsx evaluates.

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from 'd3-force'

import {
  useAggregateBankStats,
  useBankDelete,
  useBankDocuments,
  useBankGraph,
  useBankSubgraph,
  useDirectiveCreate,
  useDirectiveDelete,
  useDirectives,
  useDirectiveUpdate,
  useDocumentDelete,
  useDocumentReprocess,
  useEntityGraph,
  useMentalModelCreate,
  useMentalModelDelete,
  useMentalModelRefresh,
  useMentalModels,
  useRecall,
  useReflect,
  useBankOperations,
  useBankStats,
  useBankTimeseries,
  useBankUpsert,
  useConsolidate,
  useMemoryBanks,
  useMemoryEngine,
  useOperationCancel,
  useOperationRetry,
  summarizeBankOperations,
} from '@/api/hooks/useHindsight'
// ADR-0023 graph-extraction gate hooks — the Memory Overview drives its
// graph on/off line + slot picker from these (same source as the #agent
// Memory tab; bridged here too so #memory doesn't depend on that tab's
// bridge load order).
import {
  useMemoryGraphStatus,
  useRetryFailedExtractions,
  useUpdateMemoryGraph,
} from '@/api/hooks/useMemory'

Object.assign(window as unknown as Record<string, unknown>, {
  __hal0UseMemoryEngine: useMemoryEngine,
  __hal0UseMemoryGraphStatus: useMemoryGraphStatus,
  __hal0UseUpdateMemoryGraph: useUpdateMemoryGraph,
  __hal0UseMemoryBanks: useMemoryBanks,
  __hal0UseBankStats: useBankStats,
  __hal0UseAggregateBankStats: useAggregateBankStats,
  __hal0UseBankTimeseries: useBankTimeseries,
  __hal0UseBankUpsert: useBankUpsert,
  __hal0UseBankDelete: useBankDelete,
  __hal0UseBankOperations: useBankOperations,
  __hal0MemSummarizeOps: summarizeBankOperations,
  __hal0UseOperationRetry: useOperationRetry,
  __hal0UseOperationCancel: useOperationCancel,
  __hal0UseConsolidate: useConsolidate,
  __hal0UseRetryFailedExtractions: useRetryFailedExtractions,
  __hal0UseBankGraph: useBankGraph,
  __hal0UseBankSubgraph: useBankSubgraph,
  __hal0UseEntityGraph: useEntityGraph,
  __hal0UseRecall: useRecall,
  __hal0UseReflect: useReflect,
  __hal0UseBankDocuments: useBankDocuments,
  __hal0UseDocumentDelete: useDocumentDelete,
  __hal0UseDocumentReprocess: useDocumentReprocess,
  __hal0UseMentalModels: useMentalModels,
  __hal0UseMentalModelRefresh: useMentalModelRefresh,
  __hal0UseMentalModelCreate: useMentalModelCreate,
  __hal0UseMentalModelDelete: useMentalModelDelete,
  __hal0UseDirectives: useDirectives,
  __hal0UseDirectiveCreate: useDirectiveCreate,
  __hal0UseDirectiveUpdate: useDirectiveUpdate,
  __hal0UseDirectiveDelete: useDirectiveDelete,
  // d3-force layout primitives for the graph explorer (no-ES-imports .jsx).
  __hal0D3Force: { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY },
})
