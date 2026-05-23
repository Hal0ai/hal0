<script setup>
/**
 * primitives/Drawer.vue — v2 dashboard right-side slide-in drawer.
 *
 * Mirrors the React `Drawer` in
 *   /tmp/hal0-design-v3/dash/primitives.jsx (lines 45–75)
 * 1:1. Slide-in via `transform: translateX(100%) → 0` driven by the
 * `.open` class on `.drawer` + `.drawer-backdrop`.
 *
 * Behaviour
 * ─────────
 *   - Esc closes the drawer.
 *   - Backdrop click closes the drawer.
 *   - Body scroll is locked while open.
 *   - Focus is trapped inside the drawer; previous focus restored on close.
 *   - `side` defaults to "right"; the design only ships right-side, but
 *     the prop is exposed for future "left" variants.
 */
import { onBeforeUnmount, ref, watch, nextTick } from 'vue'

const props = defineProps({
  open:    { type: Boolean, default: false },
  onClose: { type: Function, default: () => {} },
  title:   { type: String,  default: '' },
  eyebrow: { type: String,  default: '' },
  width:   { type: Number,  default: 520 },
  side:    { type: String,  default: 'right' },
  // Optional id on the title H2 so callers can wire aria-labelledby
  // from the dialog root to a stable selector (preserves a11y spec
  // intent across UI rewrites).
  titleId: { type: String,  default: '' },
})

const drawerRef = ref(null)

let _prevFocus = null
let _prevOverflow = ''

function onKey(e) {
  if (e.key === 'Escape') {
    props.onClose()
    return
  }
  if (e.key === 'Tab' && drawerRef.value) {
    const focusables = drawerRef.value.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
    if (!focusables.length) return
    const first = focusables[0]
    const last  = focusables[focusables.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus()
    }
  }
}

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    _prevFocus = document.activeElement
    _prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKey)
    await nextTick()
    if (drawerRef.value) {
      const f = drawerRef.value.querySelector(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
      ;(f || drawerRef.value).focus?.()
    }
  } else {
    document.removeEventListener('keydown', onKey)
    document.body.style.overflow = _prevOverflow
    if (_prevFocus && typeof _prevFocus.focus === 'function') _prevFocus.focus()
    _prevFocus = null
  }
}, { immediate: true })

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  if (props.open) document.body.style.overflow = _prevOverflow
})
</script>

<template>
  <Teleport to="body">
    <div :class="['drawer-backdrop', { open }]" @click="onClose" />
    <aside
      ref="drawerRef"
      :class="['drawer', `drawer-${side}`, { open }]"
      :style="{ width: width + 'px' }"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      :aria-hidden="!open"
      :aria-labelledby="titleId || undefined"
    >
      <div class="drawer-h">
        <div v-if="eyebrow" class="modal-h-eye mono">{{ eyebrow }}</div>
        <h2 v-if="title" :id="titleId || undefined" class="mono">{{ title }}</h2>
        <button
          type="button"
          class="modal-close"
          aria-label="Close"
          @click="onClose"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 4l8 8M12 4l-8 8"/>
          </svg>
        </button>
      </div>
      <div class="drawer-body">
        <slot />
      </div>
      <div v-if="$slots.foot" class="drawer-foot mono">
        <slot name="foot" />
      </div>
    </aside>
  </Teleport>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.55);
  z-index: 80;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.18s ease;
}
.drawer-backdrop.open { opacity: 1; pointer-events: auto; }
.drawer {
  position: fixed;
  top: 0; bottom: 0;
  background: var(--bg-1);
  z-index: 90;
  display: flex;
  flex-direction: column;
  transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
  box-shadow: -24px 0 64px -16px rgba(0,0,0,0.6);
  outline: none;
}
.drawer-right {
  right: 0;
  border-left: 1px solid var(--line-strong);
  transform: translateX(100%);
}
.drawer-left {
  left: 0;
  border-right: 1px solid var(--line-strong);
  transform: translateX(-100%);
}
.drawer.open { transform: translateX(0); }
.drawer-h {
  padding: 18px 22px 14px;
  border-bottom: 1px solid var(--line-soft);
  position: relative;
}
.drawer-h h2 {
  font-size: 18px; font-weight: 500; margin: 0; letter-spacing: -0.02em;
  color: var(--fg);
}
.drawer-h .modal-h-eye {
  font-size: 10px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 4px;
}
.modal-close {
  position: absolute;
  top: 14px; right: 16px;
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  cursor: pointer;
  color: var(--fg-3);
}
.modal-close:hover { color: var(--fg); border-color: var(--line-strong); }
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px 22px;
  color: var(--fg);
}
.drawer-foot {
  padding: 14px 22px;
  border-top: 1px solid var(--line-soft);
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--fg-4);
}
</style>
