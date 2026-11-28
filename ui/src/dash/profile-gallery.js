// Profiles gallery — the pure half of the Profiles page (no React, no hooks).
//
// The page shows three kinds of profile on one grid, the way hal0.dev/profiles
// does: the ones hal0 ships ("built in": read-only, the action is clone), the
// ones made on this box ("custom"), and the ones the import catalog offers
// ("import": GET /api/profile-addons). Everything here turns a profile into
// what a card or the detail drawer prints, so the three kinds read the same.

export const KIND_SEED = 'built in';
export const KIND_CUSTOM = 'custom';
export const KIND_IMPORT = 'import';
export const KIND_ORDER = [KIND_SEED, KIND_CUSTOM, KIND_IMPORT];

// hal0's ProfileConfig keys, in the order hal0 documents them.
export const TUNE_KEYS = [
  'flags', 'ctx_size', 'enable_thinking', 'reasoning_effort', 'reasoning_budget',
  'vision', 'speculation', 'runner', 'cloned_from', 'intent',
];

// What each typed field is when a profile does not set it. GET /api/profiles
// returns every field, so "the profile sets this" is "it differs from this".
const UNSET = {
  flags: '', ctx_size: null, enable_thinking: null, reasoning_effort: 'default',
  reasoning_budget: -1, vision: 'auto', speculation: 'auto', runner: null,
  cloned_from: null, intent: '',
};

// 65536 → "64K", 4096 → "4096", 262144 → "256K". Exact multiples of 1024 from
// 16K up only: a short count that is not a round K is easier to read whole.
export function formatContext(tokens) {
  const n = Number(tokens);
  if (!Number.isInteger(n)) return String(tokens);
  return n >= 16384 && n % 1024 === 0 ? `${n / 1024}K` : String(n);
}

// The fields a profile sets, as a plain object in TUNE_KEYS order. `p` is a
// profile from GET /api/profiles or the sparse body of a catalog envelope.
export function tuneOf(p) {
  const body = {};
  for (const key of TUNE_KEYS) {
    const value = p?.[key];
    if (value === undefined || value === null) continue;
    if (value === UNSET[key]) continue;
    body[key] = value;
  }
  return body;
}

// The typed settings a profile makes, as short labels in a fixed order. Flags
// and the intent text are shown on their own, so they are not facts here.
// `runnerTitle` turns a runner key into the name the rest of the dashboard
// uses for it.
export function tuneFacts(p, runnerTitle = (key) => key) {
  const tune = tuneOf(p);
  const facts = [];
  if (tune.ctx_size !== undefined)
    facts.push({ key: 'ctx_size', label: 'context', value: formatContext(tune.ctx_size) });
  if (tune.enable_thinking !== undefined)
    facts.push({ key: 'enable_thinking', label: 'thinking', value: tune.enable_thinking ? 'on' : 'off' });
  if (tune.reasoning_effort !== undefined)
    facts.push({ key: 'reasoning_effort', label: 'effort', value: String(tune.reasoning_effort) });
  if (tune.reasoning_budget !== undefined)
    facts.push({ key: 'reasoning_budget', label: 'reasoning budget', value: String(tune.reasoning_budget) });
  if (tune.vision !== undefined) facts.push({ key: 'vision', label: 'vision', value: String(tune.vision) });
  if (tune.speculation !== undefined)
    facts.push({ key: 'speculation', label: 'speculation', value: String(tune.speculation) });
  if (tune.runner !== undefined)
    facts.push({ key: 'runner', label: 'runner', value: runnerTitle(tune.runner) });
  if (tune.cloned_from !== undefined)
    facts.push({ key: 'cloned_from', label: 'cloned from', value: String(tune.cloned_from) });
  return facts;
}

// What a card says when a profile sets no typed field at all.
export function emptyFactsLabel(p) {
  return tuneOf(p).flags ? 'flags only' : 'engine defaults';
}

// The profile as the table hal0 reads from profiles.toml.
export function profileToml(name, p) {
  const lines = [`[profile.${name}]`];
  for (const [key, value] of Object.entries(tuneOf(p))) {
    lines.push(`${key} = ${typeof value === 'string' ? JSON.stringify(value) : value}`);
  }
  return lines.join('\n');
}

// One card per profile. Local profiles first (built in, then custom), then
// the catalog rows; a quarantined entry is not a card (it has no tune). Within
// a kind the order is the server's: hal0 lists its seeds in a chosen order.
export function galleryCards(profiles, addons) {
  const cards = [];
  for (const p of profiles || []) {
    if (p.quarantined) continue;
    cards.push({
      id: `local:${p.name}`,
      kind: p.seed ? KIND_SEED : KIND_CUSTOM,
      name: p.name,
      family: p.runtime_family || '',
      profile: p,
      addon: null,
    });
  }
  for (const a of addons || []) {
    cards.push({
      id: `addon:${a.name}`,
      kind: KIND_IMPORT,
      name: a.name,
      family: a.runtime_family || '',
      // A blocked row may have no readable body; it still gets a card that
      // says why it is not offered.
      profile: a.profile || {},
      addon: a,
    });
  }
  const rank = (c) => KIND_ORDER.indexOf(c.kind);
  return cards.sort((a, b) => rank(a) - rank(b));
}

export const FACETS = [
  { key: 'kind', label: 'kind', value: (c) => c.kind },
  { key: 'runtime', label: 'runtime', value: (c) => c.family },
];

// Option lists and full-corpus counts per facet, derived from the cards (a
// new runtime family shows up without a code change). `kind` keeps its fixed
// order; the rest sort by name.
export function facetOptions(cards) {
  return FACETS.map((facet) => {
    const counts = new Map();
    for (const c of cards) {
      const v = facet.value(c);
      if (v) counts.set(v, (counts.get(v) || 0) + 1);
    }
    const options = [...counts.keys()].sort((a, b) =>
      facet.key === 'kind' ? KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b) : a.localeCompare(b));
    return { key: facet.key, label: facet.label, options, counts };
  });
}

// AND across facets, OR within one. `selected` maps a facet key to the array
// of values picked for it; an empty or missing array means "all".
export function matchesFilters(card, selected) {
  return FACETS.every((facet) => {
    const picked = selected?.[facet.key];
    return !picked || picked.length === 0 || picked.includes(facet.value(card));
  });
}

// Pick or unpick one value of one facet, returning the new selection.
export function toggleFilter(selected, facetKey, value) {
  const current = selected?.[facetKey] || [];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  return { ...selected, [facetKey]: next };
}
