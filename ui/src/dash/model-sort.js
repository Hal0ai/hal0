// Pure sort / tag-filter helpers for the Models catalog toolbar (WS-13).
//
// Kept dependency-free so it runs under plain
// `node ui/src/dash/__tests__/model-sort.test.mjs` — no bundler, no React.
//
// Sorting is applied WITHIN each catalog section (installed / blessed /
// user.* / upstream) — the section structure itself never reorders.

/** Sort fields offered by the toolbar dropdown, presentation order. */
export const MODEL_SORT_FIELDS = [
  { id: "name", label: "name" },
  { id: "size", label: "size" },
  { id: "params", label: "params" },
  { id: "added", label: "added" },
];

/**
 * Parse a human param-count ("27B", "350M", "1.5B", "74K") — or a raw
 * number — into a comparable count. Returns null when unparseable.
 *
 * @param {unknown} v
 * @returns {number|null}
 */
export function parseParamCount(v) {
  if (typeof v === "number" && isFinite(v)) return v;
  if (typeof v !== "string") return null;
  const m = v.trim().match(/^([\d.]+)\s*([kmbt])?b?$/i);
  if (!m) return null;
  const n = parseFloat(m[1]);
  if (!isFinite(n)) return null;
  const mult = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 }[(m[2] || "b").toLowerCase()] || 1e9;
  return n * mult;
}

/**
 * Size in bytes for a model row: `size_bytes` when present, else parse the
 * legacy pre-formatted `size` string ("18.8 GB"). Null when unknown.
 *
 * @param {{size_bytes?: number, size?: string}} m
 * @returns {number|null}
 */
export function modelSizeBytes(m) {
  if (typeof m?.size_bytes === "number" && m.size_bytes > 0) return m.size_bytes;
  if (typeof m?.size === "string") {
    const p = m.size.trim().match(/^([\d.]+)\s*(b|kb|mb|gb|tb)$/i);
    if (p) {
      const mult = { b: 1, kb: 1024, mb: 1024 ** 2, gb: 1024 ** 3, tb: 1024 ** 4 }[
        p[2].toLowerCase()
      ];
      return parseFloat(p[1]) * mult;
    }
  }
  return null;
}

/**
 * Comparable key for one model row under a sort field.
 * Strings for `name`, numbers (or null = unknown) for the rest.
 *
 * @param {any} m
 * @param {"name"|"size"|"params"|"added"} field
 * @returns {string|number|null}
 */
export function modelSortKey(m, field) {
  if (field === "name") {
    return String(m?.longName || m?.name || m?.id || "").toLowerCase();
  }
  if (field === "size") return modelSizeBytes(m);
  if (field === "params") return parseParamCount(m?.params);
  if (field === "added") {
    return typeof m?.created === "number" && m.created > 0 ? m.created : null;
  }
  return null;
}

/**
 * Stable sort of catalog rows by field + direction. Rows with an unknown
 * key always sink to the end regardless of direction, so "sort by params"
 * doesn't shove every registry row (no params) above real values on desc.
 *
 * @param {any[]} rows
 * @param {"name"|"size"|"params"|"added"} field
 * @param {"asc"|"desc"} dir
 * @returns {any[]} a new array; input untouched
 */
export function sortModels(rows, field, dir = "asc") {
  const list = Array.isArray(rows) ? [...rows] : [];
  const sign = dir === "desc" ? -1 : 1;
  return list
    .map((m, i) => ({ m, i }))
    .sort((a, b) => {
      const ka = modelSortKey(a.m, field);
      const kb = modelSortKey(b.m, field);
      const aMiss = ka === null || ka === undefined || ka === "";
      const bMiss = kb === null || kb === undefined || kb === "";
      if (aMiss && bMiss) return a.i - b.i;
      if (aMiss) return 1;
      if (bMiss) return -1;
      if (ka < kb) return -1 * sign;
      if (ka > kb) return 1 * sign;
      return a.i - b.i; // stable
    })
    .map((x) => x.m);
}

/**
 * Tag chips worth rendering: the curated vocabulary (in its canonical
 * order) intersected with the tags actually present on the given rows, so
 * the toolbar never renders a 35-chip wall of dead filters.
 *
 * @param {any[]} rows
 * @param {string[]} curatedTags meta.curated_model_tags
 * @returns {string[]}
 */
export function tagChipsFor(rows, curatedTags) {
  const present = new Set();
  for (const m of Array.isArray(rows) ? rows : []) {
    for (const t of Array.isArray(m?.tags) ? m.tags : []) present.add(t);
  }
  return (Array.isArray(curatedTags) ? curatedTags : []).filter((t) => present.has(t));
}

/**
 * True when the model carries EVERY selected tag (AND semantics — chips
 * narrow). No selection matches everything.
 *
 * @param {any} m
 * @param {string[]} selected
 * @returns {boolean}
 */
export function modelMatchesTags(m, selected) {
  const sel = Array.isArray(selected) ? selected : [];
  if (sel.length === 0) return true;
  const tags = new Set(Array.isArray(m?.tags) ? m.tags : []);
  return sel.every((t) => tags.has(t));
}

/**
 * Short human "added" label from a unix-seconds timestamp: relative for
 * the last month ("today", "3d ago"), short date beyond. "—" when unknown.
 *
 * @param {number|undefined|null} created unix seconds
 * @param {number} [nowS] injectable clock for tests
 * @returns {string}
 */
export function fmtAdded(created, nowS = Date.now() / 1000) {
  if (typeof created !== "number" || !(created > 0)) return "—";
  const d = nowS - created;
  if (d < 0) return "—";
  if (d < 86400) return "today";
  const days = Math.floor(d / 86400);
  if (days < 31) return `${days}d ago`;
  const dt = new Date(created * 1000);
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
    dt.getUTCMonth()
  ];
  return `${mon} ${dt.getUTCFullYear()}`;
}
