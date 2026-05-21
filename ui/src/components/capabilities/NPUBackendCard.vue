<script setup>
/**
 * NPUBackendCard
 *
 * Two roles:
 *   1. LIVE view of what's currently multiplexed on the NPU, driven by
 *      `useBackend('npu')` polling /api/backends/npu every 5s.
 *      The "Loaded on NPU" list and memory bar both read from the live
 *      `{ loaded, memUsedMb, memTotalMb, totalReqPerSec }` snapshot.
 *
 *   2. Operator affordance: "+ load NPU model". The button creates a real
 *      FLM slot via `POST /api/backends/npu/load`; the slot then shows up
 *      in `/api/backends/npu`'s `loaded` array on the next poll. Removing
 *      a row tears the slot back down via `POST /api/backends/npu/unload`.
 *
 * Long-term home for this card is the Hardware view (as one of a row of
 * backend cards). Surfaced here next to the capability cards because it
 * makes the multiplex story obvious at a glance.
 */
import { computed, ref } from 'vue'
import { useCapabilities, useBackend } from '../../composables/useCapabilities.js'
import { usePullJob, fmtBytes } from '../../composables/usePullJob.js'
import { useToastsStore } from '../../stores/toasts.js'
import { api } from '../../composables/useApi.js'

const cap = useCapabilities()
const { data: npu, error: npuError, refresh: refreshNpu } = useBackend('npu')

const showAdvanced = ref(false)
const showAdd = ref(false)
const addPick = ref('')
const busyUnload = ref('')  // slot name currently being unloaded (for spinner)

// Static fallback while /api/backends/npu hasn't responded yet — keeps
// the card from flickering empty on first paint.
const FALLBACK = {
  id: 'npu',
  hardware: 'NPU',
  driver: '—',
  state: 'unknown',
  memUsedMb: 0,
  memTotalMb: 0,
  totalReqPerSec: 0,
  loaded: [],
}

const snap = computed(() => npu.value ?? FALLBACK)

// Build the "+ load NPU model" option list from the union of every
// capability's catalog where the model can run on the NPU backend. Each
// option carries the capability so we can post the right shape if/when
// v1.1 wires this up. The catalog rows are model-first grouped (see
// hal0.capabilities.catalog.models_for_capability) so a model belongs
// to NPU when its `backends` list contains an entry with id === 'npu'.
const npuModels = computed(() => {
  const out = []
  const cats = cap.catalogs.value ?? {}
  for (const slot of Object.keys(cats)) {
    for (const capability of Object.keys(cats[slot] ?? {})) {
      for (const m of cats[slot][capability] ?? []) {
        const npu = (m.backends ?? []).find((b) => b.id === 'npu')
        if (!npu) continue
        out.push({
          slot,
          capability,
          id: m.id,
          size_gb: m.size_gb,
          provider: npu.provider,
          downloaded: npu.downloaded,
          pullable: npu.pullable,
        })
      }
    }
  }
  return out
})

// IDs already on the live "loaded" list — used to filter the add picker
// so we don't offer to load a model that's already serving.
const claimedIds = computed(() => {
  const set = new Set()
  for (const item of snap.value.loaded ?? []) set.add(item.modelId)
  return set
})

const addOptions = computed(() =>
  npuModels.value.filter((m) => !claimedIds.value.has(m.id)),
)

// Live "serving" list — pulled straight from /api/backends/npu's loaded
// array. `path` is the canonical "{slot}.{child}" tag used to render the
// capability prefix in the row, and `slotName` is what the unload route
// needs.
const serving = computed(() =>
  (snap.value.loaded ?? []).map((c) => ({
    path: `${c.slot}.${c.child}`,
    slotName: c.slotName,
    modelId: c.modelId,
    source: c.source ?? 'slot',
    sizeMb: c.sizeMb,
  })),
)

const totalUsedMb = computed(() => snap.value.memUsedMb || 0)
const memPct = computed(() => {
  const tot = snap.value.memTotalMb || 0
  if (!tot) return 0
  return Math.round((totalUsedMb.value / tot) * 100)
})
const memWarn = computed(() => memPct.value >= 85)

function fmtGb(mb) { return ((mb || 0) / 1024).toFixed(1) }

const npuPull = usePullJob()
const npuToasts = useToastsStore()

async function addExtra() {
  if (!addPick.value || npuPull.inFlight.value) return
  const m = npuModels.value.find((x) => x.id === addPick.value)
  if (!m) return
  // Same pattern as the capability cards: pull first when the file
  // isn't on disk, then promote to a real FLM slot via the backend.
  if (m.downloaded === false) {
    if (m.pullable === false) {
      npuToasts.error(
        `"${m.id}" has no download source (upstream-routed model). ` +
        `Add an hf_repo + hf_filename on the registry entry to enable pull.`,
      )
      return
    }
    try {
      await npuPull.pullAndWait(m.id)
      await cap.refresh()
    } catch (err) {
      npuToasts.error(`download "${m.id}" failed: ${err?.message ?? err}`)
      return
    }
  }
  // Promote into a real slot — the next /api/backends/npu poll will
  // surface it in `loaded` so the row appears in the serving list.
  try {
    await api('/api/backends/npu/load', {
      method: 'POST',
      body: JSON.stringify({ model_id: m.id }),
    })
  } catch (err) {
    npuToasts.error(`load "${m.id}" failed: ${err?.message ?? err}`)
    return
  }
  addPick.value = ''
  showAdd.value = false
  // Refresh immediately so the user sees the new row without waiting
  // for the 5s backend poll tick.
  refreshNpu()
}

async function unloadSlot(slotName) {
  if (!slotName || busyUnload.value) return
  busyUnload.value = slotName
  try {
    await api('/api/backends/npu/unload', {
      method: 'POST',
      body: JSON.stringify({ slot_name: slotName }),
    })
    refreshNpu()
  } catch (err) {
    npuToasts.error(`unload "${slotName}" failed: ${err?.message ?? err}`)
  } finally {
    busyUnload.value = ''
  }
}

// Group add-options by the upstream capability bucket. The
// `[capability, models]` tuples drive the <optgroup> render below.
const addOptionsGrouped = computed(() => {
  const groups = new Map()
  for (const m of addOptions.value) {
    if (!groups.has(m.capability)) groups.set(m.capability, [])
    groups.get(m.capability).push(m)
  }
  return [...groups.entries()]
})
</script>

<template>
  <div class="bc-card">
    <header class="bc-head">
      <div class="bc-head-l">
        <span class="bc-dot" />
        <h3 class="bc-title">npu</h3>
        <span class="bc-type">backend · {{ snap.hardware }}</span>
      </div>
      <span class="bc-pill">
        {{ serving.length > 1 ? `multiplex · ${serving.length} children` : `${serving.length} child` }}
      </span>
    </header>

    <div v-if="npuError" class="bc-error mono" role="alert">
      backend offline · {{ npuError }}
    </div>

    <div class="bc-bar-block">
      <div class="bc-bar-label">
        <span>Memory</span>
        <span class="mono">
          {{ fmtGb(totalUsedMb) }} / {{ fmtGb(snap.memTotalMb) }} GB
        </span>
      </div>
      <div class="bc-bar" :class="{ warn: memWarn }">
        <div class="bc-bar-fill" :style="{ width: memPct + '%' }" />
      </div>
    </div>

    <section class="bc-serving">
      <div class="bc-serving-head">
        <span class="bc-serving-label">Loaded on NPU</span>
        <span class="bc-serving-sub mono">
          {{ Number(snap.totalReqPerSec || 0).toFixed(1) }} req/s · single execution queue
        </span>
      </div>
      <ul class="bc-serving-list">
        <li v-for="item in serving" :key="item.slotName + ':' + item.modelId">
          <span class="bc-ch-left">
            <span class="bc-ch-cap">{{ item.path.split('.')[1] }}</span>
            <span class="bc-ch-model mono">{{ item.modelId }}</span>
          </span>
          <span class="bc-ch-right">
            <span class="bc-ch-source" :data-source="item.source">
              {{ item.path.split('.')[0] }}
            </span>
            <button
              v-if="item.source === 'slot'"
              type="button"
              class="bc-ch-x"
              :disabled="busyUnload === item.slotName"
              :aria-label="`Unload ${item.modelId}`"
              @click="unloadSlot(item.slotName)"
            >{{ busyUnload === item.slotName ? '…' : '×' }}</button>
          </span>
        </li>
        <li v-if="serving.length === 0" class="bc-empty">
          Nothing loaded. Pick a capability card or use “+ load NPU model”.
        </li>
      </ul>

      <!-- Add row -->
      <div class="bc-add">
        <template v-if="!showAdd">
          <button
            class="bc-add-btn"
            type="button"
            :disabled="addOptions.length === 0"
            @click="showAdd = true"
          >+ load NPU model</button>
          <span v-if="addOptions.length === 0" class="bc-add-empty mono">no models left</span>
        </template>
        <template v-else>
          <select class="bc-add-select mono" v-model="addPick" @keydown.enter="addExtra">
            <option value="" disabled>pick model…</option>
            <optgroup
              v-for="[grp, models] in addOptionsGrouped"
              :key="grp"
              :label="grp.toUpperCase()"
            >
              <option
                v-for="m in models"
                :key="m.id"
                :value="m.id"
              >{{ (m.downloaded !== false ? '◉ ' : (m.pullable !== false ? '⬇ ' : '✕ ')) + m.id }}{{ m.size_gb ? ` — ${m.size_gb} GB` : '' }}</option>
            </optgroup>
          </select>
          <button class="bc-add-confirm" type="button" :disabled="!addPick || npuPull.inFlight.value" @click="addExtra">
            {{ npuPull.inFlight.value ? 'downloading…' : 'load' }}
          </button>
          <button class="bc-add-cancel" type="button" :disabled="npuPull.inFlight.value" @click="showAdd = false; addPick = ''">cancel</button>
        </template>
      </div>
      <div v-if="npuPull.inFlight.value" class="cap-pull">
        <div class="cap-pull-bar"><div class="cap-pull-fill" :style="{ width: (npuPull.pct.value ?? 0) + '%' }" /></div>
        <span class="cap-pull-label mono">
          ↓ {{ npuPull.modelId.value }} · {{ npuPull.pct.value ?? 0 }}% · {{ fmtBytes(npuPull.downloaded.value) }} / {{ fmtBytes(npuPull.total.value) }}
        </span>
        <button class="cap-pull-cancel" type="button" @click="npuPull.cancel()">cancel</button>
      </div>
    </section>

    <button
      class="bc-advanced"
      type="button"
      :aria-expanded="showAdvanced"
      @click="showAdvanced = !showAdvanced"
    >
      <span class="bc-caret" :class="{ open: showAdvanced }">▸</span>
      Advanced
    </button>
    <div v-if="showAdvanced" class="bc-advanced-body">
      <div class="bc-adv-row">
        <span class="bc-adv-label">Driver</span>
        <code class="bc-adv-val mono">{{ snap.driver || '—' }}</code>
      </div>
      <div class="bc-adv-row">
        <span class="bc-adv-label">State</span>
        <span class="bc-adv-val mono">{{ snap.state || '—' }}</span>
      </div>
      <div class="bc-adv-row">
        <span class="bc-adv-label">Memory budget</span>
        <span class="bc-adv-val mono">{{ fmtGb(snap.memTotalMb) }} GB</span>
      </div>
      <div class="bc-adv-row bc-adv-row-toggle">
        <span class="bc-adv-label">Enable as backend</span>
        <label class="bc-switch"><input type="checkbox" checked disabled /><span /></label>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bc-card {
  background: var(--color-surface);
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 22%, var(--color-border));
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  display: flex; flex-direction: column; gap: 14px;
  position: relative;
}
.bc-card::before {
  content: '';
  position: absolute; inset: 0;
  border-radius: var(--radius-lg);
  pointer-events: none;
  box-shadow: inset 0 0 0 1px color-mix(in oklch, var(--hal0-accent) 8%, transparent);
}

.bc-head { display: flex; align-items: center; justify-content: space-between; }
.bc-head-l { display: flex; align-items: center; gap: 10px; }
.bc-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 6px -1px var(--hal0-accent);
}
.bc-title { font-size: 14px; font-weight: 600; color: var(--hal0-accent); margin: 0; text-transform: uppercase; letter-spacing: 0.04em; }
.bc-type { font-family: var(--font-mono); font-size: 10.5px; color: var(--color-fg-faint); }
.bc-pill {
  font-family: var(--font-mono); font-size: 10.5px;
  padding: 2px 8px; border-radius: 999px;
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 50%, transparent);
  background: color-mix(in oklch, var(--hal0-accent) 14%, transparent);
  color: var(--hal0-accent);
}

.bc-error {
  font-size: 10.5px;
  color: var(--color-danger);
  padding: 4px 8px;
  border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent);
  background: color-mix(in oklch, var(--color-danger) 8%, transparent);
  border-radius: var(--radius);
}

.mono { font-family: var(--font-mono); }

.bc-bar-block { display: flex; flex-direction: column; gap: 4px; }
.bc-bar-label { display: flex; justify-content: space-between; font-size: 11px; color: var(--color-fg-muted); }
.bc-bar {
  height: 6px;
  background: var(--hal0-bg-sunken);
  border: 1px solid var(--color-border);
  border-radius: 3px;
  overflow: hidden;
}
.bc-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--hal0-accent), var(--hal0-accent-hover));
  transition: width 0.2s;
}
.bc-bar.warn .bc-bar-fill { background: linear-gradient(90deg, var(--color-warning), var(--color-danger)); }

.bc-serving { display: flex; flex-direction: column; gap: 6px; }
.bc-serving-head { display: flex; align-items: baseline; justify-content: space-between; }
.bc-serving-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.bc-serving-sub { font-family: var(--font-mono); font-size: 10.5px; color: var(--color-fg-faint); }
.bc-serving-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
.bc-serving-list li {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
}
.bc-ch-left { display: inline-flex; align-items: baseline; gap: 8px; min-width: 0; }
.bc-ch-cap {
  font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--color-fg-faint);
  width: 42px;
  flex-shrink: 0;
}
.bc-ch-model { font-size: 11.5px; color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bc-ch-right { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; }
.bc-ch-source {
  font-family: var(--font-mono); font-size: 9.5px;
  padding: 1px 6px; border-radius: 3px;
  background: var(--color-surface-3); color: var(--color-fg-faint);
  border: 1px solid var(--color-border);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.bc-ch-source[data-source="extra"] {
  background: color-mix(in oklch, var(--hal0-accent) 14%, transparent);
  color: var(--hal0-accent);
  border-color: color-mix(in oklch, var(--hal0-accent) 35%, transparent);
}
.bc-ch-x {
  width: 18px; height: 18px;
  border-radius: 50%;
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-faint);
  font-size: 14px; line-height: 1;
  cursor: pointer;
  display: grid; place-items: center;
}
.bc-ch-x:hover { color: var(--color-danger); border-color: color-mix(in oklch, var(--color-danger) 40%, var(--color-border)); }
.bc-empty { color: var(--color-fg-faint); font-style: italic; font-size: 11.5px; padding: 6px 0; }

.bc-add {
  display: flex; align-items: center; gap: 6px;
  margin-top: 2px;
}
.bc-add-btn {
  padding: 5px 10px;
  background: transparent;
  border: 1px dashed color-mix(in oklch, var(--hal0-accent) 40%, transparent);
  color: var(--hal0-accent);
  border-radius: var(--radius);
  font-family: var(--font-mono); font-size: 11px; cursor: pointer;
}
.bc-add-btn:hover:not(:disabled) {
  background: color-mix(in oklch, var(--hal0-accent) 10%, transparent);
  border-color: var(--hal0-accent);
}
.bc-add-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.bc-add-hint {
  font-size: 9.5px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.8;
}
.bc-add-empty { font-size: 10.5px; color: var(--color-fg-faint); }

.bc-add-select {
  flex: 1; min-width: 0;
  padding: 5px 8px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border-hi);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 11.5px;
}
.bc-add-confirm {
  padding: 5px 10px;
  border-radius: var(--radius);
  border: none;
  background: var(--hal0-accent); color: #000;
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  cursor: pointer;
}
.bc-add-confirm:disabled { opacity: 0.4; cursor: not-allowed; }
.bc-add-confirm:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.bc-add-cancel {
  padding: 5px 10px;
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-fg-faint);
  border-radius: var(--radius);
  font-family: var(--font-mono); font-size: 11px; cursor: pointer;
}
.bc-add-cancel:hover { color: var(--color-fg); border-color: var(--color-border-hi); }

.bc-advanced {
  display: flex; align-items: center; gap: 6px;
  background: transparent; border: none; padding: 0;
  color: var(--color-fg-muted);
  font-size: 12px; font-weight: 600; cursor: pointer;
  text-align: left;
}
.bc-advanced:hover { color: var(--color-fg); }
.bc-caret { display: inline-block; transition: transform 0.15s; color: var(--color-fg-faint); font-size: 10px; }
.bc-caret.open { transform: rotate(90deg); }

.bc-advanced-body {
  display: flex; flex-direction: column; gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}
.bc-adv-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 11.5px; }
.bc-adv-label { color: var(--color-fg-faint); }
.bc-adv-val   { color: var(--color-fg-muted); }
.bc-adv-val em { color: var(--color-fg-faint); font-style: normal; }
.bc-adv-row-toggle .bc-adv-label { color: var(--color-fg-muted); }

.bc-switch { position: relative; display: inline-block; width: 30px; height: 16px; }
.bc-switch input { opacity: 0; width: 0; height: 0; }
.bc-switch span {
  position: absolute; cursor: pointer; inset: 0;
  background: var(--color-surface-3);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  transition: 0.15s;
}
.bc-switch span::before {
  content: '';
  position: absolute;
  left: 2px; top: 1px;
  width: 10px; height: 10px;
  border-radius: 50%;
  background: var(--color-fg-faint);
  transition: 0.15s;
}
.bc-switch input:checked + span {
  background: color-mix(in oklch, var(--hal0-accent) 25%, var(--color-surface-3));
  border-color: color-mix(in oklch, var(--hal0-accent) 50%, var(--color-border));
}
.bc-switch input:checked + span::before { transform: translateX(14px); background: var(--hal0-accent); }
</style>
