<script setup>
/**
 * components/settings/SecKey.vue — key/value field for the Lemonade
 * admin section.
 *
 * Renders a labelled input row (text / number / select / checkbox /
 * readonly-with-edit-toggle) with optional restart-required chip and a
 * warning slot for footgun fields like `llamacpp.args`.
 *
 * v-model binds the field value; emits `restart-toggle` when an Edit
 * pencil flips a readonly-by-default field into editable mode (so the
 * parent can render the inline warning text).
 *
 * Props
 * ─────
 *   k           label text
 *   sub         muted sub-label
 *   type        'text' | 'number' | 'select' | 'checkbox' | 'readonly'
 *   options     for type=select
 *   restart     true → render the `⟳` chip next to the input
 *   modelValue  v-model bind
 *   readonly    when true (and type !== 'readonly') the input is
 *               disabled — used for the locked `extra_models_dir`.
 *   testid      data-testid root for the field
 *
 * Slots
 * ─────
 *   warn — destructive guidance rendered below the input when present
 */
import { ref, computed } from 'vue'

const props = defineProps({
  k:          { type: String, required: true },
  sub:        { type: String, default: '' },
  type:       { type: String, default: 'text' },
  options:    { type: Array, default: () => [] },
  restart:    { type: Boolean, default: false },
  modelValue: { type: [String, Number, Boolean], default: '' },
  readonly:   { type: Boolean, default: false },
  step:       { type: String, default: null },
  testid:     { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'edit-toggle'])

// For the `type='readonly'` mode we toggle into an editable input. The
// parent can listen to `edit-toggle(true)` to render the inline footgun
// warning (e.g. llamacpp.args).
const editing = ref(false)
const isReadonlyMode = computed(() => props.type === 'readonly' && !editing.value)

function toggleEdit() {
  editing.value = !editing.value
  emit('edit-toggle', editing.value)
}

function onInput(e) {
  emit('update:modelValue', e.target.value)
}

function onCheck(e) {
  emit('update:modelValue', e.target.checked)
}
</script>

<template>
  <div class="s-row" :data-testid="testid">
    <div class="k">
      <span>{{ k }}</span>
      <span v-if="sub" class="sub">{{ sub }}</span>
    </div>
    <div class="v mono">
      <template v-if="isReadonlyMode">
        <span class="readonly-value" :data-testid="testid ? `${testid}-readonly` : undefined">
          {{ modelValue }}
        </span>
      </template>
      <template v-else-if="type === 'select'">
        <select class="field-input" :value="modelValue" @change="onInput">
          <option v-for="o in options" :key="o" :value="o">{{ o }}</option>
        </select>
      </template>
      <template v-else-if="type === 'checkbox'">
        <label class="toggle-label">
          <input type="checkbox" :checked="modelValue" @change="onCheck" />
          <span>{{ modelValue ? 'Enabled' : 'Disabled' }}</span>
        </label>
      </template>
      <template v-else-if="type === 'number'">
        <input
          class="field-input"
          type="number"
          :step="step"
          :value="modelValue"
          :readonly="readonly"
          @input="onInput"
        />
      </template>
      <template v-else>
        <input
          class="field-input"
          type="text"
          :value="modelValue"
          :readonly="readonly"
          @input="onInput"
        />
      </template>
      <div v-if="$slots.warn && (editing || type !== 'readonly')" class="warn">
        <slot name="warn" />
      </div>
    </div>
    <div class="ac">
      <slot name="actions" />
      <button
        v-if="type === 'readonly'"
        type="button"
        class="btn-ghost-sm"
        :data-testid="testid ? `${testid}-edit-toggle` : undefined"
        @click="toggleEdit"
      >
        {{ editing ? 'Done' : 'Edit' }}
      </button>
      <span
        v-if="restart"
        class="restart-chip"
        title="Requires lemond restart"
      >⟳ restart</span>
    </div>
  </div>
</template>

<style scoped>
.s-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(200px, 1.5fr) auto;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line-soft, var(--color-border));
  align-items: center;
}
.s-row:last-child { border-bottom: none; }
.k {
  font-size: 13px;
  color: var(--fg, var(--color-fg));
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.k .sub {
  font-family: var(--geist, inherit);
  font-size: 11.5px;
  color: var(--fg-4, var(--color-fg-faint));
  font-weight: 400;
}
.v {
  font-size: 12.5px;
  color: var(--fg-2, var(--color-fg-muted));
  min-width: 0;
}
.field-input {
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line, var(--color-border));
  border-radius: var(--rad-sm, 4px);
  padding: 6px 10px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 12.5px;
  color: var(--fg, var(--color-fg));
  width: 100%;
  max-width: 320px;
  box-sizing: border-box;
}
.readonly-value {
  display: inline-block;
  padding: 6px 10px;
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line-soft, var(--color-border));
  border-radius: var(--rad-sm, 4px);
  color: var(--fg-2, var(--color-fg-muted));
  font-family: var(--jbm, var(--font-mono));
}
.toggle-label { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
.warn {
  margin-top: 6px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  color: var(--err, var(--color-danger));
  line-height: 1.45;
  max-width: 360px;
}
.ac { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.btn-ghost-sm {
  border: 1px solid var(--line, var(--color-border));
  background: transparent;
  color: var(--fg-2, var(--color-fg-muted));
  border-radius: var(--rad-sm, 4px);
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}
.btn-ghost-sm:hover { color: var(--fg, var(--color-fg)); border-color: var(--line-strong, var(--color-border-hi)); }
.restart-chip {
  font-family: var(--jbm, var(--font-mono));
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--warn, var(--color-warning));
  border: 1px solid color-mix(in srgb, var(--warn, var(--color-warning)) 35%, transparent);
  background: color-mix(in srgb, var(--warn, var(--color-warning)) 12%, transparent);
}
@media (max-width: 720px) {
  .s-row { grid-template-columns: 1fr; }
  .ac { justify-content: flex-start; }
}
</style>
