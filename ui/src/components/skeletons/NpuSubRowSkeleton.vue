<script setup>
/**
 * skeletons/NpuSubRowSkeleton.vue — placeholder NPU trio sub-row
 * (slice #175).
 *
 * Mirrors NpuBlock's per-sub-row grid: dot + role + model + metrics +
 * chip. Used while the live FLM trio data is in flight.
 */
</script>

<template>
  <div class="npu-row-skel" data-testid="npu-subrow-skeleton" aria-hidden="true">
    <span class="skel skel-dot" />
    <span class="skel role" />
    <span class="skel model" />
    <span class="skel met" />
    <span class="skel skel-chip st" />
  </div>
</template>

<style scoped>
.npu-row-skel {
  display: grid;
  grid-template-columns: 14px 110px 1fr 180px 100px;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-top: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
}
.npu-row-skel .role  { height: 12px; }
.npu-row-skel .model { height: 12px; }
.npu-row-skel .met   { height: 12px; }
.npu-row-skel .st    { width: 80px; height: 18px; }
</style>
