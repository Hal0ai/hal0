<script setup>
/**
 * components/settings/SecRow.vue — generic key/value row used by every
 * Settings section.
 *
 * Mirrors the React `SRow` helper in the design source
 * (/tmp/hal0-design/hal0-v2/project/dash/settings.jsx line 57). Three
 * columns: label + sub-label, value (mono optional), action buttons.
 *
 * Slots
 * ─────
 *   default — value column (overrides `value` prop when present)
 *   actions — action column (buttons, chips, status badges)
 *
 * Props
 * ─────
 *   k       label text
 *   sub     muted sub-label below the label
 *   value   plain-string value (slot wins if present)
 *   mono    render value in mono font
 */
defineProps({
  k:    { type: String, required: true },
  sub:  { type: String, default: '' },
  value:{ type: [String, Number], default: '' },
  mono: { type: Boolean, default: false },
})
</script>

<template>
  <div class="s-row">
    <div class="k">
      <span>{{ k }}</span>
      <span v-if="sub" class="sub">{{ sub }}</span>
    </div>
    <div :class="['v', { mono }]">
      <slot>{{ value }}</slot>
    </div>
    <div v-if="$slots.actions" class="ac"><slot name="actions" /></div>
  </div>
</template>

<style scoped>
.s-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(180px, 1.4fr) auto;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line-soft, var(--color-border));
  align-items: center;
}
.s-row:last-child { border-bottom: none; }
.k {
  font-size: 13px;
  color: var(--fg, var(--color-fg));
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.k .sub {
  font-family: var(--geist, inherit);
  font-size: 11.5px;
  color: var(--fg-4, var(--color-fg-faint));
  font-weight: 400;
}
.v {
  font-size: 12.5px;
  color: var(--fg-2, var(--color-fg-muted));
  min-width: 0;
  word-break: break-word;
}
.v.mono { font-family: var(--jbm, var(--font-mono)); }
.ac { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
@media (max-width: 720px) {
  .s-row { grid-template-columns: 1fr; }
  .ac { justify-content: flex-start; }
}
</style>
