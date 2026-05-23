<script setup>
/**
 * components/settings/SaveAndRestartDialog.vue — Lemonade admin
 * save+restart confirm. Surfaces the outage window estimate verbatim
 * from the brief so operators understand the impact before they hit
 * "Save + restart".
 *
 * Props
 * ─────
 *   open            : boolean
 *   pendingRestart  : count of fields whose change needs a lemond restart
 *
 * Emits
 * ─────
 *   cancel  / confirm
 */
import ConfirmDialog from '../primitives/ConfirmDialog.vue'
import { computed } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  pendingRestart: { type: Number, default: 0 },
})

const emit = defineEmits(['cancel', 'confirm'])

const msg = computed(() => {
  const n = props.pendingRestart
  const head = n
    ? `${n} change${n === 1 ? '' : 's'} require a lemond restart.`
    : 'Save changes? Some take effect immediately, others apply at next model load.'
  return n
    ? `${head} Save and restart? lemond will be unavailable for ~8-12 seconds; in-flight inference fails and the dashboard reconnects automatically.`
    : head
})

function onCancel() { emit('cancel') }
function onConfirm() { emit('confirm') }
</script>

<template>
  <ConfirmDialog
    :open="open"
    :on-cancel="onCancel"
    :on-confirm="onConfirm"
    title="Save and restart lemond?"
    :message="msg"
    confirm-label="Save + restart"
    cancel-label="Cancel"
  />
</template>
