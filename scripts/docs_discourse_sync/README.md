# docs -> Discourse sync

Publishes `docs/{getting-started,concepts,guides,operate,reference[/api]}`
(the same set `.github/workflows/mirror-docs.yml` mirrors into hal0-web)
one-way into Discourse "Doc Categories" topics at forum.hal0.dev. `docs/`
stays the canonical source; forum topics are the generated copy — never
hand-edit a synced topic, the next sync overwrites it.

Modeled on discourse/discourse-developer-docs' own `sync_docs` script: a
per-file `external_id` (derived from the file's path, e.g.
`hal0-docs/getting-started/install`) is the idempotent create/update key,
`GET /t/external_id/{id}.json` resolves it, and a topic's content is only
touched when it actually differs.

## How it works

1. **`transform.py`** — MDX body -> Discourse markdown. Strips
   import/export and JSX comments, converts Starlight asides / Tabs /
   Cards / Steps to Discourse-native constructs, and fails loudly
   (file+line) on anything it doesn't recognize rather than silently
   dropping content. Fenced code blocks and inline code spans are never
   touched by any of this.
2. **`discovery.py`** — walks `docs/`, derives each file's `external_id`,
   section/subsection, and old site path, and runs the transform.
3. **`discourse_client.py`** — the admin API client: resolve/create/update
   topics, upload images, throttled to a configurable requests/minute.
4. **`sync.py`** — pass 1 ensures every doc has a topic (creating new ones
   with links not yet rewritten); pass 2 rewrites internal cross-links
   now that every topic has a real URL, then settles each doc's *final*
   content against the server. This ordering matters for idempotency —
   see the module docstring if you're touching it.
5. **`index_topics.py`** — one synthetic index topic per section for the
   discourse-doc-categories sidebar.
6. **`redirect_map.py`** — old `/docs/<section>/<slug>/` path -> new
   forum topic URL, written as JSON for hal0-web's eventual 301 layer.

## Running it

```sh
# Offline structural check — no network, no credentials. Discovers +
# transforms every doc; a bad doc fails with file:line, not a stack trace.
uv run python -m scripts.docs_discourse_sync.cli --check

# Plan a sync against the real forum without writing anything.
DISCOURSE_URL=https://forum.hal0.dev \
DISCOURSE_API_KEY=... DISCOURSE_API_USERNAME=... DOCS_CATEGORY_ID=7 \
uv run python -m scripts.docs_discourse_sync.cli --dry-run \
  --assets-root ../hal0-web/public

# The real thing (same env, minus --dry-run).
```

`--assets-root` should point at a hal0-web checkout's `public/` —
screenshots the docs reference (`/screenshots/*.png`) live there, not in
this repo. Without it (or if a specific file is missing), that image
falls back to linking `https://hal0.dev/screenshots/...` instead of
uploading, logged as a warning rather than failing the run.

## Tests

```sh
HAL0_HOME=$(mktemp -d) uv run pytest tests/scripts/docs_discourse_sync -v
```

No live HTTP anywhere — `discourse_client.py` is exercised entirely
through `httpx.MockTransport`. `test_transform.py` includes a regression
test that runs the transform over every real file in `docs/`, which is
what actually caught the fence-indentation, `<Card>`/`<CardGrid>` regex
collision, and header/brace-scan-ordering bugs found while building this
— fixture-only tests would not have.

## GitHub Action

`.github/workflows/sync-docs-discourse.yml` runs this on every push to
`main` that touches `docs/**`. It skips itself (logged, not a failure) if
`DISCOURSE_DOCS_API_KEY` isn't configured yet — see the comment block at
the top of that file for the full secret list and one-time setup.
