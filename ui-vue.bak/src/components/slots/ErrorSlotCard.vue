<script setup>
/**
 * ErrorSlotCard.vue — persistent inline error banner appended to a
 * SlotCard when the latest load attempt failed.
 *
 * Mirrors slot-modals.jsx::ErrorSlotCardBanner (lines 393-407). Sits
 * BELOW the regular SlotCard rather than replacing it — the card
 * still renders the slot's last-known metrics so the operator can see
 * what was running before the failure.
 */
defineProps({
  slotName: { type: String, required: true },
  message:  { type: String, required: true },
})

defineEmits(['retry', 're-pull'])
</script>

<template>
  <div class="err-row" data-testid="error-slot-banner" role="alert">
    <span class="warn-ic">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 2l6 11H2L8 2z"/>
        <path d="M8 7v3M8 12v0.01"/>
      </svg>
    </span>
    <div class="content">
      <div class="heading">load failed</div>
      <div class="body">{{ message }}</div>
      <div class="actions">
        <button class="ghost" type="button" @click="$emit('retry')">Retry</button>
        <button class="ghost" type="button" @click="$emit('re-pull')">Re-pull</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.err-row {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 10px 12px;
  background: color-mix(in oklch, var(--color-danger), transparent 88%);
  border: 1px solid color-mix(in oklch, var(--color-danger), transparent 60%);
  border-radius: var(--radius-sm);
}
.warn-ic { color: var(--color-danger); display: inline-flex; flex-shrink: 0; padding-top: 1px; }
.content { flex: 1; font-family: var(--font-mono); font-size: 11.5px; line-height: 1.5; color: var(--color-fg-muted); }
.heading { color: var(--color-danger); font-weight: 500; margin-bottom: 2px; }
.actions { display: flex; gap: 6px; margin-top: 6px; }
.ghost {
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 3px 8px; border-radius: var(--radius-sm);
  font-family: var(--font-mono); font-size: 10.5px; cursor: pointer;
}
.ghost:hover { color: var(--color-fg); border-color: var(--color-border-hi); }
</style>
