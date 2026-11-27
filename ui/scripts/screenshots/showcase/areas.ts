/**
 * Showcase overlay: per-area data. Each area lives in its own file and is
 * installed here, after the core overlay.
 */
import type { Page } from '@playwright/test'

import { AGENTS_SERVICES_PASSTHROUGH, installAgentsServices } from './agents-services'
import { ENGINES_PASSTHROUGH, installEngines } from './engines'
import { MEMORY_PASSTHROUGH, installMemory } from './memory'
import { installSettings } from './settings'

export const AREA_PASSTHROUGH: string[] = [...AGENTS_SERVICES_PASSTHROUGH, ...ENGINES_PASSTHROUGH, ...MEMORY_PASSTHROUGH]

export async function installAreas(page: Page) {
  await installAgentsServices(page)
  await installEngines(page)
  await installMemory(page)
  await installSettings(page)
}
