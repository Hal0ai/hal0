<script setup>
/**
 * DeleteModelDialog.vue — destructive delete-model confirm.
 *
 * Mirrors React `DeleteModelDialog` in
 *   /tmp/hal0-design/hal0-v2/project/dash/model-modals.jsx
 *
 * Three branches:
 *   - default: standard destructive confirm, no type-to-confirm.
 *   - slots reference the model: warn-soft block listing slot names +
 *     require type-to-confirm (model.id).
 *   - omni-collection: special copy — "only the collection entry will be
 *     removed; component models stay on disk".
 *
 * Wraps the Modal primitive directly (rather than ConfirmDialog) because
 * we need rich body content with embedded warn block and conditional
 * type-to-confirm — ConfirmDialog only accepts a flat string message.
 */
import { ref, computed, watch } from 'vue'
import Modal from '../primitives/Modal.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  model: { type: Object, default: null },
  /** Slot rows from useSystemStore — used to discover what uses this model */
  slots: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'confirm'])

const typed = ref('')
watch(() => props.open, (v) => { if (v) typed.value = '' })

const isOmni = computed(() => props.model?.type === 'omni' || props.model?.collection === true)

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

const hasUsers = computed(() => slotsUsing.value.length > 0)

const requireType = computed(() => hasUsers.value && !isOmni.value)

const canConfirm = computed(() => !requireType.value || typed.value === props.model?.id)

function onCancel() {
  emit('close')
}

function onConfirm() {
  if (!canConfirm.value) return
  emit('confirm', props.model)
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="onCancel"
    eyebrow="Destructive · cannot be undone"
    :title="model ? `Delete ${model.longName || model.id}?` : 'Delete model?'"
    :width="540"
  >
    <div v-if="model" class="del-body">
      <p class="del-lead">
        This removes
        <span class="mono">{{ model.size || '—' }}</span>
        from
        <span class="mono">/var/lib/hal0/models</span>.
      </p>

      <div v-if="isOmni" class="omni-note mono">
        Only the collection entry will be removed; component models stay on disk.
      </div>

      <div v-if="hasUsers && !isOmni" class="warn-block mono" data-test="del-slots-warn">
        <strong>⚠ {{ slotsUsing.length }} slot{{ slotsUsing.length > 1 ? 's' : '' }} reference this model:</strong>
        <span class="slot-names">
          <span v-for="s in slotsUsing" :key="s.name" class="slot-chip">{{ s.name }}</span>
        </span>
        <div class="warn-foot">
          They'll move to <span class="mono">empty</span> state. Re-configure with a different model first if you need them live.
        </div>
      </div>

      <div v-if="requireType" class="del-input-block">
        <label class="del-input-lbl mono">
          Type <span class="del-token">{{ model.id }}</span> to confirm:
        </label>
        <input
          v-model="typed"
          class="input mono del-input"
          type="text"
          :placeholder="model.id"
          autocomplete="off"
          spellcheck="false"
          data-test="del-type-confirm"
        />
      </div>
    </div>

    <template #foot>
      <span class="foot-note">This action is permanent.</span>
      <span class="foot-actions">
        <button type="button" class="btn ghost sm" @click="onCancel">Cancel</button>
        <button
          type="button"
          class="btn sm del-confirm"
          :disabled="!canConfirm"
          data-test="del-confirm"
          @click="onConfirm"
        >Delete model</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.del-body { display: flex; flex-direction: column; gap: 12px; }
.del-lead {
  font-size: 13px;
  color: var(--fg-2);
  line-height: 1.6;
  margin: 0;
}
.omni-note {
  padding: 10px 12px;
  background: var(--info-soft);
  border: 1px solid var(--info-line);
  border-radius: var(--rad-sm);
  color: var(--info);
  font-size: 11.5px;
}
.warn-block {
  padding: 10px 12px;
  background: var(--warn-soft);
  border: 1px solid var(--warn-line);
  border-radius: var(--rad-sm);
  color: var(--warn);
  font-size: 11.5px;
  line-height: 1.55;
}
.warn-block strong { color: var(--warn); }
.slot-names { display: inline-flex; flex-wrap: wrap; gap: 4px; margin-left: 6px; }
.slot-chip {
  padding: 2px 6px;
  background: var(--bg);
  border: 1px solid var(--warn-line);
  border-radius: 3px;
  font-size: 10.5px;
  color: var(--warn);
}
.warn-foot { margin-top: 8px; font-size: 11px; color: var(--fg-3); }

.del-input-block { display: flex; flex-direction: column; gap: 6px; }
.del-input-lbl { font-size: 11px; color: var(--fg-4); }
.del-token { color: var(--err); }
.del-input { width: 100%; box-sizing: border-box; }

.foot-note { color: var(--fg-4); }
.foot-actions { display: inline-flex; gap: 8px; }

.del-confirm {
  background: var(--err);
  border-color: var(--err);
  color: #0a0a0a;
}
.del-confirm:hover { filter: brightness(1.06); }
.del-confirm[disabled] {
  background: transparent;
  border-color: var(--line);
  color: var(--fg-4);
}
</style>
