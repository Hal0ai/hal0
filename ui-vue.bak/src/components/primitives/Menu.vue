<script setup>
/**
 * primitives/Menu.vue — popover dropdown menu.
 *
 * Mirrors the React `Menu` in
 *   /tmp/hal0-design-v3/dash/primitives.jsx (lines 384–403),
 * extended with the v2 brief's behaviours:
 *
 *   - `open` controls visibility (the React source assumes the parent
 *     conditionally renders Menu; we keep an `open` prop to match the
 *     other primitives' API surface for easier mounting from tests).
 *   - `anchor` is the trigger element. When provided we position the
 *     menu absolutely below it using getBoundingClientRect().
 *   - Auto-close on document click outside, Escape, and selection.
 *   - Item with `onClick`: fire AND return (do NOT also toast).
 *   - Item without `onClick`: toast "<label> — stubbed" via
 *     useToastStore (matches the design's window.__hal0Toast fallback).
 *
 * Items shape:
 *   [
 *     { icon?: VNode, label: string, kbd?: string, danger?: boolean,
 *       divider?: boolean, onClick?: () => void }
 *   ]
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  open:    { type: Boolean, default: false },
  anchor:  { type: [Object, null], default: null },     // HTMLElement
  items:   { type: Array, required: true },
  onClose: { type: Function, default: () => {} },
  side:    { type: String, default: 'right' },          // 'left' | 'right'
})

const toasts = useToastStore()
const menuRef = ref(null)

const position = ref({ top: 0, left: 0, width: 0 })

function reposition() {
  if (!props.anchor) return
  const r = props.anchor.getBoundingClientRect()
  position.value = {
    top:   r.bottom + 4,
    left:  r.left,
    width: r.width,
  }
}

const computedStyle = computed(() => {
  if (!props.anchor) return {}
  // Right-aligned: anchor's right edge.
  if (props.side === 'right') {
    return {
      position: 'fixed',
      top:   position.value.top + 'px',
      // align right edge of menu with right edge of anchor
      left:  'auto',
      right: (window.innerWidth - (position.value.left + position.value.width)) + 'px',
    }
  }
  return {
    position: 'fixed',
    top:  position.value.top + 'px',
    left: position.value.left + 'px',
  }
})

function onDocClick(e) {
  if (!props.open) return
  if (menuRef.value && menuRef.value.contains(e.target)) return
  if (props.anchor && props.anchor.contains(e.target)) return
  props.onClose()
}

function onKey(e) {
  if (e.key === 'Escape' && props.open) props.onClose()
}

function selectItem(it) {
  if (it.divider) return
  if (it.onClick) {
    it.onClick()
  } else {
    toasts.push(`${it.label} — stubbed`, 'info')
  }
  props.onClose()
}

watch(() => props.open, (v) => {
  if (v) {
    reposition()
    // Defer listener attachment so the click that opened the menu
    // doesn't immediately close it.
    setTimeout(() => {
      document.addEventListener('click', onDocClick)
    }, 0)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
  } else {
    document.removeEventListener('click', onDocClick)
    document.removeEventListener('keydown', onKey)
    window.removeEventListener('resize', reposition)
    window.removeEventListener('scroll', reposition, true)
  }
}, { immediate: true })

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', reposition)
  window.removeEventListener('scroll', reposition, true)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      ref="menuRef"
      :class="['hal0-menu', side]"
      :style="computedStyle"
      role="menu"
      @click.stop
    >
      <template v-for="(it, i) in items" :key="i">
        <div v-if="it.divider" class="hal0-menu-divider" />
        <div
          v-else
          :class="['hal0-menu-item', { danger: it.danger }]"
          role="menuitem"
          tabindex="0"
          @click="selectItem(it)"
          @keydown.enter.prevent="selectItem(it)"
          @keydown.space.prevent="selectItem(it)"
        >
          <span v-if="it.icon" class="hal0-menu-ic" v-html="it.icon" />
          <span class="hal0-menu-lbl">{{ it.label }}</span>
          <span v-if="it.kbd" class="hal0-menu-kbd kbd">{{ it.kbd }}</span>
        </div>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.hal0-menu {
  z-index: 60;
  min-width: 200px;
  background: var(--bg-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--rad);
  box-shadow: 0 16px 48px -8px rgba(0, 0, 0, 0.65);
  padding: 4px;
  font-family: var(--jbm);
}
.hal0-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--fg-2);
  user-select: none;
}
.hal0-menu-item:hover { background: var(--bg-3); color: var(--fg); }
.hal0-menu-item.danger { color: var(--err); }
.hal0-menu-item.danger:hover { background: var(--err-soft); }
.hal0-menu-ic { color: var(--fg-4); display: inline-flex; }
.hal0-menu-item:hover .hal0-menu-ic { color: var(--fg-2); }
.hal0-menu-lbl { flex: 1; }
.hal0-menu-kbd { margin-left: auto; }
.hal0-menu-divider { height: 1px; background: var(--line-soft); margin: 4px 0; }
</style>
