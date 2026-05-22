<script setup>
/**
 * AgentPendingChip.vue — tiny inline chip rendered next to a row whose
 * state has a pending approval queued against it.
 *
 * Per ADR-0004 §5: inline indicators are context-rich nudges, the
 * header bell is canonical. The chip is a link to /agent?tab=inbox so
 * the operator can jump directly to the inbox tab where the decision
 * lives.
 *
 * Usage:
 *
 *   import AgentPendingChip from '.../agent/AgentPendingChip.vue'
 *   const { pendingForResource } = useAgentStore()
 *   const pendings = computed(() => pendingForResource('model', model.id))
 *
 *   <AgentPendingChip v-for="p in pendings" :key="p.id" :entry="p" />
 */
defineProps({
  entry: { type: Object, required: true },
})
</script>

<template>
  <router-link
    :to="{ path: '/agent', query: { tab: 'inbox' } }"
    class="pending-chip"
    :title="`Approve or deny in the agent inbox · ${entry.tool}`"
  >
    <span class="dot" aria-hidden="true" />
    <span class="lbl mono">pending: {{ entry.tool }}</span>
  </router-link>
</template>

<style scoped>
.pending-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px;
  border-radius: 999px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  background: color-mix(in srgb, var(--hal0-accent) 12%, transparent);
  color: var(--hal0-accent);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent);
  text-decoration: none;
  font-feature-settings: 'zero' 1, 'ss02' 1;
}
.pending-chip:hover {
  background: color-mix(in srgb, var(--hal0-accent) 22%, transparent);
}
.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 6px var(--hal0-accent);
}
.lbl { line-height: 1.4; }
</style>
