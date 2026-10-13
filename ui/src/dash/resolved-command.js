// resolved-command.js — what the slot drawer's "Resolved command" box shows.
//
// `GET /api/slots/{name}/resolved` answers `{argv: null, provenance: []}` for a
// slot whose launch llama-server does not compose (FLM, Kokoro, Moonshine,
// Qwen3-TTS, ComfyUI): those run their provider's own command, and the API
// invents none (ADR-0029). That null is a finished answer, not "still loading",
// so the box must say so rather than the "not yet available (slot not loaded)"
// sentinel it uses before any answer exists.
//
// Pure: a function of its arguments, no React, no window globals.

export const NOT_LLAMA_TEXT =
	"— not composed by llama-server (this slot runs its provider's own command)";
export const NOT_LOADED_TEXT = "— not yet available (slot not loaded)";

/**
 * Text for the resolved-command box.
 *
 * @param {{argv?: string[] | null} | null | undefined} resolvedData  the
 *   /resolved payload, or undefined while it loads / after an error.
 * @param {{resolved_command?: string[] | string | null} | null | undefined} slot
 *   the slot's list payload.
 */
export function resolvedCommandText(resolvedData, slot) {
	const argv = resolvedData?.argv ?? null;
	if (Array.isArray(argv) && argv.length > 0) {
		return argv.join(" \\\n  ");
	}
	if (Array.isArray(slot?.resolved_command)) {
		return slot.resolved_command.join(" \\\n  ");
	}
	if (slot?.resolved_command) {
		return slot.resolved_command;
	}
	// The endpoint answered and named no argv: the slot's launch is not
	// llama-server's to describe.
	if (resolvedData && "argv" in resolvedData && resolvedData.argv === null) {
		return NOT_LLAMA_TEXT;
	}
	return NOT_LOADED_TEXT;
}
