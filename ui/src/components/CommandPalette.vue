<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { api } from '../composables/useApi.js'

const props = defineProps({ open: Boolean })
const emit  = defineEmits(['close', 'select'])

const router = useRouter()
const system = useSystemStore()
const toasts = useToastsStore()

const query    = ref('')
const active   = ref(0)
const inputRef = ref(null)

// ── Static nav items ───────────────────────────────────────────────
const NAV = [
  { id: 'nav:dashboard',  label: 'Dashboard',  hint: 'overview',   kind: 'nav',    to: '/' },
  { id: 'nav:slots',      label: 'Slots',      hint: 'inference',  kind: 'nav',    to: '/slots' },
  { id: 'nav:models',     label: 'Models',     hint: 'registry',   kind: 'nav',    to: '/models' },
  { id: 'nav:hardware',   label: 'Hardware',   hint: 'probe',      kind: 'nav',    to: '/hardware' },
  { id: 'nav:logs',       label: 'Logs',       hint: 'stream',     kind: 'nav',    to: '/logs' },
  { id: 'nav:backends',   label: 'Backends',   hint: 'runtime',    kind: 'nav',    to: '/backends' },
  { id: 'nav:settings',   label: 'Settings',   hint: 'config',     kind: 'nav',    to: '/settings' },
  { id: 'nav:welcome',    label: 'First-run wizard', hint: 'setup', kind: 'nav',   to: '/welcome' },
]

// ── Dynamic slot actions ───────────────────────────────────────────
const slotItems = computed(() =>
  system.slots.flatMap((s) => {
    const running = s.status === 'running'
    return [
      running && {
        id: `slot:${s.name}:restart`,
        label: `Restart slot "${s.name}"`,
        hint: `:${s.port}`,
        kind: 'action',
        handler: async () => {
          try {
            await api(`/api/slots/${s.name}/restart`, { method: 'POST' })
            toasts.success(`Restarting ${s.name}`)
            system.fetchStatus()
          } catch (e) { toasts.error(e.message) }
        },
      },
      running && {
        id: `slot:${s.name}:unload`,
        label: `Unload slot "${s.name}"`,
        hint: `:${s.port}`,
        kind: 'action',
        handler: async () => {
          try {
            await api(`/api/slots/${s.name}/unload`, { method: 'POST' })
            toasts.success(`Unloaded ${s.name}`)
            system.fetchStatus()
          } catch (e) { toasts.error(e.message) }
        },
      },
      !running && {
        id: `slot:${s.name}:load`,
        label: `Load slot "${s.name}"`,
        hint: `:${s.port}`,
        kind: 'action',
        to: '/slots',
      },
    ].filter(Boolean)
  })
)

// ── Filtered, ranked results ───────────────────────────────────────
const items = computed(() => {
  const all = [...NAV, ...slotItems.value]
  const term = query.value.trim().toLowerCase()
  if (!term) return all.slice(0, 10)

  return all
    .map((it) => ({ ...it, _score: rank(it, term) }))
    .filter((it) => it._score > 0)
    .sort((a, b) => b._score - a._score)
    .slice(0, 12)
})

function rank(item, term) {
  const hay = `${item.label} ${item.hint ?? ''}`.toLowerCase()
  if (hay.startsWith(term)) return 200
  if (hay.includes(term))   return 100 - hay.indexOf(term)
  let i = 0
  for (const ch of hay) { if (ch === term[i]) i++; if (i >= term.length) return 1 }
  return 0
}

// ── Keyboard & lifecycle ───────────────────────────────────────────
watch(() => props.open, async (v) => {
  if (v) {
    query.value = ''
    active.value = 0
    await nextTick()
    inputRef.value?.focus()
  }
})

watch(query, () => { active.value = 0 })

function onKey(e) {
  if (e.key === 'ArrowDown') { e.preventDefault(); active.value = Math.min(items.value.length - 1, active.value + 1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); active.value = Math.max(0, active.value - 1) }
  else if (e.key === 'Enter') { e.preventDefault(); select(items.value[active.value]) }
}

function select(item) {
  if (!item) return
  emit('close')
  if (item.handler) item.handler()
  else if (item.to) router.push(item.to)
  emit('select', item)
}

const kindLabel = { nav: 'NAV', action: 'ACT', slot: 'SLOT' }
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="cmdk-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        @click.self="$emit('close')"
      >
        <div class="cmdk-panel">
          <div class="cmdk-input-row">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z"/>
            </svg>
            <input
              ref="inputRef"
              v-model="query"
              @keydown="onKey"
              placeholder="Jump to, restart slot, search…"
              aria-label="Search commands"
              autocomplete="off"
              spellcheck="false"
            />
            <kbd class="kbd" aria-hidden="true">esc</kbd>
          </div>

          <div class="cmdk-list" role="listbox" aria-label="Command results">
            <div v-if="items.length === 0" class="cmdk-empty">No results</div>
            <div
              v-for="(it, i) in items"
              :key="it.id"
              class="cmdk-item"
              :class="{ active: i === active }"
              role="option"
              :aria-selected="i === active"
              @mouseenter="active = i"
              @click="select(it)"
            >
              <span class="kind-chip" :data-kind="it.kind">{{ kindLabel[it.kind] ?? it.kind }}</span>
              <span class="item-label">{{ it.label }}</span>
              <span v-if="it.hint" class="item-hint">{{ it.hint }}</span>
            </div>
          </div>

          <div class="cmdk-footer" aria-hidden="true">
            <span><kbd class="kbd">↑↓</kbd> navigate</span>
            <span><kbd class="kbd">↵</kbd> select</span>
            <span><kbd class="kbd">esc</kbd> close</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cmdk-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 14vh;
}

.cmdk-panel {
  width: min(560px, 90vw);
  background: var(--color-surface);
  border: 1px solid var(--color-border-hi);
  border-radius: var(--radius-xl);
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.7);
  overflow: hidden;
}

.cmdk-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-fg-faint);
}
.cmdk-input-row input {
  flex: 1;
  background: transparent;
  border: 0;
  outline: 0;
  color: var(--color-fg);
  font-size: 14px;
  font-family: var(--font-sans);
}
.cmdk-input-row input::placeholder { color: var(--color-fg-faint); }

.cmdk-list {
  max-height: 52vh;
  overflow-y: auto;
  padding: 6px;
}

.cmdk-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--color-fg-muted);
  font-size: 13px;
  transition: background 0.08s, color 0.08s;
}
.cmdk-item.active {
  background: var(--color-surface-3);
  color: var(--color-fg);
}
.cmdk-item .item-label { flex: 1; }
.cmdk-item .item-hint {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--color-fg-faint);
  flex-shrink: 0;
}

.kind-chip {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  padding: 2px 5px;
  border-radius: 3px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-faint);
  min-width: 30px;
  text-align: center;
  flex-shrink: 0;
}
.kind-chip[data-kind="action"] {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 35%, transparent);
  background: color-mix(in srgb, var(--hal0-accent) 10%, transparent);
}
.kind-chip[data-kind="slot"] {
  color: var(--color-success);
  border-color: color-mix(in oklch, var(--color-success) 30%, transparent);
  background: color-mix(in oklch, var(--color-success) 8%, transparent);
}

.cmdk-empty {
  padding: 28px;
  text-align: center;
  color: var(--color-fg-faint);
  font-size: 13px;
}

.cmdk-footer {
  display: flex;
  gap: 16px;
  padding: 8px 14px;
  border-top: 1px solid var(--color-border);
  color: var(--color-fg-faint);
  font-size: 10.5px;
  font-family: var(--font-mono);
}

.kbd {
  display: inline-grid;
  place-items: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 3px;
  border: 1px solid var(--color-border-hi);
  background: var(--color-surface-2);
  color: var(--color-fg-faint);
  font-family: var(--font-mono);
  font-size: 9.5px;
  line-height: 1;
  margin: 0 2px;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.12s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
