// Normalise a board "actor" (assignee or profile) into a shape the UI can
// safely render. /api/board/{profiles,assignees} are proxied to Hermes, which
// returns actors keyed by `name` (e.g. {name, on_disk, counts}). The board UI
// reads `.id`/`.label`; without this adapter its `?? raw` fallbacks bottom out
// at the raw object and React error #31 whites out the whole dashboard.
//
// Guarantee: the returned `id` and `label` are ALWAYS primitives, so a raw
// object can never reach a JSX child position.

function pickName(raw) {
  for (const k of ["id", "name", "label", "slug"]) {
    const v = raw[k];
    if (typeof v === "string" && v) return v;
    if (typeof v === "number") return String(v);
  }
  return "unknown";
}

/** Normalise any actor (object or bare string) to {id, label, ...rest}. */
export function normaliseActor(raw) {
  if (raw === null || raw === undefined) return { id: "unknown", label: "unknown" };
  if (typeof raw === "string" || typeof raw === "number") {
    const s = String(raw);
    return { id: s, label: s };
  }
  if (typeof raw !== "object") {
    const s = String(raw);
    return { id: s, label: s };
  }
  const id = pickName(raw);
  const label =
    typeof raw.label === "string" && raw.label
      ? raw.label
      : typeof raw.name === "string" && raw.name
        ? raw.name
        : id;
  return { ...raw, id, label };
}

export const normaliseAssignee = normaliseActor;
export const normaliseProfile = normaliseActor;
