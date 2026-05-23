<script setup>
/**
 * views/McpView.vue — `/agents/mcp` (Agents · v0.3).
 *
 * Slice #14 / issue #180. Single page, no internal tabs.
 *
 * Composition (top-down):
 *   - view header (eyebrow / title / hint / Connect-a-client / + Install)
 *   - McpKpiStrip (6 cells)
 *   - ClientsRibbon ↔ NoClientsState (whichever the data warrants)
 *   - Filter bar (segmented control + timeline-tick legend)
 *   - List of McpServerRow
 *   - Drawer / Modal cluster: InstallDrawer, EditConfigModal,
 *     LogsDrawer, ConfirmDialog (destructive uninstall),
 *     ConnectClientModal.
 *
 * Live tool-call ticks are driven by useLiveCallStream(servers).
 * Production swap: replace its inner setInterval body with a WS
 * subscription on `/api/mcp/stream`.
 *
 * Sidebar integration (Sidebar.vue slice #168 gated the row with a
 * tooltip; this slice unblocks the row + activates the route).
 */
import { computed, onMounted, ref } from 'vue'
import { useMcpStore, MCP_HOST_BASE } from '../stores/mcp.js'
import { useToastStore } from '../stores/toast.js'
import { useLiveCallStream } from '../composables/useLiveCallStream.js'
import McpKpiStrip from '../components/mcp/McpKpiStrip.vue'
import ClientsRibbon from '../components/mcp/ClientsRibbon.vue'
import NoClientsState from '../components/mcp/NoClientsState.vue'
import McpServerRow from '../components/mcp/McpServerRow.vue'
import InstallDrawer from '../components/mcp/InstallDrawer.vue'
import EditConfigModal from '../components/mcp/EditConfigModal.vue'
import LogsDrawer from '../components/mcp/LogsDrawer.vue'
import ConnectClientModal from '../components/mcp/ConnectClientModal.vue'
import ConfirmDialog from '../components/primitives/ConfirmDialog.vue'

const mcp = useMcpStore()
const toasts = useToastStore()

const installOpen = ref(false)
const configFor = ref(null)
const logsFor = ref(null)
const confirmUninstall = ref(null)
const teachOpen = ref(false)

const serversRef = computed(() => mcp.servers)
const { calls, now } = useLiveCallStream(serversRef)

onMounted(() => { mcp.fetch() })

const filters = computed(() => [
  { id: 'all',     label: 'All',     count: mcp.servers.length },
  { id: 'running', label: 'Running', count: mcp.runningCount },
  { id: 'bundled', label: 'Bundled', count: mcp.bundledCount },
  { id: 'stopped', label: 'Stopped', count: mcp.stoppedCount },
  { id: 'issues',  label: 'Issues',  count: mcp.issuesCount },
])

const filtered = computed(() => mcp.byFilter(mcp.filter))

const noClients = computed(() => (mcp.clients?.length ?? 0) === 0)

function onInstall(item) {
  mcp.install(item)
  toasts.push(`Installing ${item.name}…`, 'info')
  installOpen.value = false
}

function onSaveConfig(patch) {
  if (configFor.value) mcp.updateConfig(configFor.value.id, patch)
  configFor.value = null
}

function onToggleServer(server, next) {
  mcp.toggleEnabled(server.id, next)
  toasts.push(`${server.name} ${next ? 'started' : 'stopped'}`, 'info')
}

function onUninstallConfirmed() {
  const s = confirmUninstall.value
  if (!s) return
  mcp.uninstall(s.id)
  toasts.push(`${s.name} uninstalled`, 'warn')
  confirmUninstall.value = null
}

const uninstallTitle = computed(() => {
  return confirmUninstall.value ? `Uninstall ${confirmUninstall.value.name}?` : ''
})
const uninstallMessage = computed(() => {
  const s = confirmUninstall.value
  if (!s) return ''
  const n = s.clients?.length || 0
  return `Removes the server binary, env, and supervisor entry. Connected clients will lose access immediately. ${n} clients are currently connected.`
})
</script>

<template>
  <section class="view mcp-view" data-testid="mcp-view">
    <header class="vh">
      <span class="vh-eye mono">Agents · v0.3</span>
      <h1 class="vh-title">MCP Servers</h1>
      <span class="vh-spacer" />
      <span class="vh-hint mono">
        hal0 hosts an arbitrary number of MCP servers · clients connect over
        <span class="vh-hint-em">{{ MCP_HOST_BASE }}/mcp/*</span>
      </span>
      <button
        type="button"
        class="vh-ghost"
        data-testid="mcp-connect-client"
        @click="teachOpen = true"
      >Connect a client</button>
      <button
        type="button"
        class="vh-primary"
        data-testid="mcp-install-open"
        @click="installOpen = true"
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>
        Install
      </button>
    </header>

    <McpKpiStrip
      :servers="mcp.servers"
      :clients="mcp.clients"
      :calls="calls"
      :now="now"
    />

    <NoClientsState
      v-if="noClients"
      @teach="teachOpen = true"
    />
    <ClientsRibbon
      v-else
      :clients="mcp.clients"
      :calls="calls"
      :now="now"
      @teach="teachOpen = true"
    />

    <div class="mcp-filterbar">
      <div class="mcp-tabs" data-testid="mcp-filter-tabs">
        <button
          v-for="f in filters"
          :key="f.id"
          type="button"
          :class="['mcp-tab', { on: mcp.filter === f.id }]"
          :data-testid="`mcp-tab-${f.id}`"
          @click="mcp.filter = f.id"
        >
          <span>{{ f.label }}</span>
          <span class="mcp-tab-ct num">{{ f.count }}</span>
        </button>
      </div>
      <span class="spacer" />
      <div class="mcp-legend mono">
        <span class="lg"><span class="lg-tick glow" /> last 4s</span>
        <span class="lg"><span class="lg-tick" /> last 60s</span>
      </div>
    </div>

    <div class="mcp-list" data-testid="mcp-list">
      <McpServerRow
        v-for="s in filtered"
        :key="s.id"
        :server="s"
        :calls="calls"
        :now="now"
        :clients="mcp.clients"
        @config="(srv) => configFor = srv"
        @logs="(srv) => logsFor = srv"
        @uninstall="(srv) => confirmUninstall = srv"
        @toggle="onToggleServer"
      />
      <div v-if="filtered.length === 0" class="mcp-empty mono">
        No servers match this filter.
      </div>
    </div>

    <InstallDrawer
      :open="installOpen"
      :catalog="mcp.catalog"
      :categories="mcp.categories"
      @close="installOpen = false"
      @install="onInstall"
    />
    <EditConfigModal
      :open="!!configFor"
      :server="configFor"
      @close="configFor = null"
      @save="onSaveConfig"
    />
    <LogsDrawer
      :open="!!logsFor"
      :server="logsFor"
      @close="logsFor = null"
    />
    <ConnectClientModal
      :open="teachOpen"
      @close="teachOpen = false"
    />
    <ConfirmDialog
      :open="!!confirmUninstall"
      :title="uninstallTitle"
      :message="uninstallMessage"
      confirm-label="Uninstall"
      :destructive="true"
      :type-to-confirm="confirmUninstall?.name || ''"
      :on-cancel="() => confirmUninstall = null"
      :on-confirm="onUninstallConfirmed"
    />
  </section>
</template>

<style scoped>
.spacer { flex: 1; }

.mcp-view {
  padding: 24px 28px 80px;
  color: var(--fg);
  background: var(--bg);
  min-height: calc(100vh - 52px);
}

.vh {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}
.vh-eye {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--accent);
}
.vh-title {
  font-size: 24px;
  font-weight: 500;
  letter-spacing: -0.02em;
  margin: 0;
  color: var(--fg);
}
.vh-spacer { flex: 1; min-width: 12px; }
.vh-hint {
  font-size: 11px;
  color: var(--fg-4);
}
.vh-hint-em { color: var(--fg-2); }

.vh-ghost {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg-3);
  font-family: var(--hal0-font-mono);
  font-size: 12px;
  padding: 6px 12px;
  cursor: pointer;
}
.vh-ghost:hover { color: var(--fg); border-color: var(--line-strong); }
.vh-primary {
  background: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--rad-sm);
  color: #0a0a0a;
  font-family: var(--hal0-font-mono);
  font-size: 12px;
  padding: 6px 12px;
  cursor: pointer;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.vh-primary:hover { filter: brightness(1.06); }

.mcp-filterbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.mcp-tabs {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: var(--rad);
  overflow: hidden;
}
.mcp-tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 12px;
  background: transparent;
  border: none;
  border-right: 1px solid var(--line);
  font-family: var(--hal0-font-mono);
  font-size: 12px;
  color: var(--fg-3);
  cursor: pointer;
}
.mcp-tab:last-child { border-right: none; }
.mcp-tab:hover { color: var(--fg); background: var(--bg-2); }
.mcp-tab.on { color: var(--accent); background: var(--accent-soft); }
.mcp-tab-ct {
  font-size: 10px;
  color: var(--fg-5);
  padding: 1px 5px;
  background: var(--bg-2);
  border-radius: 3px;
  font-family: var(--hal0-font-mono);
}
.mcp-tab.on .mcp-tab-ct { color: var(--accent); background: transparent; }

.mcp-legend {
  display: inline-flex;
  align-items: center;
  gap: 14px;
  font-size: 10.5px;
  color: var(--fg-4);
}
.mcp-legend .lg {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.lg-tick {
  display: inline-block;
  width: 2px;
  height: 14px;
  background: var(--accent);
  opacity: 0.45;
}
.lg-tick.glow {
  opacity: 1;
  box-shadow: 0 0 6px var(--accent);
}

.mcp-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mcp-empty {
  padding: 32px;
  text-align: center;
  color: var(--fg-4);
  font-size: 12px;
  border: 1px dashed var(--line);
  border-radius: var(--rad);
}
</style>
