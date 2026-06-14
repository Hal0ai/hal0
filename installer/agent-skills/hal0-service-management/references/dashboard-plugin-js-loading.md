# Dashboard Plugin JS Loading Internals

Reverse-engineered from the minified dashboard bundle (hermes 0.14.0,
`web_dist/assets/index-Cf3a3-pl.js`). Understanding this saves hours when
a plugin tab shows "Could not load this plugin's script."

## Architecture

### Plugin discovery (server-side)

`_discover_dashboard_plugins()` in `web_server.py` scans three sources:
1. `~/.hermes/plugins/<name>/dashboard/manifest.json` (user)
2. `<venv>/plugins/<name>/dashboard/manifest.json` (bundled)
3. `.hermes/plugins/` (project, if `HERMES_ENABLE_PROJECT_PLUGINS`)

Each discovered plugin gets these fields (among others):
- `entry`: `data.get("entry", "dist/index.js")` — defaults to `dist/index.js`
- `tab`: path, position, override, hidden
- `_dir`: absolute path to the plugin's `dashboard/` directory

### Plugin API endpoint

```
GET /api/dashboard/plugins
```
Returns the plugin list (internal `_dir` and `_api_file` fields stripped).

### Static asset serving

```
GET /dashboard-plugins/{plugin_name}/{file_path}
```
Serves files from `{plugin._dir}/{file_path}`. Path-traversal protected.

### Plugin rescan

```
GET /api/dashboard/plugins/rescan
```
Forces `_discover_dashboard_plugins()` to re-run. Cache is per-process.

## Frontend: how plugins load (the `xI()` hook)

```javascript
function xI() {
  // Fetches plugins via ve.getPlugins()
  // For each plugin with a non-empty entry:
  //   1. If css exists, creates <link> tag
  //   2. Creates <script src="/dashboard-plugins/{name}/{entry}">
  //   3. onerror → sets plugin state to "LOAD_FAILED"
  //   4. onload → checks if register() was called; if not → "NO_REGISTER"
}
```

**Critical: there is NO guard for empty/missing `entry`.** If `entry` is
`""`, the URL becomes `/dashboard-plugins/{name}/` — a 404 that triggers
`LOAD_FAILED`. If the file at `entry` doesn't exist, same result.

### Plugin route rendering (the `sv()` component)

```javascript
function sv({name}) {
  // 1. If w_(name) returns a registered component → render it
  // 2. If _I(name) returns an error code → render error message
  //    - "LOAD_FAILED" → "Could not load this plugin's script..."
  //    - "NO_REGISTER" → "The plugin's script did not call register()..."
  // 3. Otherwise → render loading spinner
}
```

### Route creation (the `XK()` function)

```javascript
function XK(builtinRoutes, pluginManifests) {
  // For each plugin with a tab that is NOT hidden and NOT /plugins:
  //   Creates route: { path: tab.path, element: <sv name={plugin.name}> }
  // Hidden tabs also get routes, just added in a second pass.
}
```

## Common failure modes

### "Could not load this plugin's script" (LOAD_FAILED)

**Cause**: The `<script>` tag's `src` returned 404 or network error.

**Root causes**:
1. `manifest.json` declares a `tab` but the `entry` file doesn't exist on disk.
   Default `entry` is `"dist/index.js"` — if no `dist/` directory exists, this 404s.
2. `entry` is set to `""` — URL becomes `/dashboard-plugins/{name}/` which 404s
   (server requires a `{file_path}` segment).
3. The plugin's `dashboard/` directory was not packaged/shipped with the pip release.

**Diagnosis**: `curl http://127.0.0.1:9119/dashboard-plugins/{name}/{entry}` → check
HTTP status. Also check `ls <venv>/plugins/{name}/dashboard/` for missing files.

**Fix options**:
- **Remove `tab` from manifest.json** → no route created, no script attempt, error gone.
  Plugin API routes still work. Tab disappears from sidebar.
- **Ship the missing JS bundle** → place the built JS at `dashboard/dist/index.js`.
- **Set `entry` to a valid JS file** → create a minimal JS that registers the component
  via `window.__HERMES_PLUGINS__.register("name", Component)`.

### "The plugin's script did not call register()" (NO_REGISTER)

**Cause**: The JS file loaded successfully but never called
`window.__HERMES_PLUGINS__.register()`.

**Fix**: The JS bundle must call:
```javascript
window.__HERMES_PLUGINS__.register("plugin-name", MyComponent);
```

## Plugin registration API

The dashboard exposes on `window`:
```javascript
window.__HERMES_PLUGINS__ = {
  register(name, Component),    // register a tab-page component
  registerSlot(plugin, slotName, Component),  // register a slot component
};
window.__HERMES_PLUGIN_SDK__ = {
  React, hooks, api, fetchJSON, components, utils, useI18n
};
```

## Built-in components in the main bundle

Some plugin page components are compiled INTO the main dashboard JS bundle
(`index-*.js`). Evidence: i18n strings for the component exist alongside
built-in page translations. The kanban page is an example — its UI strings
("Loading Kanban board…", column labels, etc.) are in the main bundle in
multiple languages. But the plugin system still requires the external JS
to register the component — having the code in the bundle doesn't help
if the plugin loading path fails first.

## Version note

hermes-agent 0.14.0 shipped the kanban plugin without its JS bundle.
Version 0.15.2 (available on PyPI) likely includes the fix.
