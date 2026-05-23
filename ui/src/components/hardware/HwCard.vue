<script setup>
/**
 * HwCard.vue — panel-shell for a hardware section.
 *
 * Mirrors the React `HwCard` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 82–96).
 *
 * Props
 * ─────
 *   title    — section heading (mono, 16px).
 *   eyebrow  — uppercased small label above the title (slot kind label).
 *   full     — true to span the grid full-width (GPU/NPU/Memory/Storage).
 *   purple   — NPU-only purple border + eyebrow tint.
 */
const props = defineProps({
  title: { type: String, required: true },
  eyebrow: { type: String, default: '' },
  full: { type: Boolean, default: false },
  purple: { type: Boolean, default: false },
})
</script>

<template>
  <div
    class="hw-card"
    :class="{ 'hw-card-full': full, 'hw-card-purple': purple }"
    data-testid="hw-card"
  >
    <div class="hw-card-head">
      <div v-if="eyebrow" class="hw-card-eye mono">{{ eyebrow }}</div>
      <div class="hw-card-title mono">{{ title }}</div>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.hw-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  grid-column: auto;
}
.hw-card-full { grid-column: 1 / -1; }
.hw-card-purple { border-color: rgba(200, 150, 255, 0.25); }
.hw-card-head {
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
  background: var(--hal0-bg-sunken);
}
.hw-card-eye {
  font-size: 10px;
  color: var(--hal0-accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 4px;
}
.hw-card-purple .hw-card-eye {
  color: rgb(200, 150, 255);
}
.hw-card-title {
  font-size: 16px;
  font-weight: 500;
  letter-spacing: -0.02em;
  color: var(--color-fg);
}
</style>
