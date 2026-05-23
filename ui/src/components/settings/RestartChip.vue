<script setup>
/**
 * components/settings/RestartChip.vue — `⟳ restart` marker shown next to
 * Lemonade-admin fields whose change requires a lemond restart to take
 * effect.
 *
 * Kept as its own component because it appears in three places:
 *   - SecKey row action column
 *   - section footer "N fields require restart" rollup
 *   - the SaveAndRestartDialog summary
 */
defineProps({
  label:   { type: String, default: 'restart' },
  variant: { type: String, default: 'inline' }, // 'inline' | 'pill'
})
</script>

<template>
  <span :class="['restart-chip', `variant-${variant}`]" :title="`Requires lemond restart`">
    ⟳ {{ label }}
  </span>
</template>

<style scoped>
.restart-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--warn, var(--color-warning));
  border: 1px solid color-mix(in srgb, var(--warn, var(--color-warning)) 35%, transparent);
  background: color-mix(in srgb, var(--warn, var(--color-warning)) 12%, transparent);
  white-space: nowrap;
}
.variant-pill { padding: 3px 9px; font-size: 11px; }
</style>
