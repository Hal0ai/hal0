<script setup>
/**
 * primitives/Toast.vue — single toast pill (presentational).
 *
 * ToastStack instantiates one of these per queued toast. We keep it
 * separate so a view can render an ad-hoc toast (rare) without the
 * store, and so the visual classes are colocated with the markup.
 *
 * `kind`: 'info' | 'success' | 'warning' | 'error'.
 * The amber/red/info tone mapping follows the design's compact toast
 * style (smaller than the v1 ToastContainer's stack).
 */
defineProps({
  msg:  { type: String, required: true },
  kind: { type: String, default: 'info' },
  onDismiss: { type: Function, default: null },
})
</script>

<template>
  <div :class="['hal0-toast', `hal0-toast-${kind}`]" role="status">
    <span class="hal0-toast-msg">{{ msg }}</span>
    <button
      v-if="onDismiss"
      type="button"
      class="hal0-toast-x"
      aria-label="Dismiss"
      @click="onDismiss"
    >
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 4l8 8M12 4l-8 8"/>
      </svg>
    </button>
  </div>
</template>

<style scoped>
.hal0-toast {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--rad);
  border: 1px solid;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg);
  background: var(--bg-1);
  box-shadow: 0 12px 32px -8px rgba(0, 0, 0, 0.55);
  pointer-events: auto;
  max-width: 360px;
}
.hal0-toast-info {
  border-color: var(--accent-line);
  background: color-mix(in oklab, var(--accent-soft) 110%, var(--bg-1));
}
.hal0-toast-success {
  border-color: rgba(106, 196, 130, 0.45);
  background: color-mix(in oklab, rgba(106, 196, 130, 0.18) 110%, var(--bg-1));
}
.hal0-toast-warning {
  border-color: var(--warn-line);
  background: color-mix(in oklab, var(--warn-soft) 110%, var(--bg-1));
}
.hal0-toast-error {
  border-color: var(--err-line);
  background: color-mix(in oklab, var(--err-soft) 110%, var(--bg-1));
}
.hal0-toast-msg { flex: 1; line-height: 1.4; word-wrap: break-word; }
.hal0-toast-x {
  background: transparent;
  border: none;
  color: var(--fg-4);
  cursor: pointer;
  padding: 2px;
  display: inline-flex;
  align-items: center;
}
.hal0-toast-x:hover { color: var(--fg); }
</style>
