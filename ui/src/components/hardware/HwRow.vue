<script setup>
/**
 * HwRow.vue — single key/value row inside an HwCard.
 *
 * Mirrors the React `HwRow` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 98–108).
 *
 * The value slot is intentionally flexible — chips, dot+text, plain
 * strings, etc. all render correctly because the row's right column
 * just slots whatever you pass.
 */
const props = defineProps({
  k: { type: String, required: true },
  mono: { type: Boolean, default: false },
  sub: { type: String, default: '' },
})
</script>

<template>
  <div class="hw-row">
    <div class="hw-key">
      {{ k }}
      <div v-if="sub" class="hw-key-sub">{{ sub }}</div>
    </div>
    <div class="hw-val" :class="{ mono }">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.hw-row {
  padding: 10px 18px;
  border-bottom: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: 180px 1fr;
  gap: 14px;
  align-items: baseline;
}
.hw-row:last-child { border-bottom: none; }
.hw-key {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  text-transform: lowercase;
  letter-spacing: 0.02em;
}
.hw-key-sub {
  color: var(--color-fg-faint);
  opacity: 0.7;
  font-size: 10px;
  margin-top: 2px;
}
.hw-val {
  font-size: 12.5px;
  color: var(--color-fg);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.hw-val.mono { font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
</style>
