<script setup>
/**
 * MemoryBar.vue — stacked unified-memory bar with colour segments.
 *
 * Mirrors the memory-card visualisation block in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 56–69).
 *
 * `segments` is an array of `{ label, gb, cls }`; the row total is
 * `totalGb`. Each segment's width is `gb / totalGb * 100`. The "free"
 * segment is rendered as a faint trailing fill so the bar is always
 * end-anchored — matches the v0.3 design.
 */
defineProps({
  segments: { type: Array, required: true },
  totalGb: { type: Number, required: true },
  usedGb: { type: Number, required: true },
  caption: { type: String, default: '' },
})
</script>

<template>
  <div class="memory-bar-wrap">
    <div class="bar">
      <div
        v-for="seg in segments"
        :key="seg.label"
        class="bar-seg"
        :class="seg.cls"
        :style="{ width: ((seg.gb / totalGb) * 100) + '%' }"
        :title="`${seg.label}: ${seg.gb.toFixed(2)} GB`"
      />
    </div>
    <div class="bar-meta mono">
      <span>{{ caption }}</span>
      <span>{{ usedGb.toFixed(1) }} / {{ totalGb.toFixed(0) }} GB</span>
    </div>
  </div>
</template>

<style scoped>
.memory-bar-wrap {
  padding: 10px 18px;
  border-top: 1px solid var(--color-border);
}
.bar {
  display: flex;
  height: 6px;
  border-radius: 1px;
  overflow: hidden;
  background: var(--color-surface-3);
}
.bar-seg { height: 100%; transition: width 0.3s ease; }
.bar-seg.seg-primary { background: var(--hal0-accent); }
.bar-seg.seg-agent { background: rgb(200, 150, 255); }
.bar-seg.seg-embed { background: color-mix(in oklch, var(--hal0-accent) 60%, transparent); }
.bar-seg.seg-tts { background: color-mix(in oklch, var(--color-fg-muted) 55%, transparent); }
.bar-seg.seg-free { background: var(--color-surface-3); }
.bar-meta {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--color-fg-faint);
  margin-top: 6px;
}
</style>
