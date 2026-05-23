<script setup>
/**
 * AgentActivityTab.vue — last ~50 MCP tool calls.
 *
 * Reads from GET /api/agents/{name}/activity (added by Wave 2 — the
 * existing /api/logs route only returns plain-text journal lines and
 * can't reliably surface the structlog audit fields the bell + this
 * table both need). The route shells journalctl with -o json + filters
 * for hal0.mcp.audit events.
 *
 * Filters:
 *   - tool (text input, substring match)
 *   - status (autonomous OK / gated→pending / denied / failed)
 *
 * No live tail. The audit ring is small enough that a polled refresh is
 * fine; SSE for this surface is a Phase 9 nice-to-have.
 */
import { computed, onMounted, ref } from 'vue'
import { useAgentStore } from '../../stores/agent.js'
import Card from '../Card.vue'

const agent = useAgentStore()

const toolFilter = ref('')
const statusFilter = ref('all')   // 'all' | 'ok' | 'pending' | 'denied' | 'failed'
const refreshing = ref(false)

async function refresh() {
  refreshing.value = true
  try {
    await agent.fetchActivity({ limit: 50 })
  } finally {
    refreshing.value = false
  }
}

onMounted(refresh)

const filtered = computed(() => {
  const q = toolFilter.value.trim().toLowerCase()
  return agent.activity.filter((row) => {
    if (q && !String(row.tool || '').toLowerCase().includes(q)) return false
    if (statusFilter.value !== 'all') {
      const s = row.outcome || row.status || 'ok'
      if (statusFilter.value === 'ok' && !(s === 'ok' || s === 'executed')) return false
      if (statusFilter.value === 'pending' && s !== 'enqueued' && s !== 'pending') return false
      if (statusFilter.value === 'denied' && s !== 'denied') return false
      if (statusFilter.value === 'failed' && s !== 'failed' && s !== 'error') return false
    }
    return true
  })
})

function fmtTs(epochSeconds) {
  if (!epochSeconds) return '—'
  try {
    const d = new Date(epochSeconds * 1000)
    return d.toISOString().slice(11, 19)
  } catch { return '—' }
}

function argsSummary(args) {
  if (!args || typeof args !== 'object') return ''
  const parts = []
  for (const [k, v] of Object.entries(args)) {
    if (v == null) continue
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      parts.push(`${k}=${v}`)
    }
    if (parts.join(' ').length > 60) break
  }
  return parts.join(' ')
}

function statusClass(row) {
  const s = row.outcome || row.status || 'ok'
  if (s === 'ok' || s === 'executed') return 'st-ok'
  if (s === 'enqueued' || s === 'pending') return 'st-pending'
  if (s === 'denied') return 'st-denied'
  return 'st-failed'
}

function statusLabel(row) {
  return row.outcome || row.status || 'ok'
}
</script>

<template>
  <div class="activity">
    <div class="activity-head">
      <div class="activity-l">
        <h2 class="activity-title">Activity</h2>
        <p class="activity-sub">
          Recent MCP tool calls from {{ agent.currentAgent?.name ?? 'the bundled agent' }}.
          Reads the same audit log feeding journald (per ADR-0004 §7).
        </p>
      </div>
      <button class="btn-ghost" type="button" :disabled="refreshing" @click="refresh">
        {{ refreshing ? 'Refreshing…' : 'Refresh' }}
      </button>
    </div>

    <div class="filters">
      <input
        v-model="toolFilter"
        class="filter-input"
        type="text"
        placeholder="filter by tool name…"
        aria-label="Filter by tool name"
      />
      <select v-model="statusFilter" class="filter-select" aria-label="Filter by status">
        <option value="all">All statuses</option>
        <option value="ok">OK / executed</option>
        <option value="pending">Pending</option>
        <option value="denied">Denied</option>
        <option value="failed">Failed</option>
      </select>
    </div>

    <Card v-if="filtered.length === 0" class="empty-card">
      <p class="empty-msg">
        <template v-if="agent.activity.length === 0">No MCP calls recorded yet.</template>
        <template v-else>No rows match the current filters.</template>
      </p>
    </Card>

    <Card v-else :padded="false">
      <table class="activity-table" aria-label="MCP audit rows">
        <thead>
          <tr>
            <th scope="col" class="col-ts">Time</th>
            <th scope="col" class="col-tool">Tool</th>
            <th scope="col" class="col-args">Args</th>
            <th scope="col" class="col-status">Status</th>
            <th scope="col" class="col-client">Client</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in filtered" :key="row.id ?? `${row.tool}-${row.timestamp}-${i}`">
            <td class="col-ts mono">{{ fmtTs(row.timestamp) }}</td>
            <td class="col-tool mono">{{ row.tool }}</td>
            <td class="col-args mono args-cell" :title="argsSummary(row.args)">{{ argsSummary(row.args) }}</td>
            <td class="col-status">
              <span class="status-pill mono" :class="statusClass(row)">{{ statusLabel(row) }}</span>
            </td>
            <td class="col-client mono client-cell">{{ row.client_id || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </Card>
  </div>
</template>

<style scoped>
.activity { display: flex; flex-direction: column; gap: 14px; }

.activity-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.activity-l { flex: 1; min-width: 0; }
.activity-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 4px;
  letter-spacing: -0.01em;
}
.activity-sub {
  font-size: 12.5px;
  color: var(--color-fg-muted);
  margin: 0;
  line-height: 1.55;
  max-width: 65ch;
}

.filters {
  display: flex;
  gap: 8px;
  align-items: center;
}
.filter-input {
  flex: 1;
  padding: 7px 10px;
  font-size: 12.5px;
  font-family: var(--font-mono);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg);
}
.filter-input:focus { outline: 2px solid var(--color-accent); outline-offset: 1px; }
.filter-select {
  padding: 7px 10px;
  font-size: 12.5px;
  font-family: var(--font-mono);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg);
}

.btn-ghost {
  padding: 7px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

/* Table */
.activity-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.activity-table th {
  text-align: left;
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-fg-faint);
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border);
  font-weight: 500;
}
.activity-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-fg);
  vertical-align: top;
}
.activity-table tr:last-child td { border-bottom: none; }
.activity-table tr:hover td { background: var(--color-surface-2); }

.mono { font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'ss02' 1; }
.col-ts { width: 90px; color: var(--color-fg-muted); }
.col-tool { width: 180px; color: var(--color-fg); }
.col-args { color: var(--color-fg-muted); }
.col-status { width: 110px; }
.col-client { width: 140px; color: var(--color-fg-muted); }
.args-cell, .client-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 360px; }

.status-pill {
  font-size: 10.5px;
  padding: 2px 7px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.st-ok {
  color: var(--color-success);
  border-color: color-mix(in srgb, var(--color-success) 30%, var(--color-border));
}
.st-pending {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 30%, var(--color-border));
}
.st-denied {
  color: var(--color-fg-faint);
}
.st-failed {
  color: var(--color-danger);
  border-color: color-mix(in srgb, var(--color-danger) 30%, var(--color-border));
}

.empty-card { padding: 24px; }
.empty-msg { font-size: 13px; color: var(--color-fg-muted); margin: 0; }
</style>
