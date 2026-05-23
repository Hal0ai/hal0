<script setup>
/**
 * mcp/LiveTimeline.vue — 60-second oscilloscope of MCP tool calls.
 *
 * Mirrors the React `LiveTimeline` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 142–182).
 *
 * Each call is a vertical tick positioned by AGE — newer ticks on
 * the right, fading left over 60s. Ticks <4s old gain a glow box-
 * shadow + the brand accent-hover background; older ticks linearly
 * fade to ~25% opacity at the 60s edge.
 *
 * `state === 'stopped'` flips the track to the diagonal-stripe
 * pattern + dims opacity (visual: signal lost). For other non-running
 * states the parent doesn't render the timeline at all.
 *
 * Inputs:
 *   serverId — key into the calls Map.
 *   calls    — Map<serverId, Array<{ts, client, tool}>> from
 *              useLiveCallStream.
 *   now      — current tick ms (drives reactive re-render).
 *   state    — 'running' | 'stopped'.
 */
import { computed } from 'vue'

const props = defineProps({
  serverId: { type: String, required: true },
  calls:    { type: Object, required: true },
  now:      { type: Number, required: true },
  state:    { type: String, required: true },
})

const WINDOW = 60_000

const events = computed(() => {
  const arr = props.calls?.get?.(props.serverId) || []
  // mirror the design's `slice(-200)` cap so a runaway burst doesn't
  // blow the DOM.
  return arr.slice(-200)
})

function tickStyle(e) {
  const age = props.now - e.ts
  if (age > WINDOW) return { display: 'none' }
  const right = (age / WINDOW) * 100
  const opacity = 1 - (age / WINDOW) * 0.75
  return {
    right: `${right}%`,
    opacity,
  }
}

function isGlow(e) {
  return (props.now - e.ts) < 4000
}
</script>

<template>
  <div :class="['mcp-tl', state === 'running' ? 'on' : 'off']" data-testid="mcp-timeline">
    <div class="mcp-tl-track">
      <div v-for="s in [0, 15, 30, 45]" :key="s" class="mcp-tl-grid" :style="{ right: `${(s / 60) * 100}%` }" />
      <div
        v-for="(e, i) in events"
        :key="e.ts + '-' + i"
        :class="['mcp-tl-tick', { glow: isGlow(e) }]"
        :style="tickStyle(e)"
        :title="`${e.tool} via ${e.client}`"
      />
      <div class="mcp-tl-now" />
    </div>
    <div class="mcp-tl-axis mono">
      <span>−60s</span>
      <span>−45</span>
      <span>−30</span>
      <span>−15</span>
      <span class="now">now</span>
    </div>
  </div>
</template>

<style scoped>
.mcp-tl {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mcp-tl-track {
  position: relative;
  height: 28px;
  background:
    linear-gradient(90deg, rgba(255, 176, 0, 0.025), transparent 30%, transparent),
    var(--bg);
  border: 1px solid var(--line);
  border-radius: 3px;
  overflow: hidden;
}
.mcp-tl.off .mcp-tl-track {
  background: repeating-linear-gradient(45deg, var(--bg) 0 6px, transparent 6px 12px), var(--bg);
  opacity: 0.5;
}
.mcp-tl-grid {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--line-soft);
}
.mcp-tl-tick {
  position: absolute;
  top: 4px;
  bottom: 4px;
  width: 2px;
  background: var(--accent);
  border-radius: 1px;
  transition: opacity 0.5s linear;
}
.mcp-tl-tick.glow {
  box-shadow: 0 0 8px var(--accent), 0 0 16px var(--accent-glow);
  background: var(--hal0-accent-hover);
}
.mcp-tl-now {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 1px;
  background: linear-gradient(180deg, transparent, var(--accent) 30%, var(--accent) 70%, transparent);
  opacity: 0.8;
}
.mcp-tl-axis {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  color: var(--fg-5);
  letter-spacing: 0.04em;
  padding: 0 2px;
}
.mcp-tl-axis .now { color: var(--accent); }
</style>
