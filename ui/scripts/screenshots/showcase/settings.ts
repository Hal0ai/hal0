/**
 * Showcase overlay: Settings pages that read their own endpoints.
 *
 * /api/settings, /fields, /schema and /apply-plan come from data/*.json,
 * rendered from hal0's own schema at its defaults by gen_settings_data.py
 * (re-run it after a schema change). Storage: the model store on a dedicated
 * disk (shape: ModelStoreState, ui/src/api/hooks/useSettings.ts:117-139).
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Page } from '@playwright/test'

import { json } from './core'

const DATA = path.join(path.dirname(fileURLToPath(import.meta.url)), 'data')
const load = (name: string) => JSON.parse(fs.readFileSync(path.join(DATA, name), 'utf-8'))

const GB = 1024 ** 3

function probe(path: string, files: number, usedGb: number, freeGb: number, exists = true) {
  return {
    path,
    exists,
    is_dir: exists,
    readable: exists,
    writable: exists,
    files_count: files,
    size_bytes: Math.round(usedGb * GB),
    free_bytes: Math.round(freeGb * GB),
  }
}

export async function installSettings(page: Page) {
  const settings = load('settings-defaults.json')
  settings.models = { ...settings.models, store: '/srv/models' }
  const get = (body: unknown) => (route: any) =>
    route.request().method() === 'GET' ? json(route, body) : route.fallback()
  await page.route('**/api/settings', get(settings))
  await page.route('**/api/settings/fields', get(load('settings-fields.json')))
  await page.route('**/api/settings/schema', get(load('settings-schema.json')))
  await page.route('**/api/settings/apply-plan', get(load('settings-apply-plan.json')))
  await page.route('**/api/settings/models/store', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const current = probe('/srv/models', 14, 186.4, 1652.3)
    return json(route, {
      store: '/srv/models',
      effective: '/srv/models',
      fallback_active: false,
      pull_root_legacy: '/var/lib/hal0/models',
      current_state: current,
      suggestions: [
        { ...current, is_current: true },
        { ...probe('/var/lib/hal0/models', 0, 0, 412.8), is_current: false },
        { ...probe('/mnt/ai-models', 0, 0, 0, false), is_current: false },
      ],
    })
  })
}
