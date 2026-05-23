<script setup>
/**
 * skeletons/SlotCardSkeleton.vue — placeholder for SlotCard (slice #175).
 *
 * Matches SlotCard's footprint so the layout doesn't jump when the
 * real card mounts: a dot + slot-name row, a model-id strip, and a
 * metric chip row underneath. Background uses the shared `.skel`
 * shimmer rule from style.css.
 */
</script>

<template>
  <div class="slot-skel" data-testid="slot-card-skeleton" aria-hidden="true">
    <div class="row hdr">
      <span class="skel skel-dot" />
      <span class="skel skel-line med name" />
      <span class="skel skel-chip kind" />
    </div>
    <div class="row mid">
      <span class="skel skel-line short" />
    </div>
    <div class="row metrics">
      <span class="skel skel-chip" />
      <span class="skel skel-chip" />
      <span class="skel skel-chip" />
    </div>
  </div>
</template>

<style scoped>
.slot-skel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 14px 12px;
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 6px;
  background: var(--color-surface, var(--bg, #0a0a0a));
  min-height: 116px;
}
.row { display: flex; align-items: center; gap: 10px; }
.row.hdr .name { flex: 1; height: 14px; }
.row.hdr .kind { width: 80px; }
.row.metrics { gap: 6px; }
.row.metrics .skel-chip { width: 56px; }
</style>
