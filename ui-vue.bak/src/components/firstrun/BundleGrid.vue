<script setup>
/**
 * BundleGrid — 4-up tier card grid (default firstrun layout).
 *
 * Mirrors `<BundleGrid>` in
 *   /tmp/hal0-design/hal0-v2/project/dash/firstrun.jsx (lines 57-102).
 */
import TierCard from './TierCard.vue'

defineProps({
  /** Array of bundles enriched with `_state` and `_fits` flags. */
  bundles: { type: Array, required: true },
})

const emit = defineEmits(['pick'])
function onPick(id) { emit('pick', id) }
</script>

<template>
  <div class="tiers" data-firstrun-layout="grid">
    <TierCard
      v-for="b in bundles"
      :key="b.id"
      :bundle="b"
      :state="b._state"
      @pick="onPick"
    />
  </div>
</template>

<style scoped>
.tiers {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 40px;
}
@media (max-width: 1100px) { .tiers { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 640px)  { .tiers { grid-template-columns: 1fr; } }
</style>
