<script setup>
/**
 * LogFilterBar.vue — filter row above the log viewport.
 *
 * Mirrors the React `LogsView` filter strip in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 291–324).
 *
 * Source / level toggles are segmented buttons; slot is a select; the
 * search input is free-text. follow-tail + pause + export buttons live
 * on the right.
 */
defineProps({
  source: { type: String, required: true },
  level: { type: String, default: '' },
  slotFilter: { type: String, default: '' },
  search: { type: String, default: '' },
  followTail: { type: Boolean, default: true },
  paused: { type: Boolean, default: false },
  slotOptions: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'update:source',
  'update:level',
  'update:slotFilter',
  'update:search',
  'toggle-pause',
  'export',
])

const SOURCE_TABS = [
  { k: 'merged', l: 'merged' },
  { k: 'hal0', l: 'hal0' },
  { k: 'lemond', l: 'lemond' },
]
const LEVEL_TABS = [
  { k: '', l: 'all' },
  { k: 'ok', l: 'ok' },
  { k: 'info', l: 'info' },
  { k: 'warn', l: 'warn' },
  { k: 'error', l: 'err' },
]
</script>

<template>
  <div class="filterbar" data-testid="log-filter-bar">
    <div class="seg mono">
      <button
        v-for="t in SOURCE_TABS"
        :key="t.k"
        type="button"
        :class="['seg-btn', { active: source === t.k }]"
        :data-testid="`log-source-${t.k}`"
        @click="emit('update:source', t.k)"
      >{{ t.l }}</button>
    </div>

    <div class="seg mono">
      <button
        v-for="t in LEVEL_TABS"
        :key="t.l"
        type="button"
        :class="['seg-btn', { active: (level || '') === t.k }]"
        :data-testid="`log-level-${t.l}`"
        @click="emit('update:level', t.k)"
      >{{ t.l }}</button>
    </div>

    <select
      class="input mono"
      :value="slotFilter"
      data-testid="log-slot-filter"
      @change="emit('update:slotFilter', $event.target.value)"
    >
      <option value="">all slots</option>
      <option v-for="s in slotOptions" :key="s" :value="s">slot: {{ s }}</option>
    </select>

    <input
      class="input mono search"
      :value="search"
      placeholder="search…"
      data-testid="log-search"
      @input="emit('update:search', $event.target.value)"
    />

    <span class="tail mono">
      <span :class="['dot', followTail ? 'dot-ok' : 'dot-off']" />
      <span>{{ followTail ? 'follow tail' : 'paused tail' }}</span>
    </span>

    <button
      class="btn-ghost sm"
      type="button"
      data-testid="log-pause"
      @click="emit('toggle-pause')"
    >{{ paused ? 'Resume' : 'Pause' }}</button>
    <button
      class="btn-ghost sm"
      type="button"
      data-testid="log-export"
      @click="emit('export')"
    >⇩ Export</button>
  </div>
</template>

<style scoped>
.filterbar {
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border);
  display: flex; align-items: center; gap: 8px;
  background: var(--hal0-bg-sunken);
  flex-wrap: wrap;
}
.seg {
  display: inline-flex;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  overflow: hidden;
  font-size: 11px;
}
.seg-btn {
  padding: 4px 11px;
  background: transparent;
  color: var(--color-fg-muted);
  border: none;
  border-right: 1px solid var(--color-border);
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 11px;
}
.seg-btn:last-child { border-right: none; }
.seg-btn.active {
  background: color-mix(in srgb, var(--hal0-accent) 14%, transparent);
  color: var(--hal0-accent);
}
.input {
  padding: 4px 8px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  height: 26px;
  font-size: 11px;
  outline: none;
}
.input.search { flex: 1; min-width: 120px; max-width: 280px; }
.tail {
  margin-left: auto;
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px;
  color: var(--color-fg-muted);
}
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: var(--color-success); box-shadow: 0 0 8px var(--color-success); }
.dot-off { background: var(--color-fg-faint); }

.btn-ghost {
  padding: 4px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
}
.btn-ghost:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
.mono { font-family: var(--font-mono); }
</style>
