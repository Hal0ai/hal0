<script setup>
/**
 * EmptySlotCard.vue — placeholder card for a seeded-but-unconfigured slot.
 *
 * Used by the skip-path 6-card grid (slot rendered before the user picks
 * a model). The Configure button opens the Create-slot modal pre-filled
 * with {name, type, group, device} so the operator only has to pick a
 * model + confirm.
 *
 * Mirrors slot-modals.jsx::EmptySlotCard (lines 369-390).
 */
defineProps({
  name:   { type: String, required: true },
  type:   { type: String, required: true },
  group:  { type: String, default: 'custom' },
  device: { type: String, default: 'gpu-vulkan' },
})

defineEmits(['configure'])
</script>

<template>
  <div class="slot empty" data-testid="empty-slot-card">
    <div class="slot-h">
      <span class="dot empty" />
      <div class="slot-name"><span class="nm">{{ name }}</span></div>
    </div>
    <div class="no-model mono">no model loaded</div>
    <div class="slot-chips">
      <span class="chip">{{ type }}</span>
      <span :class="['chip', 'dev-' + device.replace('gpu-', '')]">{{ device }}</span>
      <span class="chip">{{ group }}</span>
    </div>
    <div class="seeded-row">
      <span class="seeded mono">seeded · ready to configure</span>
      <button class="btn-primary" type="button" @click="$emit('configure')">
        <svg width="10" height="10" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
        </svg>
        Configure
      </button>
    </div>
  </div>
</template>

<style scoped>
.slot {
  background: var(--color-surface);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px 16px 14px;
  display: flex; flex-direction: column; gap: 12px;
}
.slot-h { display: flex; align-items: center; gap: 8px; }
.slot-name { display: flex; align-items: center; gap: 8px; font-family: var(--font-mono); font-size: 14.5px; }
.slot-name .nm { color: var(--color-fg-faint); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.empty { background: var(--color-fg-faint); opacity: 0.55; }
.no-model {
  padding: 8px 10px;
  background: var(--color-surface-2);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-fg-faint);
  font-style: italic;
}
.slot-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip {
  font-family: var(--font-mono); font-size: 10px;
  padding: 2px 6px; border-radius: var(--radius-sm);
  background: var(--color-surface-2); color: var(--color-fg-muted);
  border: 1px solid var(--color-border); letter-spacing: 0.04em;
}
.seeded-row {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--hal0-accent) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
  border-radius: var(--radius-sm);
}
.seeded {
  flex: 1; font-size: 11px;
  color: var(--hal0-accent);
}
.btn-primary {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 11px; border-radius: var(--radius-sm);
  background: var(--hal0-accent); color: #000;
  font-family: var(--font-mono); font-size: 11px; font-weight: 500;
  border: none; cursor: pointer;
}
.btn-primary:hover { background: var(--hal0-accent-hover); }
</style>
