// hal0 dashboard — Delete-slot dialog (D2, extracted from EditSlotDrawer).
//
// The confirm states the TRUE blast radius: the unit is removed, the port is
// released back to PortAuthority, and the slot state is deleted — while the
// model and its weights are explicitly untouched (a slot is a pure instance
// over a shared, refcounted model). Type-to-confirm the slot name.

import { useSlotDelete } from '@/api/hooks/useSlots'

function DeleteSlotDialog({ open, slot, onClose, onDeleted }) {
  const del = useSlotDelete();
  if (!open || !slot) return null;

  const slotId = slot.id != null ? slot.id : slot.slot_id;
  const port = slot.port;
  const modelLabel = slot.modelLong || slot.model || "its model";

  const onConfirm = async () => {
    try {
      await del.mutateAsync(slot.name);
      window.__hal0Toast && window.__hal0Toast(`Slot "${slot.name}" deleted`, "ok");
      onDeleted && onDeleted();
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Delete failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <ConfirmDialog
      open={open}
      onCancel={onClose}
      onConfirm={onConfirm}
      destructive
      typeToConfirm={slot.name}
      confirmLabel={del.isPending ? "Deleting…" : "Delete slot"}
      title={`Delete slot "${slot.name}"?`}
      message={
        <span data-testid="delete-slot-blast">
          Deleting <span className="mono" style={{ fontSize: 11 }}>{slot.name}</span>
          {slotId != null && <span style={{ color: "var(--fg-5)" }}> (#{slotId})</span>} will:
          <span style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 11, fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.5 }}>
            <span style={{ display: "flex", gap: 8 }}><span style={{ color: "var(--err)" }}>−</span><span style={{ color: "var(--fg-2)" }}>remove the unit <span style={{ color: "var(--fg)" }}>hal0-slot@{slot.name}.service</span></span></span>
            <span style={{ display: "flex", gap: 8 }}><span style={{ color: "var(--err)" }}>−</span><span style={{ color: "var(--fg-2)" }}>release port <span style={{ color: "var(--fg)" }}>{port != null ? `:${port}` : "(none)"}</span> back to PortAuthority</span></span>
            <span style={{ display: "flex", gap: 8 }}><span style={{ color: "var(--err)" }}>−</span><span style={{ color: "var(--fg-2)" }}>delete slot state <span style={{ color: "var(--fg)" }}>/var/lib/hal0/slots/{slot.name}/</span></span></span>
            <span style={{ display: "flex", gap: 8 }}><span style={{ color: "var(--ok)" }}>✓</span><span style={{ color: "var(--fg-3)" }}>the model <b style={{ color: "var(--fg-2)" }}>{modelLabel}</b> &amp; its weights are untouched</span></span>
          </span>
        </span>
      }
    />
  );
}

Object.assign(window, { DeleteSlotDialog });
