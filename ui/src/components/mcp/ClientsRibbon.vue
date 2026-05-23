<script setup>
/**
 * mcp/ClientsRibbon.vue — connected-clients ribbon between KPI strip
 * and the server filter bar.
 *
 * Mirrors the React `ClientsRibbon` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 102–137).
 *
 * A client is "live" when one of its calls lands inside the last 5s.
 * Live clients get an amber dot pulse + a faint amber gradient wash
 * across the cell background (matches the React `.mcp-client.live`).
 *
 * Emits `teach` when the right-aligned link is clicked — the parent
 * view opens the ConnectClientModal.
 */
import { computed } from 'vue'

const props = defineProps({
  clients: { type: Array, required: true },
  calls:   { type: Object, required: true },
  now:     { type: Number, required: true },
})

const emit = defineEmits(['teach'])

function liveCallsFor(clientId) {
  let n = 0
  for (const arr of props.calls.values()) {
    for (const c of arr) {
      if (c.client === clientId && (props.now - c.ts) < 5000) n++
    }
  }
  return n
}

const enriched = computed(() =>
  props.clients.map((c) => ({ ...c, _live: liveCallsFor(c.id) > 0 })),
)
</script>

<template>
  <div class="mcp-clients" data-testid="mcp-clients-ribbon">
    <div class="mcp-clients-h">
      <span class="mono">Connected clients<span class="ct">· {{ clients.length }}</span></span>
      <span class="spacer" />
      <button type="button" class="mcp-link mono" data-testid="mcp-teach-link" @click="$emit('teach')">
        How do I point a new client at this host?  →
      </button>
    </div>
    <div class="mcp-clients-row">
      <div
        v-for="c in enriched"
        :key="c.id"
        :class="['mcp-client', { live: c._live }]"
        :data-testid="`mcp-client-${c.id}`"
      >
        <div class="mcp-client-h">
          <span :class="['mcp-client-dot', { pulsing: c._live }]" />
          <span class="mcp-client-name mono">{{ c.name }}</span>
          <span class="mcp-client-role mono">{{ c.role }}</span>
        </div>
        <div class="mcp-client-meta mono">
          <span class="k">host</span><span class="v">{{ c.host }}</span>
          <span class="dvd">·</span>
          <span class="k">since</span><span class="v">{{ c.since }}</span>
        </div>
        <div class="mcp-client-servers">
          <span v-for="sid in c.servers" :key="sid" class="mcp-client-server mono">{{ sid }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes mcp-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(1.35); opacity: 0.75; }
}

.spacer { flex: 1; }

.mcp-clients {
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  background: var(--bg-1);
  margin-bottom: 16px;
  overflow: hidden;
}
.mcp-clients-h {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line-soft);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-3);
}
.mcp-clients-h .ct { color: var(--fg-5); margin-left: 6px; }
.mcp-link {
  background: none;
  border: none;
  color: var(--accent);
  font-family: var(--hal0-font-mono);
  cursor: pointer;
  font-size: 11px;
  text-transform: none;
  letter-spacing: 0;
}
.mcp-link:hover { text-decoration: underline; }

.mcp-clients-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 0;
}
.mcp-client {
  padding: 14px 18px;
  border-right: 1px solid var(--line-soft);
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--bg-1);
  transition: background 0.15s ease;
}
.mcp-client:last-child { border-right: none; }
.mcp-client.live { background: linear-gradient(90deg, rgba(255, 176, 0, 0.04), transparent); }

.mcp-client-h {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mcp-client-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 8px var(--ok);
}
.mcp-client-dot.pulsing {
  background: var(--accent);
  box-shadow: 0 0 10px var(--accent);
  animation: mcp-pulse 1.0s ease-in-out infinite;
}
.mcp-client-name {
  font-family: var(--hal0-font-mono);
  font-size: 13.5px;
  color: var(--fg);
  font-weight: 500;
}
.mcp-client-role {
  font-family: var(--hal0-font-mono);
  font-size: 9.5px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-4);
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 2px;
}
.mcp-client-meta {
  font-size: 11px;
  color: var(--fg-3);
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: wrap;
}
.mcp-client-meta .k { color: var(--fg-5); }
.mcp-client-meta .v { color: var(--fg-2); }
.mcp-client-meta .dvd { color: var(--fg-5); margin: 0 4px; }
.mcp-client-servers {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.mcp-client-server {
  font-size: 10.5px;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--fg-3);
  background: var(--bg-2);
}
</style>
