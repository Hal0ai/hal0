<script setup>
/**
 * skeletons/ModelRowSkeleton.vue — placeholder row for the Models
 * left-pane list (slice #175).
 *
 * Mirrors ModelList's row layout: icon + name + size chip + state chip.
 */
</script>

<template>
  <div class="mr-skel" data-testid="model-row-skeleton" aria-hidden="true">
    <span class="skel skel-dot" />
    <div class="lines">
      <span class="skel skel-line med" />
      <span class="skel skel-line short" />
    </div>
    <span class="skel skel-chip" />
  </div>
</template>

<style scoped>
.mr-skel {
  display: grid;
  grid-template-columns: 14px 1fr 56px;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
}
.mr-skel .lines {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.mr-skel .skel-line { height: 11px; }
.mr-skel .skel-chip { height: 16px; }
</style>
