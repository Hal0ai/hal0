// hal0 v3 dashboard — barrel export for hooks (Phase B1).
//
// Single re-export so views can `import { useSlots, useLemonadeHealth }
// from '@/api/hooks'`. MCP + Agent hooks are deliberately omitted —
// those views stay on HAL0_DATA mock for B1 (see issues #TBD).

export * from './useLemonade'
export * from './useSlots'
export * from './useModels'
export * from './useBackends'
export * from './useCapabilities'
export * from './useHardware'
export * from './useLogs'
export * from './useUpdates'
export * from './useSecrets'
export * from './useFirstRun'
export * from './useAgentMcpClients'
export * from './useMemory'
