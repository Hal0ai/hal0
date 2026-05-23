<script setup>
/**
 * LogGroup.vue — collapsible header for adjacent same-source/level/
 * request_id lines arriving within 200 ms.
 *
 * Mirrors the React `LogGroup` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 388–418).
 */
import { ref, computed } from 'vue'

const props = defineProps({
  group: { type: Object, required: true },
})

const open = ref(false)
const head = computed(() => props.group.items[0])
const rest = computed(() => props.group.items.length - 1)
</script>

<template>
  <div
    class="group-head"
    :class="{ open }"
    data-testid="log-group-head"
    @click="open = !open"
  >
    <span class="ts">{{ head.ts }}</span>
    <span class="src src-lemond">{{ head.source }}</span>
    <span class="lvl">{{ head.level }}</span>
    <span class="slot">{{ head.slot || '—' }}</span>
    <span class="msg">
      <span class="caret" data-testid="log-group-caret">{{ open ? '▾' : '▸' }}</span>
      <b>{{ head.msg }}</b>
      <span class="meta">+ {{ rest }} more · request {{ group.id }}</span>
    </span>
  </div>
  <template v-if="open">
    <div
      v-for="(ln, i) in group.items.slice(1)"
      :key="i"
      class="group-child"
      data-testid="log-group-child"
    >
      <span class="ts">{{ ln.ts }}</span>
      <span class="src src-lemond">{{ ln.source }}</span>
      <span class="lvl">{{ ln.level }}</span>
      <span class="slot">{{ ln.slot || '—' }}</span>
      <span class="msg">{{ ln.msg }}</span>
    </div>
  </template>
</template>

<style scoped>
.group-head, .group-child {
  display: grid;
  gap: 12px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.6;
}
.group-head {
  padding: 2px 16px;
  grid-template-columns: 100px 78px 60px 80px 1fr;
  border-left: 2px solid var(--color-warning);
  cursor: pointer;
}
.group-head.open {
  background: color-mix(in srgb, var(--color-warning) 8%, transparent);
}
.group-child {
  padding: 2px 16px 2px 32px;
  grid-template-columns: 84px 78px 60px 80px 1fr;
  border-left: 2px solid color-mix(in srgb, var(--color-warning) 50%, transparent);
  background: color-mix(in srgb, var(--color-warning) 4%, transparent);
  color: var(--color-fg-muted);
}
.ts { color: var(--color-fg-faint); }
.src-lemond { color: color-mix(in oklch, var(--hal0-accent) 70%, var(--color-fg-muted)); }
.lvl { color: var(--color-warning); }
.slot { color: var(--color-fg-muted); }
.msg {
  color: var(--color-fg-muted);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.msg b { color: var(--color-fg); font-weight: 500; }
.meta { color: var(--color-fg-faint); font-size: 10px; margin-left: 4px; }
.caret { display: inline-block; width: 12px; }
</style>
