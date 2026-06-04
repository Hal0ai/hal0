// Package normalize holds the pure request-transform logic for the hal0
// Bifrost gateway plugin. It is intentionally free of any Bifrost dependency
// so it can be unit-tested in isolation; the plugin glue adapts Bifrost's
// schemas types to/from these minimal shapes.
package normalize

// ChatReq is the minimal view of a chat-completion request the transform needs.
type ChatReq struct {
	// Model is the requested model id (rewritten to the live primary slot).
	Model string
	// ExtraParams carries provider-specific body params (e.g. chat_template_kwargs).
	ExtraParams map[string]any
}

// Apply normalizes req in place:
//
//   - rewrites Model to primaryModel (the currently-loaded lemond slot id), so
//     callers can use a stable virtual name regardless of which model is loaded;
//   - unless callerOptedThinking, injects chat_template_kwargs.enable_thinking=false
//     so local reasoning models don't emit <think> blocks that time out.
func Apply(req *ChatReq, primaryModel string, callerOptedThinking bool) {
	req.Model = primaryModel

	if callerOptedThinking {
		return
	}
	if req.ExtraParams == nil {
		req.ExtraParams = map[string]any{}
	}
	ctk, ok := req.ExtraParams["chat_template_kwargs"].(map[string]any)
	if !ok {
		ctk = map[string]any{}
		req.ExtraParams["chat_template_kwargs"] = ctk
	}
	ctk["enable_thinking"] = false
}
