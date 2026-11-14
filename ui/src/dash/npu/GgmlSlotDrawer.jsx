// The drawer shell around the GGML slot form, opened from the NPU tab's GGML lane (create or edit).
// The Slots page's edit drawer wraps the same form in its own chrome (slot-modals.jsx).
import { GgmlSlotForm } from './GgmlSlotForm.jsx'

export function GgmlSlotDrawer({ open, mode, slot, initialModel, onClose }) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      eyebrow={mode === 'create' ? 'NPU · GGML' : `NPU · GGML · ${slot?.name ?? ''}`}
      title={mode === 'create' ? 'New GGML slot' : slot?.name}
      width={560}
    >
      {open && <GgmlSlotForm mode={mode} slot={slot} initialModel={initialModel} onDone={onClose} />}
    </Drawer>
  )
}
