package normalize

import "testing"

// Apply must rewrite the requested model to the live primary slot id, so a
// caller (Hermes) can send any stable virtual name and still hit whatever
// lemond currently has loaded.
func TestApplyRewritesModelToPrimary(t *testing.T) {
	req := &ChatReq{Model: "lemonade/primary"}

	Apply(req, "qwen3.6-35b-a3b-uncensored-q6kp", false)

	if req.Model != "qwen3.6-35b-a3b-uncensored-q6kp" {
		t.Fatalf("model = %q, want the resolved primary id", req.Model)
	}
}

// When the caller did NOT opt into thinking, Apply injects
// chat_template_kwargs.enable_thinking=false so local reasoning models don't
// emit <think> blocks that blow the request timeout.
func TestApplyInjectsThinkingOffWhenNotOptedIn(t *testing.T) {
	req := &ChatReq{Model: "x"} // ExtraParams nil — must be created

	Apply(req, "primary-id", false)

	ctk, ok := req.ExtraParams["chat_template_kwargs"].(map[string]any)
	if !ok {
		t.Fatalf("chat_template_kwargs not a map: %#v", req.ExtraParams["chat_template_kwargs"])
	}
	if ctk["enable_thinking"] != false {
		t.Fatalf("enable_thinking = %v, want false", ctk["enable_thinking"])
	}
}

// When the caller explicitly opted into thinking, Apply must NOT clobber their
// chat_template_kwargs.
func TestApplyPreservesCallerThinkingWhenOptedIn(t *testing.T) {
	req := &ChatReq{
		Model: "x",
		ExtraParams: map[string]any{
			"chat_template_kwargs": map[string]any{"enable_thinking": true},
		},
	}

	Apply(req, "primary-id", true)

	ctk := req.ExtraParams["chat_template_kwargs"].(map[string]any)
	if ctk["enable_thinking"] != true {
		t.Fatalf("enable_thinking = %v, want caller's true preserved", ctk["enable_thinking"])
	}
}
