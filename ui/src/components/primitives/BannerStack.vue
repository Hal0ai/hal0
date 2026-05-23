<script setup>
/**
 * primitives/BannerStack.vue — view-scoped banner renderer.
 *
 * Reads `useBannerStore.activeByScope(scope)` and renders one
 * `<Banner>` per match. Mirrors the React `BannerStack` in
 *   /tmp/hal0-design-v3/dash/primitives.jsx (lines 161–195).
 *
 * Includes the "global" scope automatically so a `<BannerStack
 * scope="dashboard">` shows both global + dashboard banners
 * (the design source filters `scope === "global" || scope === scope`).
 *
 * Action wiring
 * ─────────────
 *   - When the catalog entry's action has `onClick`, that's fired.
 *   - When missing, falls back to `useToastStore.push("<label> — stubbed", "info")`
 *     (matches the design's `window.__hal0Toast` fallback).
 *
 * Dismiss wiring
 * ──────────────
 *   - When `dismissable !== false`, the × calls `banner.dismiss(id)`.
 *   - When `dismissable === false` (e.g. npu-swap), × is hidden.
 */
import { computed } from 'vue'
import Banner from './Banner.vue'
import { useBannerStore } from '../../stores/banner.js'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  scope: { type: String, default: 'global' },
})

const banners = useBannerStore()
const toasts  = useToastStore()

const items = computed(() => {
  // Always include "global" alongside the requested scope, unless
  // scope IS "global" (avoid duplicates).
  const all = banners.activeList
  if (props.scope === 'global') return all.filter((b) => b.scope === 'global')
  return all.filter((b) => b.scope === props.scope || b.scope === 'global')
})

function handleAction(entry, action) {
  if (action.onClick) { action.onClick(); return }
  toasts.push(`${action.label} — stubbed`, 'info')
}

function handleDismiss(entry) {
  banners.dismiss(entry.id)
}
</script>

<template>
  <div v-if="items.length" class="banner-stack" data-testid="banner-stack">
    <Banner
      v-for="b in items"
      :key="b.id"
      :kind="b.kind"
      :eyebrow="b.eyebrow"
      :heading="b.heading"
      :body="b.body"
      :actions="b.actions || []"
      :dismissable="b.dismissable !== false"
      :on-dismiss="b.dismissable !== false ? () => handleDismiss(b) : null"
      @action="(a) => handleAction(b, a)"
      :data-banner-id="b.id"
    />
  </div>
</template>

<style scoped>
.banner-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}
</style>
