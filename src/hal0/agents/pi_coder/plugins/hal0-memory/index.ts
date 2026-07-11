/**
 * hal0-memory extension for pi
 *
 * Wires pi to hal0-api's `/api/memory/*` REST surface (the same one the
 * hermes plugin uses; hermes itself uses REST too, not MCP — see
 * /var/lib/hal0/.hermes/plugins/hal0-memory/_client.py).
 *
 * Two banks, selected per write by `X-hal0-Private`:
 *   - `X-hal0-Private: 1` → private:pi-coder  (default; only this agent reads)
 *   - `X-hal0-Private: 0` → shared            (cross-agent; hermes reads too)
 *
 * Identity: `X-hal0-Agent: pi-coder` (override with HAL0_AGENT_ID env).
 * No Bearer auth — LAN trust per ADR-0012.
 *
 * Live-API quirks (verified against hal0-api 0.8.3b1):
 *   1. `/api/memory/add` is ASYNC. The response carries an `operation_id`,
 *      not the eventual memory item id. Hindsight extracts the text into
 *      a structured `experience`/`observation` record within ~2-5s. Tags
 *      ARE preserved through extraction. Text is rewritten by the
 *      extraction model — don't pattern-match the original phrasing.
 *   2. `/api/memory/search` and `/api/memory/recall` return the UNION of
 *      both banks (server-side) regardless of the header.
 *   3. `/api/memory/list` is BANK-FILTERED (NOT union). This extension
 *      fetches both banks and merges so the LLM sees everything.
 *   4. `POST /api/memory/delete` is a no-op (`{"deleted":0}`) — the real
 *      item-delete path is `DELETE /api/memory/banks/{id}?confirm={id}`,
 *      which works because each memory item has its own bank id. This
 *      extension uses that path.
 *
 * Vendored into the hal0 repo at src/hal0/agents/pi_coder/plugins/hal0-memory/
 * and deployed to ~/.pi/agent/extensions/hal0-memory/ by the pi-coder
 * driver at install time. Supersedes routing `/mcp/memory` through the
 * generic pi-mcp-adapter proxy — this native extension is the shipped
 * default (see the pi-coder driver's adapter config, which now only
 * wires `hal0-admin`).
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "@earendil-works/pi-ai";

const DEFAULT_BASE_URL = "http://127.0.0.1:8080";
const DEFAULT_AGENT_ID = "pi-coder";
const BASE_URL = (process.env.HAL0_MEMORY_BASE ?? DEFAULT_BASE_URL).replace(/\/$/, "");
const AGENT_ID = process.env.HAL0_AGENT_ID ?? DEFAULT_AGENT_ID;
const READ_TIMEOUT_MS = 30_000;

// ── REST client ──────────────────────────────────────────────────────

type MemoryItem = {
	id: string;
	text?: string;
	content?: string;
	dataset?: string;
	tags?: string[];
	timestamp?: string;
	metadata?: Record<string, unknown>;
	score?: number | null;
	type?: string;
};

class Hal0MemoryError extends Error {
	constructor(message: string, public readonly status?: number) {
		super(message);
		this.name = "Hal0MemoryError";
	}
}

function headers(privateBank: boolean, hasBody: boolean): Record<string, string> {
	const h: Record<string, string> = {
		"X-hal0-Agent": AGENT_ID,
		"X-hal0-Private": privateBank ? "1" : "0",
		Accept: "application/json",
	};
	if (hasBody) h["Content-Type"] = "application/json";
	return h;
}

async function request<T = Record<string, unknown>>(
	method: "GET" | "POST" | "DELETE",
	path: string,
	opts: { body?: unknown; params?: Record<string, string | number>; privateBank?: boolean } = {},
): Promise<{ status: number; data: T }> {
	const { body, params, privateBank = true } = opts;
	let url = path;
	if (params) {
		const qs = new URLSearchParams(
			Object.entries(params).map(([k, v]) => [k, String(v)]),
		).toString();
		url += (path.includes("?") ? "&" : "?") + qs;
	}
	let res: Response;
	try {
		res = await fetch(`${BASE_URL}${url}`, {
			method,
			headers: headers(privateBank, body !== undefined),
			body: body !== undefined ? JSON.stringify(body) : undefined,
			signal: AbortSignal.timeout(READ_TIMEOUT_MS),
		});
	} catch (e) {
		throw new Hal0MemoryError(
			`hal0-memory transport failure on ${method} ${path}: ${(e as Error).message}`,
		);
	}
	const text = await res.text();
	if (!res.ok) {
		throw new Hal0MemoryError(
			`hal0-memory ${method} ${path} → ${res.status}: ${text.slice(0, 200)}`,
			res.status,
		);
	}
	let data: T;
	try {
		data = text ? (JSON.parse(text) as T) : ({} as T);
	} catch {
		data = { raw: text } as unknown as T;
	}
	return { status: res.status, data };
}

async function add(text: string, opts: { tags?: string[]; shared?: boolean } = {}) {
	const { data, status } = await request<{ id?: string; operation_id?: string; timestamp?: string }>(
		"POST",
		"/api/memory/add",
		{
			body: { text, tags: opts.tags ?? [`agent:${AGENT_ID}`] },
			privateBank: !(opts.shared ?? false),
		},
	);
	return { status, id: data.operation_id ?? data.id ?? null, timestamp: data.timestamp };
}

async function search(query: string, limit = 10) {
	return request<{ items?: MemoryItem[] }>("POST", "/api/memory/search", {
		body: { query, limit },
		privateBank: true,
	});
}

async function recall(query: string, maxTokens = 2048) {
	return request<{ items?: MemoryItem[]; text?: string }>("POST", "/api/memory/recall", {
		body: { query, max_tokens: maxTokens },
		privateBank: true,
	});
}

async function listBank(privateBank: boolean, limit = 50) {
	return request<{ items?: MemoryItem[] }>("GET", "/api/memory/list", {
		params: { limit },
		privateBank,
	});
}

async function listUnion(limit = 50): Promise<MemoryItem[]> {
	// list is bank-filtered; merge both directions. Dedup by id.
	const [priv, sh] = await Promise.all([listBank(true, limit), listBank(false, limit)]);
	const seen = new Set<string>();
	const merged: MemoryItem[] = [];
	for (const it of [...(priv.data.items ?? []), ...(sh.data.items ?? [])]) {
		if (!seen.has(it.id)) {
			seen.add(it.id);
			merged.push(it);
		}
	}
	return merged;
}

async function deleteItem(id: string): Promise<{ status: number; ok: boolean; body: string }> {
	// POST /api/memory/delete is a no-op; the real path is bank-delete with the
	// item's own id (each memory item is its own bank in hal0-api's model).
	const url = `/api/memory/banks/${encodeURIComponent(id)}?confirm=${encodeURIComponent(id)}`;
	const res = await fetch(`${BASE_URL}${url}`, {
		method: "DELETE",
		headers: headers(true, false),
		signal: AbortSignal.timeout(READ_TIMEOUT_MS),
	});
	const body = await res.text();
	return { status: res.status, ok: res.ok, body };
}

// ── Helpers ──────────────────────────────────────────────────────────

function fmtItem(item: MemoryItem, i: number): string {
	const text = item.text || item.content || "(no text)";
	const dataset = item.dataset ? ` [${item.dataset}]` : "";
	const ts = item.timestamp ? ` · ${item.timestamp.slice(0, 19)}` : "";
	const tags = item.tags?.length ? ` · tags=${item.tags.join(",")}` : "";
	return `${i + 1}.${dataset}${ts}${tags}\n   id=${item.id}\n   ${text}`;
}

function shortErr(e: unknown): string {
	if (e instanceof Hal0MemoryError) return `${e.message}${e.status ? ` (HTTP ${e.status})` : ""}`;
	return String((e as Error)?.message ?? e);
}

// ── Schemas ──────────────────────────────────────────────────────────

const SEARCH_PARAMS = Type.Object({
	query: Type.String({ description: "What to search for." }),
	limit: Type.Optional(Type.Integer({ description: "Max results (default 10)." })),
});

const RECALL_PARAMS = Type.Object({
	query: Type.String({ description: "Topic to recall." }),
	max_tokens: Type.Optional(Type.Integer({ description: "Token budget (default 2048)." })),
});

const ADD_PARAMS = Type.Object({
	text: Type.String({ description: "The fact to remember." }),
	shared: Type.Optional(
		Type.Boolean({
			description: "true → shared bank; false/omitted → private:pi-coder.",
		}),
	),
	tags: Type.Optional(Type.Array(Type.String(), { description: "Optional tags." })),
});

const LIST_PARAMS = Type.Object({
	limit: Type.Optional(Type.Integer({ description: "Max items per bank (default 50)." })),
});

const DELETE_PARAMS = Type.Object({
	ids: Type.Array(Type.String(), { description: "Memory item IDs to delete." }),
	confirm: Type.Optional(
		Type.Boolean({ description: "Must be true to actually delete. Safety guard." }),
	),
});

const WHOAMI_PARAMS = Type.Object({});

// ── Extension ────────────────────────────────────────────────────────

export default function hal0MemoryExtension(pi: ExtensionAPI) {
	let toolsRegistered = false;

	const registerTools = () => {
		if (toolsRegistered) return;
		toolsRegistered = true;

		pi.registerTool({
			name: "hal0_memory_search",
			label: "hal0 memory search",
			description:
				"Search durable hal0 memory. Returns ranked excerpts across BOTH pi-coder-private and shared banks (server-side union). Use before asking the user to repeat themselves.",
			promptSnippet: "Search durable hal0 memory (private + shared union).",
			promptGuidelines: [
				"Use hal0_memory_search when the user asks about previously stored context, prior decisions, or anything that might already be remembered.",
			],
			parameters: SEARCH_PARAMS,
			async execute(_id, params) {
				try {
					const { data } = await search(params.query, params.limit ?? 10);
					const items = data.items ?? [];
					const body = items.length
						? items.map((it, i) => fmtItem(it, i)).join("\n\n")
						: "(no matches)";
					return { content: [{ type: "text", text: body }], details: { count: items.length } };
				} catch (e) {
					return { content: [{ type: "text", text: `error: ${shortErr(e)}` }] };
				}
			},
		});

		pi.registerTool({
			name: "hal0_memory_recall",
			label: "hal0 memory recall",
			description:
				"Token-budgeted consolidated recall (Hindsight observations) across both banks. Prefer over search for a synthesized picture rather than raw excerpts.",
			promptSnippet: "Token-budgeted consolidated recall across both banks.",
			promptGuidelines: [
				"Use hal0_memory_recall for a synthesized picture of what's known about a topic.",
			],
			parameters: RECALL_PARAMS,
			async execute(_id, params) {
				try {
					const { data } = await recall(params.query, params.max_tokens ?? 2048);
					const items = data.items ?? [];
					if (items.length) {
						const body = items.map((it, i) => fmtItem(it, i)).join("\n\n");
						return { content: [{ type: "text", text: body }], details: { count: items.length } };
					}
					if (data.text) return { content: [{ type: "text", text: data.text }] };
					return { content: [{ type: "text", text: "(no recall results)" }] };
				} catch (e) {
					return { content: [{ type: "text", text: `error: ${shortErr(e)}` }] };
				}
			},
		});

		pi.registerTool({
			name: "hal0_memory_add",
			label: "hal0 memory add",
			description:
				"Persist a durable fact to hal0 memory. Default bank is private:pi-coder (only this agent recalls it). Set shared=true to write the shared bank (readable by hermes and every other agent).",
			promptSnippet: "Persist a durable fact to private or shared memory.",
			promptGuidelines: [
				"Use hal0_memory_add when the user states a durable preference, decision, or fact they want remembered across sessions.",
				"Default to private; use shared=true only when the fact is meaningful to other agents on this host.",
				"add is asynchronous: the returned id is an operation_id; the resolved memory item id appears ~2-5s later via list/search.",
			],
			parameters: ADD_PARAMS,
			async execute(_id, params) {
				try {
					const tags = params.tags ?? [`agent:${AGENT_ID}`];
					const shared = params.shared ?? false;
					const { id, timestamp } = await add(params.text, { tags, shared });
					const bank = shared ? "shared" : `private:${AGENT_ID}`;
					return {
						content: [
							{
								type: "text",
								text: `queued → bank=${bank} operation_id=${id ?? "(none)"}${timestamp ? ` ts=${timestamp}` : ""}\n(extraction runs server-side; the resolved item id appears via list/search in 2-5s)`,
							},
						],
						details: { bank, operation_id: id, timestamp },
					};
				} catch (e) {
					return { content: [{ type: "text", text: `error: ${shortErr(e)}` }] };
				}
			},
		});

		pi.registerTool({
			name: "hal0_memory_list",
			label: "hal0 memory list",
			description:
				"List memory items from both banks (private:pi-coder + shared). This extension fetches both directions and merges; the underlying list endpoint is bank-filtered.",
			promptSnippet: "List stored memory items from both banks (merged).",
			promptGuidelines: [
				"Use hal0_memory_list to inventory what's stored before deciding what to add or remove.",
			],
			parameters: LIST_PARAMS,
			async execute(_id, params) {
				try {
					const items = await listUnion(params.limit ?? 50);
					const body = items.length
						? items.map((it, i) => fmtItem(it, i)).join("\n\n")
						: "(empty)";
					const byBank = items.reduce<Record<string, number>>((acc, it) => {
						const k = it.dataset ?? "unknown";
						acc[k] = (acc[k] ?? 0) + 1;
						return acc;
					}, {});
					return {
						content: [{ type: "text", text: body }],
						details: { count: items.length, byBank },
					};
				} catch (e) {
					return { content: [{ type: "text", text: `error: ${shortErr(e)}` }] };
				}
			},
		});

		pi.registerTool({
			name: "hal0_memory_delete",
			label: "hal0 memory delete",
			description:
				"Delete memory items by ID. Requires confirm=true to actually delete (safety guard). Implemented via DELETE /api/memory/banks/{id}?confirm={id} because POST /api/memory/delete is a no-op in hal0-api.",
			promptSnippet: "Delete memory items by ID (requires confirm=true).",
			promptGuidelines: [
				"hal0_memory_delete requires confirm=true; without it the call returns a dry-run preview and does not mutate state.",
				"Use hal0_memory_list or hal0_memory_search to find item ids before calling delete.",
			],
			parameters: DELETE_PARAMS,
			async execute(_id, params) {
				if (!params.confirm) {
					return {
						content: [
							{
								type: "text",
								text: `dry-run: would delete ${params.ids.length} item(s). Re-call with confirm=true to execute.`,
							},
						],
						details: { dryRun: true, ids: params.ids },
					};
				}
				const results: { id: string; status: number; ok: boolean }[] = [];
				for (const id of params.ids) {
					try {
						const r = await deleteItem(id);
						results.push({ id, status: r.status, ok: r.ok });
					} catch (e) {
						results.push({ id, status: -1, ok: false });
					}
				}
				const ok = results.filter((r) => r.ok).length;
				const body = results
					.map((r) => `  ${r.ok ? "✓" : "✗"} ${r.id} (HTTP ${r.status})`)
					.join("\n");
				return {
					content: [{ type: "text", text: `deleted ${ok}/${results.length} item(s):\n${body}` }],
					details: { results },
				};
			},
		});

		pi.registerTool({
			name: "hal0_memory_whoami",
			label: "hal0 memory whoami",
			description:
				"Show hal0-memory identity, configured banks, endpoint reachability, and item counts. Useful for debugging which bank a write will land in.",
			promptSnippet: "Show memory identity, banks, endpoint reachability.",
			parameters: WHOAMI_PARAMS,
			async execute() {
				let ping = "ok";
				let counts = "—";
				try {
					const items = await listUnion(200);
					const byBank = items.reduce<Record<string, number>>((acc, it) => {
						const k = it.dataset ?? "unknown";
						acc[k] = (acc[k] ?? 0) + 1;
						return acc;
					}, {});
					counts = Object.entries(byBank).map(([k, v]) => `${k}=${v}`).join(", ") || "empty";
				} catch (e) {
					ping = `unreachable: ${shortErr(e)}`;
				}
				const out = [
					`agent_id:    ${AGENT_ID}`,
					`base_url:    ${BASE_URL}`,
					`private bank: private:${AGENT_ID}`,
					`shared bank:  shared`,
					`read union:   private:${AGENT_ID} + shared (server-side, search/recall only)`,
					`list:         fetches both banks and merges (list endpoint is bank-filtered)`,
					`delete:       DELETE /api/memory/banks/{id}?confirm={id}`,
					`endpoint:     ${ping}`,
					`item counts:  ${counts}`,
				].join("\n");
				return { content: [{ type: "text", text: out }] };
			},
		});
	};

	// ── Session start: register tools + status notification ───────────

	pi.on("session_start", async (_event, ctx: ExtensionContext) => {
		registerTools();
		ctx.ui.notify(
			`hal0-memory: agent=${AGENT_ID} banks=private:${AGENT_ID}+shared endpoint=${shortHost(BASE_URL)}`,
			"info",
		);
	});

	function shortHost(url: string): string {
		try {
			return new URL(url).host;
		} catch {
			return url;
		}
	}

	// ── Slash commands ────────────────────────────────────────────────

	pi.registerCommand("mem", {
		description: "Show memory status and recent items (union)",
		handler: async (_args, ctx) => {
			try {
				const items = await listUnion(10);
				const byBank = items.reduce<Record<string, number>>((acc, it) => {
					const k = it.dataset ?? "unknown";
					acc[k] = (acc[k] ?? 0) + 1;
					return acc;
				}, {});
				const status =
					`agent:    ${AGENT_ID}\n` +
					`endpoint: ${BASE_URL}\n` +
					`banks:    private:${AGENT_ID} + shared\n` +
					`recent:   ${items.length} (${Object.entries(byBank).map(([k, v]) => `${k}=${v}`).join(", ") || "—"})`;
				const body = items.length ? "\n\n" + items.map((it, i) => fmtItem(it, i)).join("\n\n") : "";
				ctx.ui.notify(status + body, "info");
			} catch (e) {
				ctx.ui.notify(`mem: ${shortErr(e)}`, "error");
			}
		},
	});

	pi.registerCommand("mem-recall", {
		description: "Quick recall: /mem-recall <query>",
		handler: async (args, ctx) => {
			const q = args.trim();
			if (!q) {
				ctx.ui.notify("usage: /mem-recall <query>", "warning");
				return;
			}
			try {
				const { data } = await recall(q, 2048);
				const items = data.items ?? [];
				if (items.length) {
					ctx.ui.notify(items.map((it, i) => fmtItem(it, i)).join("\n\n"), "info");
				} else if (data.text) {
					ctx.ui.notify(data.text, "info");
				} else {
					ctx.ui.notify("(no recall results)", "info");
				}
			} catch (e) {
				ctx.ui.notify(`mem-recall: ${shortErr(e)}`, "error");
			}
		},
	});

	pi.registerCommand("mem-forget", {
		description:
			"Delete memory items by id (comma-separated) or search-then-pick: /mem-forget <id[,id...] | query>",
		handler: async (args, ctx) => {
			const q = args.trim();
			if (!q) {
				ctx.ui.notify(
					"usage: /mem-forget <id[,id...]>   delete by id(s)\n" +
						"       /mem-forget <query>          search-then-pick (use ids shown)",
					"warning",
				);
				return;
			}
			const idLike = q
				.split(/[,\s]+/)
				.every((s) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s));
			try {
				if (idLike) {
					const ids = q.split(/[,\s]+/).filter(Boolean);
					const results: string[] = [];
					for (const id of ids) {
						const r = await deleteItem(id);
						results.push(`  ${r.ok ? "✓" : "✗"} ${id} (HTTP ${r.status})`);
					}
					ctx.ui.notify(`deleted ${results.filter((s) => s.startsWith("  ✓")).length}/${ids.length}:\n${results.join("\n")}`, "info");
					return;
				}
				const { data } = await search(q, 10);
				const items = data.items ?? [];
				if (!items.length) {
					ctx.ui.notify("(no matches)", "info");
					return;
				}
				ctx.ui.notify(
					`candidates for "${q}":\n\n` +
						items.map((it, i) => fmtItem(it, i)).join("\n\n") +
						"\n\nuse /mem-forget <id> to remove a specific item",
					"info",
				);
			} catch (e) {
				ctx.ui.notify(`mem-forget: ${shortErr(e)}`, "error");
			}
		},
	});

	pi.registerCommand("mem-doctor", {
		description: "Health check: reachability, identity, item counts per bank",
		handler: async (_args, ctx) => {
			try {
				const t0 = Date.now();
				const items = await listUnion(200);
				const ms = Date.now() - t0;
				const byBank = items.reduce<Record<string, number>>((acc, it) => {
					const k = it.dataset ?? "unknown";
					acc[k] = (acc[k] ?? 0) + 1;
					return acc;
				}, {});
				const lines = [
					`endpoint:    ${BASE_URL} (${ms}ms)`,
					`agent_id:    ${AGENT_ID}`,
					`reachable:   yes`,
					`total items: ${items.length}`,
					`by bank:     ${Object.entries(byBank).map(([k, v]) => `${k}=${v}`).join(", ") || "—"}`,
					`delete path: DELETE /api/memory/banks/{id}?confirm={id}`,
				];
				ctx.ui.notify(lines.join("\n"), "info");
			} catch (e) {
				ctx.ui.notify(`mem-doctor: ${shortErr(e)}`, "error");
			}
		},
	});
}