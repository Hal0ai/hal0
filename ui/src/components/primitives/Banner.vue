<script setup>
/**
 * primitives/Banner.vue — reusable warn/err/info banner shell.
 *
 * Mirrors the React `Banner` in
 *   /tmp/hal0-design-v3/dash/primitives.jsx (lines 123–142).
 *
 * Anatomy
 * ───────
 *   [icon] [eyebrow / heading / body / actions]   [× dismiss]
 *
 * Kind drives both the soft-tinted background + border + icon-tile
 * colour (warn=amber, err=red, info=accent).
 *
 * `actions` is an array of `{ label, primary?, onClick? }`. Buttons
 * render via the global `.btn` style; primary fills with accent,
 * non-primary uses the ghost variant. When `onClick` is missing the
 * banner renders the button as a no-op stub — wiring decisions live
 * in `BannerStack.vue` (which can fall back to a toast).
 */
defineProps({
  kind:        { type: String, default: 'warn' },          // warn | err | info
  heading:     { type: String, default: '' },
  eyebrow:     { type: String, default: '' },
  body:        { type: String, default: '' },
  actions:     { type: Array, default: () => [] },
  onDismiss:   { type: Function, default: null },
  dismissable: { type: Boolean, default: true },
})

const emit = defineEmits(['action'])

function fireAction(a) {
  if (a.onClick) { a.onClick(); return }
  // Banner is NOT store-aware (per slice brief). Emit an event so a
  // host (BannerStack / consumer view) can toast-or-no-op.
  emit('action', a)
}
</script>

<template>
  <div :class="['banner', `banner-${kind}`]" :role="kind === 'err' ? 'alert' : 'status'">
    <div class="banner-ic">
      <!-- bell for info, warn-triangle for warn/err — matches Icons.bell/Icons.warn -->
      <svg
        v-if="kind === 'info'"
        width="16" height="16" viewBox="0 0 16 16"
        fill="none" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" stroke-linejoin="round"
      >
        <path d="M4 11h8c-1 0-1.5-0.5-1.5-2V6.5a2.5 2.5 0 0 0-5 0V9c0 1.5-0.5 2-1.5 2zM6.5 13a1.5 1.5 0 0 0 3 0"/>
      </svg>
      <svg
        v-else
        width="16" height="16" viewBox="0 0 16 16"
        fill="none" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" stroke-linejoin="round"
      >
        <path d="M8 2l6 11H2L8 2z"/>
        <path d="M8 7v3M8 12v0.01"/>
      </svg>
    </div>
    <div class="banner-content">
      <div v-if="eyebrow" class="banner-eye mono">{{ eyebrow }}</div>
      <div v-if="heading" class="banner-heading mono">{{ heading }}</div>
      <div v-if="body" class="banner-body">
        <slot name="body">{{ body }}</slot>
      </div>
      <div v-if="actions && actions.length" class="banner-actions">
        <button
          v-for="(a, i) in actions"
          :key="i"
          type="button"
          :class="a.primary ? 'btn sm' : 'btn ghost sm'"
          @click="fireAction(a)"
        >{{ a.label }}</button>
      </div>
    </div>
    <button
      v-if="onDismiss && dismissable"
      type="button"
      class="banner-dismiss"
      aria-label="Dismiss"
      @click="onDismiss"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 4l8 8M12 4l-8 8"/>
      </svg>
    </button>
  </div>
</template>

<style scoped>
.banner {
  display: grid;
  grid-template-columns: 28px 1fr auto;
  gap: 14px;
  padding: 12px 14px;
  border-radius: var(--rad);
  border: 1px solid;
  align-items: start;
  position: relative;
}
.banner-warn {
  background: color-mix(in oklab, var(--warn-soft) 110%, transparent);
  border-color: var(--warn-line);
  color: var(--fg);
}
.banner-err {
  background: color-mix(in oklab, var(--err-soft) 130%, transparent);
  border-color: var(--err-line);
  color: var(--fg);
}
.banner-info {
  background: var(--accent-soft);
  border-color: var(--accent-line);
  color: var(--fg);
}
.banner-ic {
  width: 28px; height: 28px;
  border-radius: 4px;
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.banner-warn .banner-ic { background: rgba(232, 185, 78, 0.18); color: var(--warn); }
.banner-err  .banner-ic { background: rgba(239, 107, 107, 0.18); color: var(--err); }
.banner-info .banner-ic { background: var(--accent-bg); color: var(--accent); }

.banner-content { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.banner-eye {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: currentColor;
  opacity: 0.7;
}
.banner-warn .banner-eye { color: var(--warn); opacity: 1; }
.banner-err  .banner-eye { color: var(--err);  opacity: 1; }
.banner-info .banner-eye { color: var(--accent); opacity: 1; }
.banner-heading {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--fg);
  line-height: 1.35;
  letter-spacing: -0.005em;
}
.banner-body {
  font-size: 12.5px;
  color: var(--fg-2);
  line-height: 1.5;
  text-wrap: pretty;
}
.banner-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.banner-dismiss {
  background: transparent;
  border: none;
  color: var(--fg-4);
  width: 24px; height: 24px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 4px;
  cursor: pointer;
}
.banner-dismiss:hover { color: var(--fg); background: rgba(255, 255, 255, 0.05); }
</style>
