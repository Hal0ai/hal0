<script setup>
defineProps({
  /** Add extra padding inside */
  padded: { type: Boolean, default: true },
  /** Visually highlight (e.g. active state) — subtle 2px amber underline along the bottom edge */
  highlight: { type: Boolean, default: false },
})
</script>

<template>
  <div
    class="card"
    :class="{
      'card-padded': padded,
      'card-highlight': highlight,
    }"
  >
    <slot />
  </div>
</template>

<style scoped>
.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.card-padded {
  padding: 16px;
}

.card-highlight {
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
  box-shadow: inset 0 -2px 0 var(--hal0-accent);
}
</style>
