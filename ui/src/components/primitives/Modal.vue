<script setup>
/**
 * primitives/Modal.vue — v2 dashboard generic modal shell.
 *
 * Mirrors the React `Modal` in
 *   /tmp/hal0-design-v3/dash/primitives.jsx (lines 8–42)
 * 1:1 in markup + class names, so it consumes the global `.modal-*`
 * styles defined inline below (kept on the component to match the
 * design source which co-locates the CSS in chrome.jsx).
 *
 * Behaviour
 * ─────────
 *   - `open=false` → renders nothing (no overlay, no body-lock).
 *   - Esc closes when `dismissable=true` (default).
 *   - Backdrop mouse-down closes when `dismissable=true`.
 *   - Body scroll is locked while open (saves/restores `overflow`).
 *   - Focus is trapped inside `.modal-shell` via Tab/Shift+Tab cycling;
 *     focus is restored to the previously-focused element on close.
 *
 * Slot
 * ────
 *   Default slot is the body content. Pass `eyebrow`/`title` for the
 *   header strip and `foot` (slot or string) for the footer strip.
 */
import { onBeforeUnmount, ref, watch, nextTick } from 'vue'

const props = defineProps({
  open:        { type: Boolean, default: false },
  onClose:     { type: Function, default: () => {} },
  title:       { type: String,  default: '' },
  eyebrow:     { type: String,  default: '' },
  width:       { type: Number,  default: 640 },
  dismissable: { type: Boolean, default: true },
  // Optional id on the title H2 so callers can wire aria-labelledby
  // from the dialog root to a stable selector (mirrors Drawer.vue's
  // same prop — preserves a11y test intent across UI rewrites).
  titleId:     { type: String,  default: '' },
})

const overlayRef = ref(null)
const shellRef   = ref(null)

let _prevFocus = null
let _prevOverflow = ''

function onKey(e) {
  if (e.key === 'Escape' && props.dismissable) {
    props.onClose()
    return
  }
  if (e.key === 'Tab' && shellRef.value) {
    // Hand-rolled focus trap.
    const focusables = shellRef.value.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
    if (!focusables.length) return
    const first = focusables[0]
    const last  = focusables[focusables.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }
}

function onBackdropMouseDown(e) {
  if (props.dismissable && e.target === overlayRef.value) {
    props.onClose()
  }
}

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    _prevFocus = document.activeElement
    _prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKey)
    await nextTick()
    // Focus first focusable inside the shell, or the shell itself.
    if (shellRef.value) {
      const f = shellRef.value.querySelector(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
      ;(f || shellRef.value).focus?.()
    }
  } else {
    document.removeEventListener('keydown', onKey)
    document.body.style.overflow = _prevOverflow
    if (_prevFocus && typeof _prevFocus.focus === 'function') {
      _prevFocus.focus()
    }
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
    <div
      v-if="open"
      ref="overlayRef"
      class="modal-backdrop"
      @mousedown="onBackdropMouseDown"
    >
      <div
        ref="shellRef"
        class="modal-shell"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
        :style="{ maxWidth: width + 'px' }"
        :aria-labelledby="titleId || undefined"
        @mousedown.stop
      >
        <div v-if="title || eyebrow" class="modal-h">
          <div v-if="eyebrow" class="modal-h-eye mono">{{ eyebrow }}</div>
          <h2 v-if="title" :id="titleId || undefined" class="mono">{{ title }}</h2>
          <button
            v-if="dismissable"
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
        <div class="modal-body">
          <slot />
        </div>
        <div v-if="$slots.foot" class="modal-foot mono">
          <slot name="foot" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(2px);
  z-index: 100;
  display: flex; align-items: flex-start; justify-content: center;
  padding: 80px 24px 24px;
  overflow-y: auto;
}
.modal-shell {
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--rad-lg);
  box-shadow: 0 24px 80px -16px rgba(0, 0, 0, 0.8);
  max-width: 680px;
  width: 100%;
  overflow: hidden;
  outline: none;
}
.modal-h {
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--line-soft);
  position: relative;
}
.modal-h-eye {
  font-size: 10px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 4px;
}
.modal-h h2 {
  font-size: 18px;
  font-weight: 500;
  margin: 0;
  letter-spacing: -0.02em;
  color: var(--fg);
}
.modal-close {
  position: absolute;
  top: 16px; right: 16px;
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  cursor: pointer;
  color: var(--fg-3);
}
.modal-close:hover { color: var(--fg); border-color: var(--line-strong); }
.modal-body {
  padding: 18px 22px;
  max-height: 70vh;
  overflow-y: auto;
  color: var(--fg);
}
.modal-foot {
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
