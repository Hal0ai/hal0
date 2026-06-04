package normalize

import "testing"

// ParseLoadedLLM picks the loaded LLM slot's model_name out of a lemond
// /api/v1/health body, ignoring non-llm slots (embed/tts/etc.) that share the
// all_models_loaded array.
func TestParseLoadedLLMPicksLLMSlot(t *testing.T) {
	body := []byte(`{
	  "all_models_loaded": [
	    {"model_name": "bge-embed", "type": "embedding"},
	    {"model_name": "qwen3.6-35b-a3b-uncensored-q6kp", "type": "llm", "device": "gpu"}
	  ],
	  "model_loaded": null,
	  "status": "ok"
	}`)

	got, err := ParseLoadedLLM(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "qwen3.6-35b-a3b-uncensored-q6kp" {
		t.Fatalf("got %q, want the llm slot model_name", got)
	}
}

// With no llm loaded, ParseLoadedLLM errors so the plugin can fail the request
// loudly (or fall back) rather than rewrite to an empty model.
func TestParseLoadedLLMErrorsWhenNoLLM(t *testing.T) {
	body := []byte(`{"all_models_loaded": [], "model_loaded": null, "status": "ok"}`)

	if _, err := ParseLoadedLLM(body); err == nil {
		t.Fatal("expected error when no llm slot is loaded, got nil")
	}
}
