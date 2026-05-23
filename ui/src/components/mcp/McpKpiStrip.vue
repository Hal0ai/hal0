<script setup>
/**
 * mcp/McpKpiStrip.vue — six-cell aggregate strip above the server list.
 *
 * Mirrors the React `McpKpiStrip` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 66–99).
 *
 * Tones map to the v2 status palette:
 *   ok    → --ok (green)
 *   amber → --accent
 *   err   → --err
 *   warn  → --warn
 *   dim   → --fg-2
 *
 * The last cell is wider (`grid-template-columns: repeat(5,1fr) 1.8fr`)
 * and carries the "last activity" sub-text showing `client → tool`.
 */
import { computed } from 'vue'

const props = defineProps({
  servers: { type: Array, required: true },
  clients: { type: Array, required: true },
  calls:   { type: Object, required: true },
  now:     { type: Number, required: true },
})

const running = computed(() => props.servers.filter((s) => s.state === 'running').length)
const failed = computed(() => props.servers.filter((s) => s.state === 'failed').length)
const installing = computed(() => props.servers.filter((s) => s.state === 'installing').length)

const allCalls = computed(() => {
  const out = []
  for (const arr of props.calls.values()) out.push(...arr)
  out.sort((a, b) => b.ts - a.ts)
  // touch `now` so changes drive recompute (otherwise Map ref change suffices)
  void props.now
  return out
})

const lastCall = computed(() => allCalls.value[0] || null)
const lastAgo = computed(() => {
  if (!lastCall.value) return null
  return Math.floor((props.now - lastCall.value.ts) / 1000)
})

const cells = computed(() => [
  { l: 'running',     v: running.value, total: props.servers.length, tone: 'ok' },
  {
    l: 'clients',
    v: props.clients.length,
    sub: props.clients.map((c) => c.name).join(' · '),
    tone: 'amber',
  },
  { l: 'calls / 60s', v: allCalls.value.length, sub: 'live', tone: 'amber' },
  { l: 'failures',    v: failed.value, tone: failed.value ? 'err' : 'dim' },
  { l: 'installing',  v: installing.value, tone: installing.value ? 'warn' : 'dim' },
  {
    l: 'last activity',
    v: lastAgo.value === null ? '—' : (lastAgo.value < 1 ? 'now' : `${lastAgo.value}s`),
    sub: lastCall.value ? `${lastCall.value.client} → ${lastCall.value.tool}` : 'no recent calls',
    tone: 'dim',
    wide: true,
  },
])
</script>

<template>
  <div class="mcp-kpi" data-testid="mcp-kpi-strip">
    <div
      v-for="(s, i) in cells"
      :key="i"
      :class="['mcp-kpi-cell', { wide: s.wide }]"
      :data-testid="`mcp-kpi-${s.l.replace(/[^a-z0-9]+/gi, '-')}`"
    >
      <div class="mcp-kpi-l mono">{{ s.l }}</div>
      <div :class="['mcp-kpi-v', 'mono', 'num', `tone-${s.tone}`]">
        {{ s.v }}<span v-if="s.total !== undefined" class="mcp-kpi-total">/{{ s.total }}</span>
      </div>
      <div v-if="s.sub" class="mcp-kpi-sub mono">{{ s.sub }}</div>
    </div>
  </div>
</template>

<style scoped>
.mcp-kpi {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr)) minmax(0, 1.8fr);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  background: var(--bg-1);
  overflow: hidden;
  margin-bottom: 16px;
}
.mcp-kpi-cell {
  padding: 14px 18px;
  border-right: 1px solid var(--line-soft);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.mcp-kpi-cell:last-child {
  border-right: none;
  background: linear-gradient(90deg, transparent, rgba(255, 176, 0, 0.025));
}
.mcp-kpi-l {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-4);
}
.mcp-kpi-v {
  font-size: 24px;
  color: var(--fg);
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-family: var(--hal0-font-mono);
}
.mcp-kpi-v.tone-ok    { color: var(--ok); }
.mcp-kpi-v.tone-amber { color: var(--accent); }
.mcp-kpi-v.tone-err   { color: var(--err); }
.mcp-kpi-v.tone-warn  { color: var(--warn); }
.mcp-kpi-v.tone-dim   { color: var(--fg-2); }
.mcp-kpi-total {
  font-size: 13px;
  color: var(--fg-4);
  margin-left: 2px;
}
.mcp-kpi-sub {
  font-size: 11px;
  color: var(--fg-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
