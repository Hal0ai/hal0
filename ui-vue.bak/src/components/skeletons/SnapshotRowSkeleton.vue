<script setup>
/**
 * skeletons/SnapshotRowSkeleton.vue — single horizontal placeholder
 * row for the SnapshotStrip on the Dashboard / view (slice #175).
 *
 * Width is full-bleed inside the strip; each row is dot + name +
 * chips. Background uses the shared `.skel` shimmer rule.
 */
</script>

<template>
  <div class="snap-skel" data-testid="snapshot-row-skeleton" aria-hidden="true">
    <span class="skel skel-dot" />
    <span class="skel skel-line med name" />
    <span class="skel skel-chip" />
    <span class="skel skel-chip" />
    <span class="skel skel-chip" />
  </div>
</template>

<style scoped>
.snap-skel {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
}
.snap-skel .name { flex: 1; height: 12px; max-width: 220px; }
.snap-skel .skel-chip { width: 48px; height: 16px; }
</style>
