<script setup>
/**
 * ModelDetail.vue — right-top pane of the v2 Models view.
 *
 * Header (id + coords + namespace badge + capability chips), recipe
 * options (key/value list w/ inline Edit form + real-time llamacpp_args
 * denied-flag rejection), Used-by panel, On-disk panel, actions row.
 *
 * "Load now" disabled when no compatible slot exists for the model type;
 * tooltip explains why. Reveal/Copy-path fallback per OS capability.
 *
 * Mirrors `ModelDetail` + `UsedByPanel` + `OnDiskPanel` in
 *   /tmp/hal0-design/hal0-v2/project/dash/{models,model-modals}.jsx
 */
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  model: { type: Object, default: null },
  recipe: { type: Object, default: () => ({}) },
  slots: { type: Array, default: () => [] },
})

const emit = defineEmits(['load', 'reveal', 'delete', 'recipe-update', 'pull'])

const router = useRouter()

// Real-time-rejected llamacpp flags. Source of truth: docs/internal/
// lemonade-migration-plan.md §B (these flags are owned by lemond/slot
// orchestration; user must NOT override them).
const DENIED_LLAMACPP_FLAGS = Object.freeze([
  '-m', '--port', '--ctx-size', '-ngl',
  '--jinja', '--mmproj', '--embeddings', '--reranking',
])

const editing = ref(false)
const draft = ref({})
const draftErrors = ref({})

watch(() => props.recipe, (r) => {
  if (!editing.value) draft.value = { ...(r || {}) }
}, { immediate: true, deep: true })

watch(() => props.model?.id, () => {
  editing.value = false
  draftErrors.value = {}
})

function startEdit() {
  draft.value = { ...(props.recipe || {}) }
  draftErrors.value = {}
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  draftErrors.value = {}
}

function validateLlamacppArgs(value) {
  if (typeof value !== 'string' || !value.trim()) return null
  const tokens = value.split(/\s+/)
  const hits = []
  for (const tok of tokens) {
    const head = tok.split('=')[0]
    if (DENIED_LLAMACPP_FLAGS.includes(head)) hits.push(head)
  }
  if (hits.length === 0) return null
  const unique = [...new Set(hits)]
  return `denied flag${unique.length > 1 ? 's' : ''}: ${unique.join(', ')} — lemond owns these`
}

function onDraftInput(key, value) {
  draft.value = { ...draft.value, [key]: value }
  if (key === 'llamacpp_args') {
    const err = validateLlamacppArgs(value)
    if (err) draftErrors.value = { ...draftErrors.value, llamacpp_args: err }
    else {
      const next = { ...draftErrors.value }
      delete next.llamacpp_args
      draftErrors.value = next
    }
  }
}

const hasErrors = computed(() => Object.keys(draftErrors.value).length > 0)

function saveEdit() {
  if (hasErrors.value) return
  emit('recipe-update', { ...draft.value })
  editing.value = false
}

const compatSlots = computed(() => {
  if (!props.model) return []
  const t = props.model.type
  if (!t) return []
  return props.slots.filter((s) => s.type === t || s.kind === t)
})

const canLoadNow = computed(() => props.model?.installed && compatSlots.value.length > 0)

const slotsUsing = computed(() => {
  if (!props.model) return []
  const m = props.model
  const repoStem = (m.repo || '').split(':')[0]
  return props.slots.filter((s) => {
    if (s.model === m.id || s.model_id === m.id) return true
    if (repoStem && s.modelLong && s.modelLong.includes(repoStem)) return true
    return false
  })
})

const labelChips = computed(() => Array.isArray(props.model?.labels) ? props.model.labels : [])

const onDiskPath = computed(() => {
  if (!props.model?.installed) return null
  // Default convention: /var/lib/hal0/models/<id>.gguf. Real backend will
  // emit the path in /api/models/<id>; respect that if present.
  return props.model?.path || `/var/lib/hal0/models/${props.model.id}.gguf`
})

const sha256 = computed(() => props.model?.sha256 || null)
const verifiedAt = computed(() => props.model?.verified_at || null)

const supportsReveal = computed(() => {
  // Tauri / Electron / system bridge would provide a reveal-in-folder
  // capability via window.__hal0BridgeReveal. In plain browser, fall back
  // to copy-path.
  return typeof window !== 'undefined' && typeof window.__hal0BridgeReveal === 'function'
})

function onLoad() {
  if (!canLoadNow.value) return
  emit('load', props.model)
}
function onReveal() {
  if (supportsReveal.value) {
    try { window.__hal0BridgeReveal(onDiskPath.value) } catch { /* swallow */ }
  } else {
    // Copy-path fallback.
    try {
      navigator.clipboard?.writeText?.(onDiskPath.value)
    } catch { /* swallow */ }
    emit('reveal', { model: props.model, path: onDiskPath.value, copied: true })
  }
}
function onDelete() { emit('delete', props.model) }
function gotoSlot(name) {
  router.push(`/slots/${encodeURIComponent(name)}`)
}

const recipeEntries = computed(() => Object.entries(props.recipe || {}))
</script>

<template>
  <div v-if="!model" class="mdl-detail empty">
    <div class="empty-text">Pick a model from the left to inspect.</div>
  </div>

  <div v-else class="mdl-detail" :data-detail-id="model.id">
    <div class="mdl-detail-h">
      <div class="title-row">
        <div :class="['dot', model.installed ? 'ready' : 'empty']" />
        <div class="nm mono">{{ model.longName || model.name || model.id }}</div>
        <span class="namespace">
          <span v-if="model.installed" class="chip ok">installed</span>
          <span v-else class="chip amber">available</span>
          <span v-if="model.ns" class="chip outlined">{{ model.ns }}</span>
        </span>
      </div>
      <div class="repo">{{ model.repo || model.id }}</div>
      <div v-if="labelChips.length" class="cap-chips">
        <span v-for="l in labelChips" :key="l" class="chip">{{ l }}</span>
      </div>
    </div>

    <div class="mdl-detail-meta">
      <div><div class="k">params</div><div class="v">{{ model.params || '—' }}</div></div>
      <div><div class="k">size</div><div class="v">{{ model.size || '—' }}</div></div>
      <div><div class="k">type</div><div class="v">{{ model.type || '—' }}</div></div>
      <div><div class="k">device</div><div class="v">{{ model.device || '—' }}</div></div>
      <div><div class="k">runtime</div><div class="v">{{ model.runtime || '—' }}</div></div>
      <div><div class="k">namespace</div><div class="v">{{ model.ns || '—' }}</div></div>
    </div>

    <!-- Recipe options -->
    <div class="mdl-detail-recipe">
      <div class="recipe-h">
        <div class="lbl">recipe options</div>
        <button
          v-if="!editing"
          type="button"
          class="btn ghost sm"
          data-test="recipe-edit"
          @click="startEdit"
        >Edit</button>
        <span v-else class="recipe-actions">
          <button type="button" class="btn ghost sm" @click="cancelEdit">Cancel</button>
          <button
            type="button"
            class="btn sm"
            :disabled="hasErrors"
            data-test="recipe-save"
            @click="saveEdit"
          >Save</button>
        </span>
      </div>

      <template v-if="!editing">
        <div v-if="recipeEntries.length === 0" class="recipe-empty mono">No recipe options defined.</div>
        <div
          v-for="[k, v] in recipeEntries"
          :key="k"
          class="ro-row"
        >
          <span class="k">{{ k }}</span>
          <span class="v mono">{{ String(v) }}</span>
        </div>
      </template>

      <template v-else>
        <div
          v-for="[k] in recipeEntries"
          :key="k"
          class="ro-row edit"
        >
          <label class="k">
            {{ k }}
            <span v-if="k === 'llamacpp_args'" class="sub-lbl">
              denied: {{ DENIED_LLAMACPP_FLAGS.join(' ') }}
            </span>
          </label>
          <div class="ctl">
            <input
              v-if="k !== 'llamacpp_args'"
              :value="draft[k]"
              class="input mono recipe-input"
              @input="onDraftInput(k, $event.target.value)"
            />
            <textarea
              v-else
              :value="draft[k]"
              class="input mono recipe-input"
              rows="2"
              spellcheck="false"
              :data-test="'recipe-' + k"
              @input="onDraftInput(k, $event.target.value)"
            />
            <span
              v-if="draftErrors[k]"
              class="err mono"
              :data-test="'recipe-err-' + k"
            >{{ draftErrors[k] }}</span>
          </div>
        </div>
      </template>

      <div class="recipe-foot mono">
        <span class="warn-glyph">⟳</span>
        <span>ctx_size + llamacpp_backend require slot restart to apply.</span>
      </div>
    </div>

    <!-- Used by -->
    <div class="mdl-detail-recipe">
      <div class="lbl">Used by</div>
      <div v-if="slotsUsing.length === 0" class="recipe-empty mono">
        No slot references this model.
      </div>
      <div
        v-for="s in slotsUsing"
        :key="s.name"
        class="ro-row clickable"
        :data-used-by="s.name"
        @click="gotoSlot(s.name)"
      >
        <span class="k slot-link">
          <span :class="['dot', s.state || 'idle']" />
          {{ s.name }}
        </span>
        <span class="v">
          <span class="meta">{{ (s.type || s.kind) }} · {{ s.device || '—' }}</span>
          <span v-if="s.isDefault || s.is_default" class="chip outlined amber default-chip">default</span>
          <span class="arrow">→</span>
        </span>
      </div>
    </div>

    <!-- On disk -->
    <div v-if="model.installed" class="mdl-detail-recipe">
      <div class="lbl">On disk</div>
      <div class="ro-row">
        <span class="k">path</span>
        <span class="v mono path">{{ onDiskPath }}</span>
      </div>
      <div class="ro-row">
        <span class="k">sha256</span>
        <span class="v mono sha">{{ sha256 ? `${sha256.slice(0, 8)}…${sha256.slice(-4)}` : '—' }}</span>
      </div>
      <div class="ro-row">
        <span class="k">verified</span>
        <span class="v">
          <span v-if="verifiedAt"><span class="ok-glyph">✓</span> {{ verifiedAt }}</span>
          <span v-else class="fg-4">not verified</span>
        </span>
      </div>
      <div class="ondisk-actions">
        <button
          type="button"
          class="btn ghost sm"
          data-test="reveal-btn"
          @click="onReveal"
        >{{ supportsReveal ? 'Reveal in file manager' : 'Copy path' }}</button>
      </div>
    </div>

    <!-- Actions -->
    <div class="mdl-detail-actions">
      <template v-if="model.installed">
        <button
          type="button"
          :class="['btn', { primary: canLoadNow }]"
          :disabled="!canLoadNow"
          :title="canLoadNow ? '' : 'Create a slot of type `llm` to use this model'"
          data-test="load-now"
          @click="onLoad"
        >Load now</button>
        <button
          v-if="!supportsReveal"
          type="button"
          class="btn ghost sm"
          @click="onReveal"
        >Copy path</button>
        <button
          v-else
          type="button"
          class="btn ghost sm"
          @click="onReveal"
        >Reveal</button>
        <button
          type="button"
          class="btn danger sm"
          data-test="delete-btn"
          @click="onDelete"
        >Delete</button>
      </template>
      <template v-else>
        <button
          type="button"
          class="btn primary"
          data-test="pull-btn"
          @click="emit('pull', model)"
        >Pull{{ model.size ? ` (${model.size})` : '' }}</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.mdl-detail {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  overflow: hidden;
}
.mdl-detail.empty {
  padding: 40px 24px;
  text-align: center;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-4);
}
.empty-text { line-height: 1.6; }

.mdl-detail-h {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--bg);
}
.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.namespace {
  margin-left: auto;
  display: inline-flex;
  gap: 6px;
}
.mdl-detail-h .nm {
  font-family: var(--jbm);
  font-size: 15px;
  font-weight: 500;
  color: var(--fg);
}
.mdl-detail-h .repo {
  font-family: var(--jbm);
  font-size: 11px;
  color: var(--fg-3);
}
.cap-chips {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.mdl-detail-meta {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-soft);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  font-family: var(--jbm);
}
.mdl-detail-meta .k {
  color: var(--fg-4);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 2px;
}
.mdl-detail-meta .v {
  color: var(--fg);
  font-size: 12px;
}

.mdl-detail-recipe {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-soft);
}
.recipe-h {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.recipe-actions { display: inline-flex; gap: 6px; }
.lbl {
  font-family: var(--jbm);
  font-size: 10px;
  color: var(--fg-4);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.ro-row {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 12px;
  padding: 5px 0;
  font-family: var(--jbm);
  font-size: 12px;
  align-items: center;
}
.ro-row.edit { align-items: start; }
.ro-row .k { color: var(--fg-4); }
.ro-row .v { color: var(--fg-2); }
.ro-row.clickable { cursor: pointer; padding: 7px 0; }
.ro-row.clickable:hover { background: var(--bg-2); }
.slot-link { display: flex; align-items: center; gap: 6px; }
.meta { color: var(--fg-3); margin-right: 6px; }
.default-chip { font-size: 9.5px; }
.arrow { color: var(--accent); margin-left: 6px; }
.recipe-empty { font-size: 11.5px; color: var(--fg-5); font-style: italic; padding: 4px 0; }

.sub-lbl {
  display: block;
  margin-top: 2px;
  color: var(--fg-5);
  font-size: 10px;
  text-transform: none;
  letter-spacing: 0;
}
.ctl { display: flex; flex-direction: column; gap: 4px; }
.recipe-input { width: 100%; box-sizing: border-box; }
.err {
  font-size: 10.5px;
  color: var(--err);
}

.recipe-foot {
  margin-top: 10px;
  font-size: 11px;
  color: var(--fg-4);
  display: flex;
  align-items: center;
  gap: 6px;
}
.warn-glyph { color: var(--warn); }
.ok-glyph { color: var(--ok); }
.path { word-break: break-all; font-size: 11px; }
.sha { font-size: 11px; color: var(--fg-3); }
.ondisk-actions { display: flex; gap: 6px; margin-top: 8px; }

.mdl-detail-actions {
  padding: 14px 16px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot.ready { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
.dot.empty { background: var(--fg-5); }
.dot.serving { background: var(--accent); }
.dot.idle    { background: var(--ok); opacity: 0.45; }
.dot.error   { background: var(--err); }

.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #0a0a0a;
}
.btn.primary:hover { filter: brightness(1.06); }
.btn.primary[disabled] {
  background: transparent;
  border-color: var(--line);
  color: var(--fg-4);
}

.fg-4 { color: var(--fg-4); }
</style>
