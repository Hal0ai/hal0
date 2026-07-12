/**
 * hal0 Provider Extension for pi
 *
 * Connects pi to a local hal0 inference box by auto-discovering active
 * slots/models from the hal0 API and registering them as an OpenAI-compatible
 * provider. Re-fetches on session start so newly-loaded slots appear without
 * a restart.
 *
 * Vendored into the hal0 repo at src/hal0/agents/pi_coder/plugins/hal0-provider/
 * and deployed to ~/.pi/agent/extensions/hal0-provider/ by the pi-coder
 * driver at install time.
 *
 * Env vars:
 *   HAL0_API_URL   — default http://127.0.0.1:8080
 *   HAL0_API_KEY   — default "hal0-local" (the dev-mode sentinel)
 */

import {
  type AssistantMessageEventStream,
  type Context,
  createAssistantMessageEventStream,
  type Model,
  openAICompletionsApi,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai/compat";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// ── Config ───────────────────────────────────────────────────────────────

const HAL0_API_URL = (process.env["HAL0_API_URL"] ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
const HAL0_API_KEY = process.env["HAL0_API_KEY"] ?? "hal0-local";

interface Hal0ModelEntry {
  id: string;
  object: "model";
  created: number;
  owned_by: string;
  name?: string;
  context_length?: number;
}

// ── Model fetching ───────────────────────────────────────────────────────

async function fetchHal0Models(): Promise<Model<"openai-completions">[]> {
  const res = await fetch(`${HAL0_API_URL}/v1/models`, {
    headers: HAL0_API_KEY ? { Authorization: `Bearer ${HAL0_API_KEY}` } : {},
  });
  if (!res.ok) {
    console.error(`[hal0-provider] failed to fetch models: ${res.status} ${res.statusText}`);
    return [];
  }
  const body = (await res.json()) as { data: Hal0ModelEntry[] };
  if (!Array.isArray(body.data)) return [];

  const models: Model<"openai-completions">[] = [];
  for (const entry of body.data) {
    // Only register models owned by hal0 (skip upstream passthroughs like
    // MiniMax — those are routing targets, not local slots).
    if (entry.owned_by !== "hal0") continue;

    const displayName = entry.name ?? entry.id;
    const ctx = entry.context_length ?? 128_000;

    models.push({
      id: entry.id,
      name: displayName,
      provider: "hal0",
      api: "openai-completions",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: ctx,
      maxTokens: Math.min(ctx, 16_384),
      baseUrl: `${HAL0_API_URL}/v1`,
    });
  }
  return models;
}

// ── Stream function ──────────────────────────────────────────────────────

function streamHal0(
  model: Model<"openai-completions">,
  context: Context,
  options?: SimpleStreamOptions,
): AssistantMessageEventStream {
  const stream = createAssistantMessageEventStream();

  (async () => {
    try {
      const api = openAICompletionsApi();
      const modelWithUrl = { ...model, baseUrl: `${HAL0_API_URL}/v1` };
      const apiKey = options?.apiKey ?? HAL0_API_KEY;

      const innerStream = api.streamSimple(modelWithUrl, context, {
        ...options,
        apiKey: apiKey || undefined,
      });

      for await (const event of innerStream) stream.push(event);
      stream.end();
    } catch (error) {
      stream.push({
        type: "error",
        reason: "error",
        error: {
          role: "assistant",
          content: [],
          api: model.api,
          provider: model.provider,
          model: model.id,
          usage: {
            input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
          },
          stopReason: "error",
          errorMessage: error instanceof Error ? error.message : String(error),
          timestamp: Date.now(),
        },
      });
      stream.end();
    }
  })();

  return stream;
}

// ── Extension entry point ────────────────────────────────────────────────

export default async function (pi: ExtensionAPI) {
  let models = await fetchHal0Models();

  pi.registerProvider("hal0", {
    baseUrl: `${HAL0_API_URL}/v1`,
    apiKey: "$HAL0_API_KEY",
    api: "openai-completions",
    models: models.map(({ id, name, reasoning, input, cost, contextWindow, maxTokens }) => ({
      id,
      name,
      reasoning,
      input,
      cost,
      contextWindow,
      maxTokens,
    })),
    streamSimple: streamHal0,
  });

  pi.on("session_start", async (_event, ctx) => {
    const fresh = await fetchHal0Models();
    if (fresh.length > 0) {
      models = fresh;
      ctx.ui.notify(`hal0: ${fresh.map((m) => m.id).join(", ")}`, "info");
    }
  });
}
