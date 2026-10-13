// resolved-command.js — what the slot drawer's "Resolved command" box shows.
//
// `GET /api/slots/{name}/resolved` answers `{argv: null, provenance: []}` for a
// slot whose launch llama-server does not compose (FLM, Kokoro, Moonshine,
// Qwen3-TTS, ComfyUI): those run their provider's own command, and the API
// invents none (ADR-0029). That null is a finished answer, not "still loading",
// so the box must say so (it is also the answer for a slot that cannot launch)
// rather than the "not yet available (slot not loaded)"
// sentinel it uses before any answer exists.
//
// Pure: a function of its arguments, no React, no window globals.

export const NOT_LLAMA_TEXT =
	"— no llama-server command (the slot runs its provider's own command, or cannot launch as configured)";
export const NOT_LOADED_TEXT = "— not yet available (slot not loaded)";

/**
 * Text for the resolved-command box.
 *
 * @param {{argv?: string[] | null} | null | undefined} resolvedData  the
 *   /resolved payload, or undefined while it loads / after an error.
 * @param {{name?: string, resolved_command?: string[] | string | null} | null | undefined} slot
 *   the slot's list payload.
 */
export function resolvedCommandText(resolvedData, slot) {
	const argv = resolvedData?.argv ?? null;
	if (Array.isArray(argv) && argv.length > 0) {
		return argv.join(" \\\n  ");
	}
	// The endpoint answered and named no argv: a finished answer that beats
	// the list payload's (possibly older) command — the slot either runs its
	// provider's own command or cannot launch as configured.
	if (resolvedData && "argv" in resolvedData && resolvedData.argv === null) {
		return NOT_LLAMA_TEXT;
	}
	if (Array.isArray(slot?.resolved_command)) {
		return slot.resolved_command.join(" \\\n  ");
	}
	if (slot?.resolved_command) {
		return slot.resolved_command;
	}
	return NOT_LOADED_TEXT;
}
