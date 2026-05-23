<script setup>
/**
 * LogLine.vue — single log row.
 *
 * Mirrors the React `LogLine` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 373–386).
 *
 * Search-match highlighting is rendered inline by splitting the message
 * around the match index.
 */
import { computed } from 'vue'

const props = defineProps({
  entry: { type: Object, required: true },
  search: { type: String, default: '' },
})

const parts = computed(() => {
  const msg = props.entry.msg || props.entry.line || ''
  if (!props.search) return [{ t: msg, hit: false }]
  const q = props.search.toLowerCase()
  const lower = msg.toLowerCase()
  const i = lower.indexOf(q)
  if (i < 0) return [{ t: msg, hit: false }]
  return [
    { t: msg.slice(0, i), hit: false },
    { t: msg.slice(i, i + props.search.length), hit: true },
    { t: msg.slice(i + props.search.length), hit: false },
  ]
})
</script>

<template>
  <div
    class="log-line"
    :class="{
      warn: entry.level === 'warn',
      err: entry.level === 'error',
    }"
    data-testid="log-line"
  >
    <span class="ts">{{ entry.ts }}</span>
    <span
      class="src"
      :class="entry.source === 'lemond' ? 'src-lemond' : 'src-hal0'"
    >{{ entry.source }}</span>
    <span
      class="lvl"
      :class="`lvl-${entry.level}`"
    >{{ entry.level }}</span>
    <span class="slot">{{ entry.slot || '—' }}</span>
    <span class="msg">
      <template v-for="(p, i) in parts" :key="i">
        <mark v-if="p.hit" class="hit">{{ p.t }}</mark>
        <template v-else>{{ p.t }}</template>
      </template>
    </span>
  </div>
</template>

<style scoped>
.log-line {
  padding: 2px 16px;
  display: grid;
  grid-template-columns: 100px 78px 60px 80px 1fr;
  gap: 12px;
  border-left: 2px solid transparent;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.6;
}
.log-line.warn { border-left-color: var(--color-warning); }
.log-line.err { border-left-color: var(--color-danger); }
.ts { color: var(--color-fg-faint); }
.src-hal0 { color: var(--hal0-accent); }
.src-lemond { color: color-mix(in oklch, var(--hal0-accent) 70%, var(--color-fg-muted)); }
.lvl-ok { color: var(--color-success); }
.lvl-warn { color: var(--color-warning); }
.lvl-error { color: var(--color-danger); }
.lvl-info { color: var(--color-fg-muted); }
.slot { color: var(--color-fg-muted); }
.msg { color: var(--color-fg-muted); word-break: break-word; }
.hit {
  background: color-mix(in srgb, var(--hal0-accent) 25%, transparent);
  color: var(--hal0-accent);
  padding: 0 2px;
  border-radius: 2px;
}
</style>
