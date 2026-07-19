// hal0 dashboard — Rename-slot dialog (D2, greenfield).
//
// The slot name is a mutable DISPLAY LABEL; identity is the stable numeric
// slot_id, which a rename never touches (POST /api/slots/{name}/rename preserves
// it). But the systemd unit is still name-keyed, so rename requires the slot
// OFFLINE until the live-rename migration lands — surfaced here as an inline
// disabled-with-reason (never a bare tooltip), honestly.

import { useSlotRename } from '@/api/hooks/useSlots'
import { slotButtonPhase } from '../slot-status.js'

const { useState: useStateR, useEffect: useEffectR } = React;

const NAME_RE = /^[a-z][a-z0-9-]{0,30}$/;

function RenameSlotDialog({ open, slot, onClose }) {
  const rename = useSlotRename();
  const [value, setValue] = useStateR("");
  const [err, setErr] = useStateR(null);

  // Seed from the slot's name only when the dialog opens or the slot identity
  // changes — NOT on every slot object re-reference (the detail poll hands us a
  // fresh object every 2.5s, which would otherwise wipe what the user typed).
  useEffectR(() => {
    if (open && slot) { setValue(slot.name || ""); setErr(null); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, slot?.name]);

  if (!open || !slot) return null;

  // Rename needs the unit offline — anything but a fully stopped slot is gated.
  const running = slotButtonPhase(slot) !== "off";
  const slotId = slot.id != null ? slot.id : slot.slot_id;
  const nextInvalid = value && !NAME_RE.test(value);
  const unchanged = value === slot.name;
  const canSave = !running && !!value && !nextInvalid && !unchanged && !rename.isPending;

  const onSave = async () => {
    setErr(null);
    try {
      await rename.mutateAsync({ name: slot.name, new_name: value });
      window.__hal0Toast && window.__hal0Toast(`Renamed to ${value}`, "ok");
      onClose();
    } catch (e) {
      setErr(e?.message || "rename failed");
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Rename slot · display label"
      title={<span>{slot.name} {slotId != null && <span style={{ color: "var(--fg-5)", fontSize: 11 }}>#{slotId}</span>}</span>}
      width={460}
      foot={
        <>
          <span />
          <span style={{ display: "inline-flex", gap: 8 }}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" data-testid="rename-slot-save" onClick={onSave} disabled={!canSave}>
              {rename.isPending ? "Saving…" : "Save name"}
            </button>
          </span>
        </>
      }
    >
      <input
        className="input mono"
        data-testid="rename-slot-input"
        value={value}
        disabled={running}
        onChange={(e) => setValue(e.target.value)}
        placeholder={slot.name}
        style={running ? { opacity: 0.6 } : undefined}
      />
      {nextInvalid && <div className="err" style={{ marginTop: 6 }}>lowercase + dashes only</div>}
      {err && <div className="err" style={{ marginTop: 6 }}>{err}</div>}
      {running && (
        <div data-testid="rename-slot-reason" style={{ marginTop: 11, border: "1px solid var(--warn-line)", background: "var(--warn-soft)", borderRadius: 6, padding: "10px 12px", display: "flex", gap: 9, alignItems: "flex-start" }}>
          <span style={{ color: "var(--warn)", fontSize: 12 }}>⚠</span>
          <div style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--fg-2)" }}>
            <b style={{ color: "var(--fg)" }}>Stop the slot to rename.</b> Rename requires the unit offline — it
            re-templates <span className="mono" style={{ fontSize: 10 }}>hal0-slot@{slot.name}</span>. The stable
            <span className="mono" style={{ fontSize: 10 }}> slot_id{slotId != null ? ` #${slotId}` : ""}</span> never
            changes; the API and your clients keep working.{" "}
            <span style={{ color: "var(--fg-4)" }}>(live-rename lands in a later migration)</span>
          </div>
        </div>
      )}
    </Modal>
  );
}

Object.assign(window, { RenameSlotDialog });
