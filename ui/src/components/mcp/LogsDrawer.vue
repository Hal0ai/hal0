<script setup>
/**
 * mcp/LogsDrawer.vue — per-server live-tail log drawer.
 *
 * Mirrors the React `LogsDrawer` in
 *   /tmp/hal0-design-v3/dash/mcp-modals.jsx (lines 229–276).
 *
 * v0.3 ships a canned sample tail — same set of lines the React mock
 * shows — so the visual contract lands. v0.3.1 will swap this for a
 * subscription on `/api/mcp/<id>/logs/stream` (SSE, matching the
 * existing Lemonade journal panel pattern).
 */
import { computed } from 'vue'
import Drawer from '../primitives/Drawer.vue'

const props = defineProps({
  open:   { type: Boolean, default: false },
  server: { type: Object, default: null },
})
const emit = defineEmits(['close'])

const sample = computed(() => {
  if (!props.server) return []
  const name = props.server.name
  const pid = props.server.pid || '—'
  return [
    { ts: '14:02:11.117', lvl: 'ok',   src: 'supervisor', msg: `${name} pid ${pid} up · 14d 02:11` },
    { ts: '14:02:30.290', lvl: 'info', src: name,         msg: 'tool call: slot.list' },
    { ts: '14:02:30.310', lvl: 'info', src: name,         msg: '→ 9 results (claude-code)' },
    { ts: '14:02:34.117', lvl: 'info', src: name,         msg: 'tool call: lemond.status' },
    { ts: '14:02:34.121', lvl: 'info', src: name,         msg: "→ {status: 'up', ...} (cursor)" },
    { ts: '14:02:39.443', lvl: 'ok',   src: name,         msg: "tool call: model.search query='reranker'" },
    { ts: '14:02:39.502', lvl: 'info', src: name,         msg: '→ 3 results (claude-code)' },
    { ts: '14:02:41.218', lvl: 'warn', src: name,         msg: 'client cursor closed transport stream' },
    { ts: '14:02:41.218', lvl: 'info', src: name,         msg: 'client cursor reconnected · session resumed' },
    { ts: '14:02:48.117', lvl: 'ok',   src: name,         msg: 'tool call: journal.tail lines=200' },
  ]
})
</script>

<template>
  <Drawer
    :open="open"
    :on-close="() => emit('close')"
    :width="680"
    :eyebrow="`MCP · ${server?.name || ''} · live tail`"
    title="Server logs"
  >
    <div class="mcp-logs" data-testid="mcp-logs-drawer">
      <div v-for="(l, i) in sample" :key="i" :class="['mcp-logs-line', l.lvl]">
        <span class="ts">{{ l.ts }}</span>
        <span class="sl">[{{ l.src }}]</span>
        <span class="lvl">{{ l.lvl }}</span>
        <span class="msg">{{ l.msg }}</span>
      </div>
    </div>

    <template #foot>
      <span class="mcp-logs-follow">
        <span class="mcp-logs-dot" />
        following tail
      </span>
      <span class="mcp-logs-actions">
        <button type="button" class="mcp-logs-btn">Open full logs →</button>
        <button type="button" class="mcp-logs-btn" @click="emit('close')">Close</button>
      </span>
    </template>
  </Drawer>
</template>

<style scoped>
.mcp-logs {
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  background: #070707;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  padding: 6px 0;
}
.mcp-logs-line {
  display: grid;
  grid-template-columns: 96px 110px 40px 1fr;
  gap: 10px;
  padding: 2px 12px;
  line-height: 1.6;
}
.mcp-logs-line .ts  { color: var(--fg-5); }
.mcp-logs-line .sl  { color: var(--accent); }
.mcp-logs-line .lvl { color: var(--fg-3); }
.mcp-logs-line.ok   .lvl { color: var(--ok); }
.mcp-logs-line.warn .lvl { color: var(--warn); }
.mcp-logs-line.err  .lvl { color: var(--err); }
.mcp-logs-line .msg { color: var(--fg-2); }

.mcp-logs-follow {
  color: var(--ok);
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.mcp-logs-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 6px var(--ok);
}
.mcp-logs-actions { display: inline-flex; gap: 8px; }
.mcp-logs-btn {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg-3);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}
.mcp-logs-btn:hover { color: var(--fg); border-color: var(--line-strong); }
</style>
