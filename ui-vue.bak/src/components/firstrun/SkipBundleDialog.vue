<script setup>
/**
 * SkipBundleDialog — confirm modal for the picker's "Skip" path.
 *
 * Wraps `primitives/ConfirmDialog.vue` with copy mirroring
 *   /tmp/hal0-design/hal0-v2/project/dash/flow-modals.jsx (lines 9-28).
 */
import ConfirmDialog from '../primitives/ConfirmDialog.vue'

defineProps({
  open:      { type: Boolean, default: false },
  onCancel:  { type: Function, default: () => {} },
  onConfirm: { type: Function, default: () => {} },
})
</script>

<template>
  <ConfirmDialog
    :open="open"
    :on-cancel="onCancel"
    :on-confirm="onConfirm"
    title="Skip the bundle picker?"
    message="You'll land on the dashboard with no models loaded. The six seeded slots (primary, embed, rerank, stt, tts, img) will show Configure buttons. You can run the picker again later from Settings → Run bundle picker again."
    confirm-label="Skip and configure manually"
    cancel-label="Cancel"
  />
</template>
