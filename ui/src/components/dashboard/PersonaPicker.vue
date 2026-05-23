<script setup>
/**
 * dashboard/PersonaPicker.vue — slice #169.
 *
 * Persona chip + dropdown. Lists chat-capable (type=llm) slots from
 * the system store; selecting one updates the local v-model.
 *
 * Session-only persistence — the component's host (Dashboard.vue)
 * keeps the chosen persona in component-local state. Reload resets
 * to the system default (first ``is_default`` slot, else first llm).
 *
 * "+ Add chat slot" routes to ``/slots?action=create&type=llm&group=chat``
 * so the Slots view can pre-fill its create modal once that lands.
 */
import { computed, ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '../../stores/system.js'

const props = defineProps({
  modelValue: { type: String, default: '' },
  noTools:    { type: Boolean, default: false },
  disabled:   { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'swap'])

const router = useRouter()
const system = useSystemStore()

const open = ref(false)
const rootEl = ref(null)
// Active descendant index for ArrowUp / ArrowDown keyboard nav.
// -1 = nothing focused yet; on first ArrowDown we land on item 0.
const activeIndex = ref(-1)
const LISTBOX_ID = 'persona-listbox'

const llmSlots = computed(() =>
  (system.slots || []).filter((s) => (s.type || 'llm').toLowerCase() === 'llm'),
)

const current = computed(() => {
  const name = props.modelValue
  if (name) return llmSlots.value.find((s) => s.name === name)
  const def = llmSlots.value.find((s) => s.is_default)
  return def || llmSlots.value[0] || null
})

function pick(slot) {
  open.value = false
  if (!slot || slot.name === props.modelValue) return
  emit('update:modelValue', slot.name)
  emit('swap', { from: props.modelValue, to: slot.name, slot })
}

function addSlot() {
  open.value = false
  router.push('/slots?action=create&type=llm&group=chat')
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  // Seed active index to the currently-selected slot when opening so
  // ArrowDown moves to the NEXT item (not back to the head of the list).
  if (open.value) {
    const i = llmSlots.value.findIndex((s) => s.name === current.value?.name)
    activeIndex.value = i >= 0 ? i : -1
  }
}

function onKeyTrigger(e) {
  if (props.disabled) return
  // ArrowDown / Enter / Space — open and focus first item.
  if (['ArrowDown', 'Enter', ' '].includes(e.key)) {
    if (!open.value) {
      e.preventDefault()
      open.value = true
      const cur = llmSlots.value.findIndex((s) => s.name === current.value?.name)
      activeIndex.value = cur >= 0 ? cur : 0
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      activeIndex.value = Math.min(llmSlots.value.length - 1, activeIndex.value + 1)
    }
  } else if (e.key === 'ArrowUp' && open.value) {
    e.preventDefault()
    activeIndex.value = Math.max(0, activeIndex.value - 1)
  } else if (e.key === 'Escape' && open.value) {
    open.value = false
  } else if (e.key === 'Enter' && open.value && activeIndex.value >= 0) {
    e.preventDefault()
    pick(llmSlots.value[activeIndex.value])
  }
}

function onClickOutside(e) {
  if (!rootEl.value) return
  if (!rootEl.value.contains(e.target)) open.value = false
}

onMounted(() => document.addEventListener('mousedown', onClickOutside))
onBeforeUnmount(() => document.removeEventListener('mousedown', onClickOutside))

// Seed the v-model on first slot list arrival so the chip always
// has a name to show.
watch(
  current,
  (c) => {
    if (c && !props.modelValue) emit('update:modelValue', c.name)
  },
  { immediate: true },
)
</script>

<template>
  <div ref="rootEl" class="persona-wrap" data-testid="persona-picker">
    <button
      class="persona"
      :class="{ disabled }"
      :disabled="disabled"
      role="combobox"
      :aria-expanded="open"
      :aria-haspopup="'listbox'"
      :aria-controls="LISTBOX_ID"
      :aria-activedescendant="open && activeIndex >= 0 ? `persona-opt-${llmSlots[activeIndex]?.name}` : undefined"
      :aria-label="`Persona: ${current?.name || 'none'}. Press to swap chat persona.`"
      data-testid="persona-trigger"
      @click="toggle"
      @keydown="onKeyTrigger"
    >
      <span class="dot" />
      <span class="nm">
        <b>{{ current?.name || 'no persona' }}</b>
        <span v-if="current?.model" class="sub">· {{ current.model }}</span>
        <span v-if="noTools" class="sub no-tools">· no tools</span>
      </span>
      <span class="chev" aria-hidden="true">⌄</span>
    </button>
    <div
      v-if="open"
      :id="LISTBOX_ID"
      class="persona-menu"
      role="listbox"
      aria-label="Chat persona"
      data-testid="persona-menu"
    >
      <div class="pm-h" aria-hidden="true">Chat persona</div>
      <div v-if="llmSlots.length === 0" class="pm-empty">No chat slots configured.</div>
      <div
        v-for="(slot, idx) in llmSlots"
        :id="`persona-opt-${slot.name}`"
        :key="slot.name"
        class="pm-item"
        :class="{ active: current?.name === slot.name, focused: activeIndex === idx }"
        :data-testid="`persona-item-${slot.name}`"
        role="option"
        :aria-selected="current?.name === slot.name"
        tabindex="-1"
        @click="pick(slot)"
        @keydown.enter="pick(slot)"
      >
        <span class="dot" />
        <span>
          <div class="name">{{ slot.name }}</div>
          <div class="sub">{{ slot.model || 'no model' }} · {{ (slot.device || '').toUpperCase() || '—' }}</div>
          <div v-if="slot.device === 'npu'" class="warn">
            Swapping NPU chat pauses voice + embed ~14s
          </div>
        </span>
        <span v-if="current?.name === slot.name" class="check" aria-hidden="true">✓</span>
      </div>
      <div class="pm-add" data-testid="persona-add" @click="addSlot">
        <span aria-hidden="true">+</span>
        <span>Add chat slot</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.persona-wrap { position: relative; display: inline-block; }
.persona {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 9px 5px 11px;
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 6px;
  background: var(--color-surface, var(--bg, #0a0a0a));
  cursor: pointer;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11.5px;
  color: var(--color-fg, var(--fg, #e5e5e5));
}
.persona:hover {
  border-color: var(--hal0-accent, var(--accent, #feaf00));
}
.persona.disabled { opacity: 0.5; cursor: not-allowed; }
.persona .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success, #22c55e);
  box-shadow: 0 0 6px var(--color-success, #22c55e);
}
.persona .nm b {
  color: var(--hal0-accent, var(--accent, #feaf00));
  font-weight: 500;
}
.persona .nm .sub {
  color: var(--color-fg-faint, var(--fg-4, #777));
  font-size: 10px;
  margin-left: 4px;
}
.persona .nm .no-tools { color: var(--color-warning, var(--warn, #f59e0b)); }
.persona .chev { color: var(--color-fg-faint, var(--fg-4, #777)); }

.persona-menu {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  width: 300px;
  background: var(--color-surface-2, var(--bg-2, #181818));
  border: 1px solid var(--color-border-hi, var(--line-strong, #3a3a3a));
  border-radius: 6px;
  box-shadow: 0 16px 48px -8px rgba(0, 0, 0, 0.6);
  z-index: 50;
  overflow: hidden;
}
.pm-h {
  padding: 8px 12px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-fg-faint, var(--fg-4, #777));
  text-transform: uppercase;
  letter-spacing: 0.1em;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
}
.pm-empty {
  padding: 12px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11.5px;
  color: var(--color-fg-faint, var(--fg-4, #777));
}
.pm-item {
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
  cursor: pointer;
  display: grid;
  grid-template-columns: 14px 1fr auto;
  gap: 10px;
  align-items: center;
  outline: none;
}
.pm-item:hover,
.pm-item:focus-visible,
.pm-item.focused {
  background: var(--color-surface-3, var(--bg-3, #202020));
  outline: none;
}
.pm-item.focused {
  box-shadow: inset 2px 0 0 var(--hal0-accent, var(--accent, #feaf00));
}
.pm-item.active {
  background: color-mix(in oklab, var(--hal0-accent, #feaf00) 12%, transparent);
}
.pm-item .name {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12.5px;
  color: var(--color-fg, var(--fg, #e5e5e5));
}
.pm-item .sub {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10.5px;
  color: var(--color-fg-muted, var(--fg-3, #888));
  margin-top: 2px;
}
.pm-item .warn {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-warning, var(--warn, #f59e0b));
  margin-top: 3px;
}
.pm-item .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success, #22c55e);
}
.pm-item .check {
  color: var(--hal0-accent, var(--accent, #feaf00));
  font-weight: bold;
}
.pm-add {
  padding: 10px 12px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11px;
  color: var(--hal0-accent, var(--accent, #feaf00));
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pm-add:hover { background: var(--color-surface-3, var(--bg-3, #202020)); }
</style>
