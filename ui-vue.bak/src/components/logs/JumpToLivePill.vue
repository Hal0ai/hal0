<script setup>
/**
 * JumpToLivePill.vue — floating "↓ Jump to live" pill.
 *
 * Mirrors the floating button in the React `LogsView` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 343–367).
 *
 * Surfaces a "+N new" badge when buffered lines accumulate while the
 * user is scrolled up; clicking jumps to bottom + resumes follow.
 */
defineProps({
  pendingCount: { type: Number, default: 0 },
})
defineEmits(['jump'])
</script>

<template>
  <button
    class="pill"
    type="button"
    data-testid="jump-to-live"
    @click="$emit('jump')"
  >
    ↓ Jump to live
    <span v-if="pendingCount > 0" class="badge mono" data-testid="jump-to-live-badge">
      +{{ pendingCount }}
    </span>
  </button>
</template>

<style scoped>
.pill {
  position: absolute;
  right: 20px;
  bottom: 20px;
  background: var(--hal0-accent);
  color: #0a0a0a;
  border: 1px solid var(--hal0-accent);
  border-radius: 999px;
  padding: 8px 14px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 8px 24px -4px rgba(0,0,0,0.5);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  z-index: 10;
}
.badge {
  background: #0a0a0a;
  color: var(--hal0-accent);
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
}
.mono { font-family: var(--font-mono); }
</style>
