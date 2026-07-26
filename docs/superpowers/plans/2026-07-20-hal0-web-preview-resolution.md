# hal0-web Preview Manifest Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `releases.hal0.dev` so `preview.json` and `preview.json.bundle` resolve from retained GitHub prereleases while stable/nightly behavior and 60-second caching remain unchanged.

**Architecture:** Expand the existing Cloudflare Pages middleware asset router rather than adding a second service. Select assets by exact name across the GitHub Releases API, so prerelease/final state is encoded by the asset produced by hal0's release policy. Preview failures return 503 instead of unsigned static fallback because preview clients require manifest authentication.

**Tech Stack:** TypeScript 6, Cloudflare Pages Functions, npm, Vitest, Astro build.

## Global Constraints

- Target repository: `https://github.com/Hal0ai/hal0-web`, default branch `master`.
- Current middleware: `functions/_middleware.ts`.
- Existing channels: `stable`, `nightly`, `dev`; preserve their behavior.
- Current GitHub list endpoint: `https://api.github.com/repos/Hal0ai/hal0/releases?per_page=10`.
- Preview resolves exact assets `preview.json` and `preview.json.bundle` from non-draft releases.
- Preview never falls back to an unsigned static manifest; return a non-cacheable 503 on proxy failure.
- Stable/nightly/dev keep current static fallback behavior.

---

## File Structure

**Modify:**

- `functions/_middleware.ts` — preview + bundle routing and preview fail-closed response.
- `package.json`, `package-lock.json` — add Vitest and test script.
- `README.md` — document preview endpoints and deployment ordering.

**Create:**

- `functions/_middleware.test.ts` — mocked GitHub API/asset tests.
- `public/releases/preview.json` — schema example/backstop documentation only; middleware does not serve it on preview failure.

---

### Task 1: Add a middleware test seam

**Files:**

- Modify: `package.json`, `package-lock.json`
- Modify: `functions/_middleware.ts`
- Create: `functions/_middleware.test.ts`

**Interfaces:**

- Export `parseReleaseAssetPath(pathname: string) -> { channel: string; assetName: string } | null`.
- Export `proxyChannelAsset(assetName: string, channel: string, token?: string) -> Promise<ProxyOutcome>`.
- Keep default export `onRequest` unchanged for Cloudflare.

- [ ] **Step 1: Install and configure Vitest**

```bash
npm install --save-dev vitest
npm pkg set scripts.test="vitest run"
```

- [ ] **Step 2: Extract pure routing without changing behavior**

Replace `CHANNEL_RE` use with:

```ts
export const RELEASE_ASSET_RE = /^\/(stable|nightly|dev)\.json$/;

export function parseReleaseAssetPath(pathname: string) {
  const match = RELEASE_ASSET_RE.exec(pathname);
  if (!match) return null;
  return { channel: match[1], assetName: pathname.slice(1) };
}
```

Export `proxyChannelAsset` and make the existing default handler call it.

- [ ] **Step 3: Write baseline behavior tests**

In `functions/_middleware.test.ts`, mock `globalThis.fetch`. Assert stable selects exact `stable.json`, skips draft releases, sends optional bearer token, and preserves headers `x-hal0-source`, `x-hal0-channel`, CORS, nosniff, and max-age 60.

- [ ] **Step 4: Verify baseline GREEN**

```bash
npm test
npm run astro -- check
npm run build
```

Expected: all pass before adding preview behavior.

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json functions/_middleware.ts functions/_middleware.test.ts
git commit -m "test(releases): add middleware routing seam"
```

---

### Task 2: Preview JSON and bundle routing

**Files:**

- Modify: `functions/_middleware.ts`
- Modify: `functions/_middleware.test.ts`

- [ ] **Step 1: Write failing preview route tests**

```ts
import { describe, expect, it } from "vitest";
import { parseReleaseAssetPath } from "./_middleware";

describe("preview assets", () => {
  it.each([
    ["/preview.json", "preview", "preview.json"],
    ["/preview.json.bundle", "preview", "preview.json.bundle"],
  ])("parses %s", (path, channel, assetName) => {
    expect(parseReleaseAssetPath(path)).toEqual({ channel, assetName });
  });
});
```

Add fetch tests proving `preview.json` may come from a GitHub release with `prerelease: true`, while final promotion may provide it from `prerelease: false`. Exact asset name, not GitHub prerelease flag, decides the channel pointer.

- [ ] **Step 2: Run tests and confirm RED**

```bash
npm test -- --run functions/_middleware.test.ts
```

Expected: preview paths parse as null.

- [ ] **Step 3: Expand the route expression**

```ts
export const RELEASE_ASSET_RE =
  /^\/(stable|preview|nightly|dev)\.json(?:\.bundle)?$/;
```

Use `pathname.slice(1)` as the exact GitHub asset name. Keep draft skipping. Do not filter preview assets by `release.prerelease`, because final releases intentionally carry `preview.json` during GA promotion.

- [ ] **Step 4: Verify**

```bash
npm test
npm run astro -- check
npm run build
```

- [ ] **Step 5: Commit**

```bash
git add functions/_middleware.ts functions/_middleware.test.ts
git commit -m "feat(releases): proxy preview manifest and signature bundle"
```

---

### Task 3: Fail closed for preview

**Files:**

- Modify: `functions/_middleware.ts`
- Modify: `functions/_middleware.test.ts`
- Create: `public/releases/preview.json`

- [ ] **Step 1: Failing tests for preview proxy failure**

Mock GitHub list failure, missing `preview.json`, and asset download failure. Assert:

```ts
expect(response.status).toBe(503);
expect(response.headers.get("cache-control")).toBe("no-store");
expect(response.headers.get("x-hal0-proxy-failed")).toBeTruthy();
expect(await response.json()).toEqual({
  error: "preview manifest unavailable",
  channel: "preview",
});
```

Retain tests proving stable/nightly/dev still rewrite to `/releases/<asset>` fallback.

- [ ] **Step 2: Implement fail-closed response**

In the default handler, after `proxyChannelAsset` fails:

```ts
if (route.channel === "preview") {
  return new Response(
    JSON.stringify({ error: "preview manifest unavailable", channel: "preview" }),
    {
      status: 503,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "access-control-allow-origin": "*",
        "x-content-type-options": "nosniff",
        "x-hal0-proxy-failed": outcome.reason,
      },
    },
  );
}
```

- [ ] **Step 3: Add static schema example**

Create `public/releases/preview.json` with `_schema`, `channel: "preview"`, `revoked: true`, and `revoked_reason: "static schema example; not a release pointer"`. The middleware must never serve it for preview failures.

- [ ] **Step 4: Verify**

```bash
npm test
npm run astro -- check
npm run build
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add functions/_middleware.ts functions/_middleware.test.ts public/releases/preview.json
git commit -m "fix(releases): fail closed when preview metadata is unavailable"
```

---

### Task 4: Documentation and production smoke

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Document endpoint contract**

Document:

```text
https://releases.hal0.dev/stable.json
https://releases.hal0.dev/preview.json
https://releases.hal0.dev/preview.json.bundle
https://releases.hal0.dev/nightly.json
```

Explain exact-asset selection, GitHub prerelease/final promotion, authenticated preview requirement, GITHUB_TOKEN quota, and 60-second cache.

- [ ] **Step 2: Verify locally**

```bash
npm test
npm run astro -- check
npm run build
```

- [ ] **Step 3: Deploy before hal0 preview publication**

Push to `master`, wait for the production deployment, then run:

```bash
curl -i https://releases.hal0.dev/stable.json
curl -i https://releases.hal0.dev/preview.json
curl -i https://releases.hal0.dev/preview.json.bundle
```

Before the first preview release, preview endpoints should return the designed 503. After the first preview tag, both return 200 with `x-hal0-channel: preview` and matching `x-hal0-source`.

- [ ] **Step 4: Commit docs**

```bash
git add README.md
git commit -m "docs(releases): add official preview endpoint contract"
```
