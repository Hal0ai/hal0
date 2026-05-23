<script setup>
/**
 * components/settings/RotateTokenDialog.vue — thin wrapper over the
 * primitive ConfirmDialog for the "Rotate API token" path in the Auth
 * section.
 *
 * Recoverable confirm (not destructive — operators can re-issue if they
 * lose track of which scripts saw the old token), but worth a confirm
 * because revocation is immediate.
 */
import ConfirmDialog from '../primitives/ConfirmDialog.vue'

defineProps({
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['cancel', 'confirm'])

function onCancel() { emit('cancel') }
function onConfirm() { emit('confirm') }
</script>

<template>
  <ConfirmDialog
    :open="open"
    :on-cancel="onCancel"
    :on-confirm="onConfirm"
    title="Rotate API token?"
    message="Scripts and agents using the old token will need to be re-authorized. The new token is shown once after rotation — copy it before closing the dialog."
    confirm-label="Rotate token"
    cancel-label="Cancel"
  />
</template>
