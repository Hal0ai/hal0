# Dashboard plugin "Could not load this plugin's script"

Symptom: a dashboard tab for a plugin (e.g., kanban) shows an error overlay:
"Could not load this plugin's script. Check the Network tab (dashboard-plugins/…)
and the server's plugin path."

## Root cause: `entry` field mismatch

The dashboard plugin system in `web_server.py` discovers plugins from
`plugins/<name>/dashboard/manifest.json`. The `entry` field tells the
frontend SPA which JS bundle to dynamically import to render the tab:

```python
# web_server.py:3973
"entry": data.get("entry", "dist/index.js"),
```

**When `entry` is absent from the manifest, it defaults to `"dist/index.js"`**.
If that file doesn't exist in the plugin's `dashboard/` directory, the
browser hits a 404 on `/dashboard-plugins/<name>/dist/index.js` and
shows the error.

**The common case**: the plugin's React components are already built
into the main dashboard JS bundle (`web_dist/assets/index-*.js`).
The `tab` declaration in the manifest creates a route that the frontend
tries to populate by loading an external JS file via the `entry` field.
Since the component is built-in, no external JS is needed — but the
frontend loader has no empty-entry guard, so removing the `tab` entirely
is the correct local fix.

## Diagnostic procedure

**Important**: the dashboard runs as a separate process (`hermes dashboard --port 9119`).
Plugin discovery is cached per-process — after editing a manifest, you may need to
restart the dashboard process (not just rescan via API) for changes to take effect.

1. **Query the resolved plugin manifest** (shows the *effective* `entry`):
   ```bash
   curl -s http://127.0.0.1:9119/api/dashboard/plugins | python3 -m json.tool
   ```
   Look at the `entry` field. If it says `"dist/index.js"` and you
   didn't explicitly set that, the default is in play.

2. **Confirm the entry file is missing**:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" \
     http://127.0.0.1:9119/dashboard-plugins/<name>/dist/index.js
   ```
   `404` confirms the file doesn't exist.

3. **Check whether the plugin has the component built into the main JS**.
   The dashboard is built once per release into the release checkout
   (`/var/lib/hal0/hermes/current/src/`); a host not yet migrated to the
   release layout has it under the venv's `lib/python3.X/site-packages/`:
   ```bash
   grep -oP '.{0,80}<plugin_name>.{0,80}' \
     /var/lib/hal0/hermes/current/src/hermes_cli/web_dist/assets/index-*.js
   ```
   If you see UI strings like `"Loading <Plugin> board…"`,
   `"Failed to load <Plugin> board"`, or `renderingError` in multiple
   languages, the component IS built in — the tab just needs to be
   removed from the manifest (Option A) or the package upgraded (Option B).

4. **Check what's actually in the plugin dashboard directory**:
   ```bash
   ls -la /var/lib/hal0/hermes/current/src/plugins/<name>/dashboard/
   ```
   If you see `manifest.json` + `plugin_api.py` but no `dist/` and no
   JS files, the frontend was never packaged.

## Fix

### Option A: Remove the `tab` (recommended for API-only plugins)

If the plugin has no JS frontend and serves only backend API routes
(via `plugin_api.py`), remove the `tab` declaration entirely from
`manifest.json`. The backend routes at `/api/plugins/<name>/` still work;
the gateway dispatcher still runs; CLI commands still function. Only the
dashboard sidebar tab is removed.

```json
{
  "name": "kanban",
  "label": "Kanban",
  "description": "…",
  "icon": "LayoutKanban",
  "version": "0.3.2",
  "api": "plugin_api.py"
}
```

### Option B: Move to a newer vetted hermes-agent commit

hal0 pins hermes-agent to a reviewed commit and never pip-upgrades it in
place. If the missing JS bundle is fixed upstream, the fix reaches the box
when hal0's pin moves to a commit that has it. An operator can build a
different vetted commit (one already listed in hal0's `VETTED_HERMES_REFS`)
with:

```bash
sudo hal0 update component hermes --target <40-hex sha>
```

That builds a fresh release with its own checkout, plugin files and
dashboard, and switches to it. The override persists across `hal0 update`
(`hal0 update status` shows the row as overridden) until you return to the pin
with `sudo hal0 update component hermes --clear-override`.

### Why `"entry": ""` does NOT work

Setting `"entry": ""` in the manifest does **not** fix this error. The
dashboard frontend's plugin loader (`xI()` in the main JS bundle) has
**no guard for empty `entry`** — it unconditionally creates a
`<script>` tag:

```javascript
const m = `${base}/dashboard-plugins/${f.name}/${f.entry}`;
const v = document.createElement("script");
v.src = m;   // → /dashboard-plugins/kanban/  (404)
v.onerror = () => { jk(f.name, "LOAD_FAILED"); };
```

When `entry` is `""`, the URL becomes `/dashboard-plugins/<name>/`
which still 404s. The script `onerror` fires and sets `LOAD_FAILED`,
producing the same error message. The frontend would need an
empty-entry skip (e.g. `if (!f.entry) return;`) which does not exist
in any shipped version.

## Why this happens

The hermes pip package ships some plugins with built-in React components
(in the main `web_dist/` bundle — you can grep for UI strings like
`"Loading Kanban board…"` in multiple languages to confirm) but their
`manifest.json` doesn't declare `"entry": ""` AND the frontend loader
has no empty-entry guard. This is a packaging gap in the upstream
release. The fix is either removing the tab or upgrading to a version
that ships the plugin's JS bundle.
