<script setup>
/**
 * mcp/McpServerRow.vue — per-server card.
 *
 * Mirrors the React `McpServerRow` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 211–360).
 *
 * Three body variants:
 *   running / stopped → 3-col grid (connect / exposes / connected)
 *                       + LiveTimeline below
 *   installing        → progress bar + cancel link
 *   failed            → red error block with code pill + message
 *
 * Bundled servers carry an amber left rail; the overflow Menu
 * replaces the "Uninstall…" item with "Uninstall (bundled)" which
 * pushes a warn toast instead of opening the confirm dialog.
 *
 * The header band's three action btns (logs / restart / edit) are
 * state-gated; the overflow Menu always renders.
 */
import { computed, ref } from 'vue'
import Menu from '../primitives/Menu.vue'
import CopyField from './CopyField.vue'
import LiveTimeline from './LiveTimeline.vue'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  server:  { type: Object, required: true },
  calls:   { type: Object, required: true },
  now:     { type: Number, required: true },
  clients: { type: Array, required: true },
})

const emit = defineEmits(['config', 'logs', 'uninstall', 'toggle', 'restart'])

const toasts = useToastStore()
const menuOpen = ref(false)
const moreBtnRef = ref(null)

const isBundled = computed(() => !!props.server.bundled)
const state = computed(() => props.server.state)

const connectedClients = computed(() =>
  props.clients.filter((c) => Array.isArray(c.servers) && c.servers.includes(props.server.id)),
)

const callsLast60 = computed(() => {
  const arr = props.calls?.get?.(props.server.id) || []
  return arr.length
})

function chipActive(clientId) {
  const arr = props.calls?.get?.(props.server.id) || []
  return arr.some((e) => e.client === clientId && (props.now - e.ts) < 5000)
}

function onRestart() {
  emit('restart', props.server)
  toasts.push(`Restarting ${props.server.name}…`, 'info')
}

function onOpenInBrowser() {
  if (props.server.url) {
    try { window.open(props.server.url, '_blank', 'noopener') } catch {}
  }
  toasts.push(`Opening ${props.server.url || props.server.name}…`, 'info')
}

function onUninstallClick() {
  if (isBundled.value) {
    toasts.push('Bundled servers cannot be uninstalled', 'warn')
    return
  }
  emit('uninstall', props.server)
}

const menuItems = computed(() => {
  const items = [
    {
      label: state.value === 'running' ? 'Disable server' : 'Enable server',
      onClick: () => emit('toggle', props.server, state.value !== 'running'),
    },
    { label: 'Open in browser', onClick: onOpenInBrowser },
    { label: 'Restart',         onClick: onRestart },
    { label: 'Edit config',     onClick: () => emit('config', props.server) },
    { label: 'View logs',       onClick: () => emit('logs', props.server) },
    { divider: true },
  ]
  if (isBundled.value) {
    items.push({ label: 'Uninstall (bundled)', danger: true, onClick: onUninstallClick })
  } else {
    items.push({ label: 'Uninstall…', danger: true, onClick: onUninstallClick })
  }
  return items
})

function openMenu(e) {
  e.stopPropagation()
  menuOpen.value = !menuOpen.value
}
</script>

<template>
  <div
    :class="['mcp-row', `state-${state}`, { bundled: isBundled }]"
    :data-testid="`mcp-row-${server.id}`"
    :data-state="state"
  >
    <div class="mcp-row-h">
      <div class="mcp-row-id">
        <span class="mcp-row-name mono">{{ server.name }}</span>
        <span v-if="isBundled" class="mcp-row-bundled mono">bundled</span>
        <span class="mcp-row-ver mono">v{{ server.version }}</span>
        <span class="mcp-row-provider mono">· {{ server.provider }}</span>
      </div>

      <div class="mcp-row-state-cell">
        <span v-if="state === 'running'" class="mcp-state ok">
          <span class="dot ok" /> running<span class="state-dim mono">· {{ server.since }}</span>
        </span>
        <span v-else-if="state === 'stopped'" class="mcp-state dim">
          <span class="dot empty" /> stopped<span class="state-dim mono">· {{ server.since }}</span>
        </span>
        <span v-else-if="state === 'failed'" class="mcp-state err">
          <span class="dot err" /> failed<span class="state-dim mono">· {{ server.lastError?.code }}</span>
        </span>
        <span v-else-if="state === 'installing'" class="mcp-state warn">
          <span class="dot warn" /> installing<span class="state-dim mono">· {{ server.progressLabel }}</span>
        </span>
      </div>

      <div class="mcp-row-actions">
        <template v-if="state === 'running'">
          <button type="button" class="mcp-icon-btn" :data-testid="`mcp-logs-${server.id}`" title="View logs" @click="emit('logs', server)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3h10M3 6h10M3 9h7M3 12h5"/></svg>
          </button>
          <button type="button" class="mcp-icon-btn" :data-testid="`mcp-restart-${server.id}`" title="Restart" @click="onRestart">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 8a6 6 0 1 0 6-6v4M2 4v4h4"/></svg>
          </button>
          <button type="button" class="mcp-icon-btn" :data-testid="`mcp-edit-${server.id}`" title="Edit config" @click="emit('config', server)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 2l3 3-8 8H3v-3l8-8z"/></svg>
          </button>
        </template>
        <template v-else-if="state === 'stopped'">
          <button type="button" class="mcp-btn-sm" :data-testid="`mcp-start-${server.id}`" @click="emit('toggle', server, true)">Start</button>
          <button type="button" class="mcp-icon-btn" :data-testid="`mcp-edit-${server.id}`" title="Edit config" @click="emit('config', server)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 2l3 3-8 8H3v-3l8-8z"/></svg>
          </button>
        </template>
        <template v-else-if="state === 'failed'">
          <button type="button" class="mcp-btn-sm" :data-testid="`mcp-fix-${server.id}`" @click="emit('config', server)">Fix config</button>
          <button type="button" class="mcp-icon-btn" :data-testid="`mcp-logs-${server.id}`" title="View logs" @click="emit('logs', server)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3h10M3 6h10M3 9h7M3 12h5"/></svg>
          </button>
        </template>
        <template v-else-if="state === 'installing'">
          <button type="button" class="mcp-icon-btn" disabled>installing…</button>
        </template>

        <div class="mcp-row-more">
          <button
            ref="moreBtnRef"
            type="button"
            class="mcp-icon-btn"
            :data-testid="`mcp-more-${server.id}`"
            aria-haspopup="menu"
            :aria-expanded="menuOpen"
            @click="openMenu"
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <circle cx="3" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="13" cy="8" r="1.4"/>
            </svg>
          </button>
          <Menu
            :open="menuOpen"
            :anchor="moreBtnRef"
            :items="menuItems"
            :on-close="() => menuOpen = false"
            side="right"
          />
        </div>
      </div>
    </div>

    <div class="mcp-row-body">
      <div class="mcp-row-desc">{{ server.description }}</div>

      <div v-if="state === 'installing'" class="mcp-installing">
        <div class="mcp-installing-bar">
          <div class="mcp-installing-bar-fill" :style="{ width: `${server.progress || 0}%` }" />
        </div>
        <div class="mcp-installing-meta mono">
          <span>{{ server.progress }}% · {{ server.progressLabel }}</span>
          <span class="spacer" />
          <button type="button" class="mcp-link mono">Cancel install →</button>
        </div>
      </div>

      <div v-else-if="state === 'failed'" class="mcp-failed" data-testid="mcp-failed-block">
        <div class="mcp-failed-h mono">
          <span class="mcp-failed-code">{{ server.lastError?.code }}</span>
          last attempt {{ server.lastError?.ts }} · attempt #{{ server.lastError?.attempts }}
        </div>
        <div class="mcp-failed-body mono">{{ server.lastError?.msg }}</div>
      </div>

      <div v-else class="mcp-row-grid">
        <div class="mcp-cell">
          <div class="mcp-cell-l mono">connect url</div>
          <CopyField :value="server.url || '(unavailable)'" />
          <div class="mcp-cell-sub mono">{{ server.transport }} · pid {{ server.pid || '—' }}</div>
        </div>

        <div class="mcp-cell">
          <div class="mcp-cell-l mono">exposes</div>
          <div class="mcp-cell-caps mono">
            <template v-if="server.tools !== null && server.tools !== undefined">
              <span><b class="num">{{ server.tools }}</b> tools</span>
              <template v-if="server.resources > 0">
                <span class="dim">·</span><span><b class="num">{{ server.resources }}</b> resources</span>
              </template>
              <template v-if="server.prompts > 0">
                <span class="dim">·</span><span><b class="num">{{ server.prompts }}</b> prompts</span>
              </template>
            </template>
            <span v-else class="dim">—</span>
          </div>
          <div class="mcp-cell-sub mono">{{ server.transport }}</div>
        </div>

        <div class="mcp-cell">
          <div class="mcp-cell-l mono">connected<span class="ct"> · {{ connectedClients.length }}</span></div>
          <div class="mcp-clients-chips">
            <span v-if="connectedClients.length === 0" class="dim mono">no clients</span>
            <span
              v-for="c in connectedClients"
              v-else
              :key="c.id"
              :class="['mcp-client-chip', 'mono', { active: chipActive(c.id) }]"
            >
              <span :class="['mcp-client-chip-dot', { pulsing: chipActive(c.id) }]" />
              {{ c.name }}
            </span>
          </div>
          <div class="mcp-cell-sub mono">{{ callsLast60 }} calls in last 60s</div>
        </div>
      </div>

      <LiveTimeline
        v-if="state === 'running' || state === 'stopped'"
        :server-id="server.id"
        :calls="calls"
        :now="now"
        :state="state"
      />
    </div>
  </div>
</template>

<style scoped>
@keyframes mcp-row-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(1.35); opacity: 0.75; }
}

.spacer { flex: 1; }

.mcp-row {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  overflow: hidden;
  transition: border-color 0.12s ease;
}
.mcp-row:hover { border-color: var(--line-strong); }
.mcp-row.state-running.bundled { border-left: 2px solid var(--accent); }
.mcp-row.state-failed { border-color: var(--err-line); background: linear-gradient(90deg, rgba(239, 107, 107, 0.025), var(--bg-1)); }
.mcp-row.state-installing { border-color: var(--warn-line); }
.mcp-row.state-stopped { opacity: 0.85; }

.mcp-row-h {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 14px;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--bg);
}
.mcp-row-id {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  flex-wrap: wrap;
}
.mcp-row-name {
  font-size: 15.5px;
  color: var(--fg);
  font-weight: 500;
  letter-spacing: -0.01em;
  font-family: var(--hal0-font-mono);
}
.mcp-row-bundled {
  font-size: 9px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent);
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
  padding: 1px 6px;
  border-radius: 2px;
}
.mcp-row-ver, .mcp-row-provider {
  font-size: 11px;
  color: var(--fg-4);
}

.mcp-row-state-cell { display: flex; align-items: center; }
.mcp-state {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--hal0-font-mono);
  font-size: 11.5px;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.mcp-state .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
  box-shadow: 0 0 6px currentColor;
}
.mcp-state .state-dim { color: var(--fg-4); margin-left: 2px; font-size: 10.5px; font-weight: 400; }
.mcp-state.ok   { color: var(--ok);  border-color: var(--ok-line);   background: var(--ok-soft); }
.mcp-state.dim  { color: var(--fg-3); border-color: var(--line); }
.mcp-state.err  { color: var(--err); border-color: var(--err-line);  background: var(--err-soft); }
.mcp-state.warn { color: var(--warn); border-color: var(--warn-line); background: var(--warn-soft); }

.mcp-row-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}
.mcp-row-more { position: relative; }
.mcp-icon-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  cursor: pointer;
  color: var(--fg-3);
  padding: 0;
}
.mcp-icon-btn:hover { color: var(--fg); border-color: var(--line-strong); }
.mcp-icon-btn[disabled] { opacity: 0.6; cursor: default; font-family: var(--hal0-font-mono); font-size: 10px; width: auto; padding: 0 9px; }
.mcp-btn-sm {
  background: var(--bg-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--rad-sm);
  color: var(--fg);
  font-family: var(--hal0-font-mono);
  font-size: 11.5px;
  padding: 4px 10px;
  cursor: pointer;
}
.mcp-btn-sm:hover { border-color: var(--accent-line); color: var(--accent); }

.mcp-row-body {
  padding: 14px 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.mcp-row-desc {
  font-size: 13px;
  color: var(--fg-2);
  line-height: 1.5;
  max-width: 88ch;
}

.mcp-row-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 18px;
  padding: 12px 0;
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
}
.mcp-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.mcp-cell-l {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-4);
}
.mcp-cell-l .ct { color: var(--fg-5); }
.mcp-cell-sub {
  font-size: 10.5px;
  color: var(--fg-4);
}
.mcp-cell-caps {
  font-size: 13px;
  color: var(--fg-2);
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}
.mcp-cell-caps b { color: var(--fg); font-weight: 500; }
.mcp-cell-caps .dim { color: var(--fg-5); }

.mcp-clients-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.mcp-clients-chips .dim { color: var(--fg-5); font-size: 11.5px; padding: 2px 0; }
.mcp-client-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  font-size: 10.5px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--fg-2);
}
.mcp-client-chip.active {
  color: var(--accent);
  border-color: var(--accent-line);
  background: var(--accent-soft);
}
.mcp-client-chip-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 6px var(--ok);
}
.mcp-client-chip-dot.pulsing {
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
  animation: mcp-row-pulse 1.0s ease-in-out infinite;
}

.mcp-failed {
  border: 1px solid var(--err-line);
  background: var(--err-soft);
  border-radius: var(--rad-sm);
  padding: 10px 12px;
  font-family: var(--hal0-font-mono);
  font-size: 11.5px;
  line-height: 1.55;
}
.mcp-failed-h { color: var(--fg-3); display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 10.5px; flex-wrap: wrap; }
.mcp-failed-code {
  color: var(--err);
  padding: 1px 6px;
  border: 1px solid var(--err-line);
  background: rgba(0, 0, 0, 0.2);
  border-radius: 2px;
  letter-spacing: 0.04em;
}
.mcp-failed-body { color: var(--fg-2); }

.mcp-installing {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mcp-installing-bar {
  position: relative;
  height: 6px;
  background: var(--bg-3);
  border-radius: 1px;
  overflow: hidden;
}
.mcp-installing-bar-fill {
  height: 100%;
  background: var(--warn);
  transition: width 0.3s ease;
  position: relative;
  overflow: hidden;
}
.mcp-installing-bar-fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.18), transparent);
  animation: mcp-shimmer 1.4s linear infinite;
}
@keyframes mcp-shimmer {
  from { transform: translateX(-100%); }
  to   { transform: translateX(100%); }
}
.mcp-installing-meta {
  display: flex;
  align-items: center;
  font-size: 11px;
  color: var(--fg-3);
}
.mcp-link {
  background: none;
  border: none;
  color: var(--accent);
  font-family: var(--hal0-font-mono);
  cursor: pointer;
  font-size: 11px;
}
.mcp-link:hover { text-decoration: underline; }
</style>
