<script setup>
/**
 * ModelList.vue — left pane of the v2 Models view.
 *
 * Filter chips (type / device / labels / namespace) + search +
 * active-filter summary + sectioned list (installed / blessed / user.*).
 * Selection emits up via `update:selectedId`.
 *
 * Mirrors `ModelsView` left/list pieces from
 *   /tmp/hal0-design/hal0-v2/project/dash/models.jsx
 */
import { computed, ref, watch } from 'vue'

const props = defineProps({
  models: { type: Array, required: true },
  selectedId: { type: String, default: null },
})

const emit = defineEmits(['update:selectedId'])

const TYPES   = ['llm', 'embedding', 'reranking', 'transcription', 'tts', 'image']
const DEVICES = ['rocm', 'vulkan', 'cpu', 'npu']
const LABELS  = ['chat', 'tool-calling', 'vision', 'reasoning', 'embeddings', 'reranking', 'transcription', 'tts', 'image', 'edit']
const NAMESPACES = ['blessed', 'pulled']

const search = ref('')
const filters = ref({
  type: null,
  device: null,
  label: null,
  ns: null,
})

function toggle(key, value) {
  filters.value = { ...filters.value, [key]: filters.value[key] === value ? null : value }
}

function clearAll() {
  filters.value = { type: null, device: null, label: null, ns: null }
  search.value = ''
}

const activeChipCount = computed(() => {
  let n = 0
  for (const k of ['type', 'device', 'label', 'ns']) if (filters.value[k]) n++
  if (search.value.trim()) n++
  return n
})

function modelMatchesFilter(m) {
  const f = filters.value
  if (f.type && m.type !== f.type) return false
  if (f.device) {
    const dev = (m.device || '').replace(/^gpu-/, '')
    if (dev !== f.device) return false
  }
  if (f.label) {
    const ls = Array.isArray(m.labels) ? m.labels : []
    if (!ls.includes(f.label)) return false
  }
  if (f.ns && m.ns !== f.ns) return false
  if (search.value.trim()) {
    const q = search.value.trim().toLowerCase()
    const hay = [m.id, m.longName || m.name, m.repo].filter(Boolean).join(' ').toLowerCase()
    if (!hay.includes(q)) return false
  }
  return true
}

const filtered = computed(() => props.models.filter(modelMatchesFilter))

const installed = computed(() => filtered.value.filter((m) => m.installed))
const blessed   = computed(() => filtered.value.filter((m) => !m.installed && m.ns === 'blessed'))
const userNs    = computed(() => filtered.value.filter((m) => m.ns === 'pulled' && !m.installed))

const noResults = computed(() => filtered.value.length === 0 && (activeChipCount.value > 0 || props.models.length > 0))

function select(id) {
  emit('update:selectedId', id)
}

watch(() => props.models, () => {
  // If selected got filtered out / removed, drop the selection so the
  // parent can pick a sensible default (first row).
  if (!props.selectedId) return
  if (!props.models.some((m) => m.id === props.selectedId)) {
    emit('update:selectedId', null)
  }
}, { deep: true })
</script>

<template>
  <div class="mdl-list-pane">
    <!-- Filters -->
    <div class="mdl-filters" data-test="mdl-filters">
      <div class="mdl-filter-grp">
        <div class="lbl">type</div>
        <div class="mdl-filter-chips">
          <button
            v-for="t in TYPES"
            :key="t"
            type="button"
            :class="['mdl-chip', { on: filters.type === t }]"
            :data-filter-type="t"
            @click="toggle('type', t)"
          >{{ t }}</button>
        </div>
      </div>

      <div class="mdl-filter-grp">
        <div class="lbl">device</div>
        <div class="mdl-filter-chips">
          <button
            v-for="d in DEVICES"
            :key="d"
            type="button"
            :class="['mdl-chip', { on: filters.device === d }]"
            :data-filter-device="d"
            @click="toggle('device', d)"
          >{{ d }}</button>
        </div>
      </div>

      <div class="mdl-filter-grp">
        <div class="lbl">labels</div>
        <div class="mdl-filter-chips">
          <button
            v-for="l in LABELS"
            :key="l"
            type="button"
            :class="['mdl-chip', { on: filters.label === l }]"
            :data-filter-label="l"
            @click="toggle('label', l)"
          >{{ l }}</button>
        </div>
      </div>

      <div class="mdl-filter-grp">
        <div class="lbl">namespace</div>
        <div class="mdl-filter-chips">
          <button
            v-for="n in NAMESPACES"
            :key="n"
            type="button"
            :class="['mdl-chip', { on: filters.ns === n }]"
            :data-filter-ns="n"
            @click="toggle('ns', n)"
          >{{ n }}</button>
        </div>
      </div>

      <div class="mdl-filter-grp">
        <div class="lbl">search</div>
        <input
          v-model="search"
          class="input mono"
          placeholder="qwen, embed, …"
          data-test="mdl-search"
        />
      </div>

      <div v-if="activeChipCount > 0" class="active-summary mono">
        <span class="ct">{{ activeChipCount }} filter{{ activeChipCount > 1 ? 's' : '' }} active</span>
        <button type="button" class="clear-link" @click="clearAll">Clear all</button>
      </div>
    </div>

    <!-- List -->
    <div class="mdl-list">
      <div class="mdl-list-h">
        <span>Catalog</span>
        <span class="ct">· {{ filtered.length }} shown</span>
      </div>

      <template v-if="noResults">
        <div class="mdl-empty">
          <div>No models match filter.</div>
          <button type="button" class="btn ghost sm" @click="clearAll">Clear filters</button>
        </div>
      </template>

      <template v-else>
        <template v-if="installed.length">
          <div class="mdl-section-label">Installed · {{ installed.length }}</div>
          <div
            v-for="m in installed"
            :key="m.id"
            :class="['mdl-row', { sel: selectedId === m.id }]"
            :data-model-id="m.id"
            @click="select(m.id)"
          >
            <span class="dot ready" />
            <span class="nm">
              {{ m.longName || m.name || m.id }}
              <span v-if="m.repo" class="sub">{{ m.repo }}</span>
            </span>
            <span class="sz num">{{ m.params || '' }}</span>
            <span class="sz num">{{ m.size || '' }}</span>
            <span class="tg">
              <span v-if="m.collection || m.type === 'omni'" class="chip">omni</span>
            </span>
          </div>
        </template>

        <template v-if="blessed.length">
          <div class="mdl-section-label">Available · blessed · {{ blessed.length }}</div>
          <div
            v-for="m in blessed"
            :key="m.id"
            :class="['mdl-row', { sel: selectedId === m.id }]"
            :data-model-id="m.id"
            @click="select(m.id)"
          >
            <span class="dot empty" />
            <span class="nm">
              {{ m.longName || m.name || m.id }}
              <span v-if="m.repo" class="sub">{{ m.repo }}</span>
            </span>
            <span class="sz num">{{ m.params || '' }}</span>
            <span class="sz num">{{ m.size || '' }}</span>
            <span class="tg">
              <span class="chip amber">blessed</span>
            </span>
          </div>
        </template>

        <template v-if="userNs.length">
          <div class="mdl-section-label">user.* · {{ userNs.length }}</div>
          <div
            v-for="m in userNs"
            :key="m.id"
            :class="['mdl-row', { sel: selectedId === m.id }]"
            :data-model-id="m.id"
            @click="select(m.id)"
          >
            <span class="dot empty" />
            <span class="nm">
              {{ m.longName || m.name || m.id }}
              <span v-if="m.repo" class="sub">{{ m.repo }}</span>
            </span>
            <span class="sz num">{{ m.params || '' }}</span>
            <span class="sz num">{{ m.size || '' }}</span>
            <span class="tg"><span class="chip">user</span></span>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.mdl-list-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.mdl-filters {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.mdl-filter-grp .lbl {
  font-family: var(--jbm);
  font-size: 10px;
  color: var(--fg-4);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 6px;
}
.mdl-filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.mdl-chip {
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: 3px;
  background: transparent;
  font-family: var(--jbm);
  font-size: 11px;
  color: var(--fg-3);
  cursor: pointer;
}
.mdl-chip:hover { color: var(--fg); border-color: var(--line-strong); }
.mdl-chip.on { color: var(--accent); border-color: var(--accent-line); background: var(--accent-soft); }

.active-summary {
  border-top: 1px solid var(--line-soft);
  padding-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10.5px;
  color: var(--fg-4);
}
.clear-link {
  background: transparent;
  border: none;
  color: var(--accent);
  font-family: var(--jbm);
  font-size: 10.5px;
  cursor: pointer;
  padding: 0;
}
.clear-link:hover { text-decoration: underline; }

.mdl-list {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  overflow: hidden;
}
.mdl-list-h {
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
  display: flex;
  align-items: center;
  gap: 14px;
  font-family: var(--jbm);
  font-size: 11px;
  color: var(--fg-3);
}
.mdl-list-h .ct { color: var(--fg-5); }

.mdl-section-label {
  padding: 10px 16px 6px;
  font-family: var(--jbm);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--fg-4);
  background: var(--bg);
  border-bottom: 1px solid var(--line-soft);
}

.mdl-row {
  display: grid;
  grid-template-columns: 14px 1fr 56px 70px 60px;
  gap: 10px;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line-soft);
  cursor: pointer;
  font-family: var(--jbm);
  font-size: 12.5px;
}
.mdl-row:last-child { border-bottom: none; }
.mdl-row:hover { background: var(--bg-2); }
.mdl-row.sel { background: var(--bg-3, var(--accent-soft)); }
.mdl-row .nm { color: var(--fg); font-weight: 500; overflow: hidden; }
.mdl-row .nm .sub { color: var(--fg-4); font-weight: 400; font-size: 10.5px; display: block; margin-top: 2px; }
.mdl-row .sz { color: var(--fg-3); text-align: right; font-size: 11px; }
.mdl-row .tg { color: var(--fg-3); font-size: 10.5px; text-align: right; }

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot.ready { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
.dot.empty { background: var(--fg-5); }

.mdl-empty {
  padding: 28px 16px;
  text-align: center;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-4);
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
}
</style>
