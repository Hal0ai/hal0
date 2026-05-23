<script setup>
defineProps({
  lines: { type: Number, default: 3 },
  height: { type: String, default: '14px' },
})
</script>

<template>
  <div class="skeleton-wrap" aria-busy="true" aria-label="Loading…">
    <div
      v-for="i in lines"
      :key="i"
      class="skeleton-line"
      :style="{ height, width: i === lines ? '60%' : '100%' }"
    />
  </div>
</template>

<style scoped>
.skeleton-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skeleton-line {
  border-radius: var(--radius);
  background: linear-gradient(
    90deg,
    var(--color-surface-2) 25%,
    var(--color-surface-3) 50%,
    var(--color-surface-2) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
}

@keyframes shimmer {
  from { background-position: 200% 0; }
  to   { background-position: -200% 0; }
}
</style>
