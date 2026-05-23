<script setup>
/**
 * components/settings/SettingsRail.vue — left-rail anchor list.
 *
 * Slice #173 v2 Settings layout. Sticky, vertical list of section
 * labels. Click → smooth-scroll to anchor + sync the active state.
 * Scroll-spy uses IntersectionObserver against `[data-section]`
 * elements rendered by the parent view, so the active row tracks the
 * actually-visible section as the operator scrolls.
 *
 * Props
 * ─────
 *   sections: [{ id: string, label: string }]
 *   activeId: current section id (parent owns the source of truth)
 *
 * Emits
 * ─────
 *   navigate(id) — user click; parent scrolls + sets activeId.
 *
 * Scroll-spy is driven from the parent (one observer for the whole
 * content column) rather than re-implementing it here. Keeping the rail
 * dumb makes Playwright assertions trivial and avoids the
 * observer-races-route-mount class of bug.
 */
const props = defineProps({
  sections: { type: Array, required: true },
  activeId: { type: String, default: '' },
})

const emit = defineEmits(['navigate'])

function click(id) {
  emit('navigate', id)
}
</script>

<template>
  <div class="settings-nav" data-testid="settings-rail">
    <div
      v-for="s in sections"
      :key="s.id"
      :class="['nav-item', 'mono', { active: activeId === s.id }]"
      :data-section-link="s.id"
      role="button"
      tabindex="0"
      @click="click(s.id)"
      @keydown.enter.prevent="click(s.id)"
      @keydown.space.prevent="click(s.id)"
    >
      {{ s.label }}
    </div>
  </div>
</template>

<style scoped>
.settings-nav {
  position: sticky;
  top: 16px;
  align-self: flex-start;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 168px;
}
.nav-item {
  font-size: 12px;
  color: var(--fg-3, var(--color-fg-muted));
  padding: 7px 12px;
  border-radius: var(--rad-sm, 4px);
  cursor: pointer;
  letter-spacing: 0.02em;
  user-select: none;
  outline: none;
}
.nav-item:hover {
  color: var(--fg, var(--color-fg));
  background: var(--bg-2, var(--color-surface-2));
}
.nav-item:focus-visible {
  box-shadow: 0 0 0 2px var(--accent, var(--hal0-accent));
}
.nav-item.active {
  color: var(--accent, var(--hal0-accent));
  background: var(--accent-soft, color-mix(in srgb, var(--hal0-accent) 14%, transparent));
}
@media (max-width: 880px) {
  .settings-nav {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
    overflow-x: auto;
  }
}
</style>
