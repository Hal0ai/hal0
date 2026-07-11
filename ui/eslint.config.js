// hal0-ui ESLint flat config.
//
// Purpose (issue #1170): the dash prototype in src/dash/*.jsx uses a
// window-globals wiring pattern — components and helpers are published onto
// `window` in one module and referenced as BARE identifiers in another, with
// no `import`. Because these .jsx files are excluded from tsconfig.json's
// `include` and `checkJs` is false, TypeScript never type-checks them, and
// Vite/esbuild transpiles JSX without any scope analysis. That is exactly how
// a `ReferenceError: selectedProfile is not defined` (a variable that lived in
// EditSlotDrawer but was referenced in CreateSlotModal) shipped in the bundle.
//
// This config turns on `no-undef` for the dash .jsx files so that an
// identifier which is neither a local binding nor a known global is caught at
// lint time — in JSX (`<Modal/>`) and in expressions alike.
//
// The tricky part is not drowning in false positives: hundreds of legitimate
// cross-module references (Modal, Drawer, useForm, Icons, parseSizeGB, …) are
// window globals. Rather than hardcode a list that rots the moment someone
// adds a component, we DERIVE the global names by scanning the dash tree for
// `Object.assign(window, {…})` / `window.X =` / `globalThis.X =` assignments.
// A genuine typo like `selectedProfile` is never assigned to window and is not
// a local, so it still trips the rule.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import globals from 'globals'

const here = path.dirname(fileURLToPath(import.meta.url))

// Recursively collect every identifier the dash prototype publishes on
// window/globalThis, so `no-undef` treats them as known read-only globals.
function collectDashGlobals() {
  const roots = [path.join(here, 'src', 'dash'), path.join(here, 'src', 'globals-install.ts')]
  const names = new Set(['React', 'ReactDOM'])
  const files = []
  const walk = (p) => {
    if (!fs.existsSync(p)) return
    const st = fs.statSync(p)
    if (st.isDirectory()) {
      for (const f of fs.readdirSync(p)) walk(path.join(p, f))
    } else if (/\.(jsx|tsx|ts|js)$/.test(p)) {
      files.push(p)
    }
  }
  roots.forEach(walk)
  for (const file of files) {
    const src = fs.readFileSync(file, 'utf8')
    for (const m of src.matchAll(/Object\.assign\(\s*(?:window|globalThis)\s*,\s*\{([\s\S]*?)\}\s*\)/g)) {
      for (const part of m[1].split(',')) {
        const key = part.split(':')[0].trim().replace(/^\.\.\./, '')
        if (/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(key)) names.add(key)
      }
    }
    for (const m of src.matchAll(/(?:window|globalThis)\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=/g)) {
      names.add(m[1])
    }
  }
  return Object.fromEntries([...names].map((n) => [n, 'readonly']))
}

const dashGlobals = collectDashGlobals()

export default [
  { ignores: ['dist/**', 'node_modules/**', 'ui-vue.bak/**', 'tests/**', 'playwright-report/**'] },
  {
    // Scope the guard to the window-globals prototype files. .ts/.tsx are
    // covered by `npm run typecheck` (tsc), which already flags undefined
    // identifiers there.
    files: ['src/dash/**/*.jsx'],
    // The dash sources carry inline `// eslint-disable-next-line
    // react-hooks/exhaustive-deps` comments from their original toolchain.
    // We don't enable (or ship) the react-hooks plugin here — this config is
    // a focused no-undef guard — but an unresolved rule name in a disable
    // directive is itself a hard error, so we stub the rule as a no-op below.
    // Those directives then read as "unused"; silence that so the guard's
    // output stays limited to real undefined-identifier findings.
    linterOptions: { reportUnusedDisableDirectives: 'off' },
    plugins: {
      'react-hooks': { rules: { 'exhaustive-deps': { create: () => ({}) } } },
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        ...globals.browser,
        ...dashGlobals,
      },
    },
    rules: {
      // The whole point of this config for #1170: undefined identifiers
      // (including undefined JSX components) fail the build's lint step.
      'no-undef': 'error',
    },
  },
]
