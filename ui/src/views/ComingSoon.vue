<script setup>
/**
 * ComingSoon.vue — placeholder for v0.3 dashboard surfaces that the
 * sidebar advertises before their owning slice has landed. Slice #168
 * registers /agents/mcp and /agents/memory so the sidebar links don't
 * 404; slice #14 will swap MCP Servers for the real surface, Phase 9
 * (memory) lands later.
 */
import { useRoute } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()
const title = computed(() => route.meta?.title || 'Coming soon')
const detail = computed(() => route.meta?.detail || 'This surface ships in a later slice.')
</script>

<template>
  <section class="view coming-soon" data-testid="coming-soon-view">
    <h1>{{ title }}</h1>
    <p>{{ detail }}</p>
  </section>
</template>

<style scoped>
.coming-soon {
  padding: 64px 32px;
  text-align: center;
  color: var(--color-fg-muted);
}
.coming-soon h1 {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 500;
  margin-bottom: 8px;
  color: var(--color-fg);
}
.coming-soon p {
  font-size: 13px;
  color: var(--color-fg-faint);
}
</style>
