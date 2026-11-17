// The fact workspace's window-global modules, loaded when the Memory area's Facts tab first
// opens instead of at startup. They are the only readers of the `window.__hal0Use*` memory hooks
// and of `window.__hal0D3Force` (which pulls in d3-force), so nothing else waits on them.
//
// Order matters: the bridge installs the hooks before the modules that read them evaluate, and
// `window.MemV2` (memory-v2-shared) must exist before the bar, workspace and graph render.

type WorkspaceGlobal = { MemV2Workspace?: unknown }

let pending: Promise<void> | null = null

export function memoryWorkspaceReady(): boolean {
  return typeof window !== 'undefined' && typeof (window as unknown as WorkspaceGlobal).MemV2Workspace === 'function'
}

export function loadMemoryWorkspace(): Promise<void> {
  if (memoryWorkspaceReady()) return Promise.resolve()
  if (!pending) {
    pending = (async () => {
      await import('./memory-hook-bridge')
      await import('./memory-v2-shared.jsx')
      await import('./memory-bank-bar.jsx')
      await import('./memory-bank-workspace.jsx')
      await import('./memory-web-graph.jsx')
    })().catch((error: unknown) => {
      pending = null
      throw error
    })
  }
  return pending
}
