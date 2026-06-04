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

// When BOTH the iGPU chat primary and the NPU/FLM extraction model are loaded
// as type:"llm" slots, ParseLoadedLLM must prefer the iGPU chat slot (the
// llamacpp/Vulkan model), NOT the FLM extraction model. The FLM slot is
// identified by its "-FLM" model_name suffix (lemond's documented NPU naming
// convention; e.g. qwen3-it-4b-FLM, gemma3-1b-FLM) and/or an npu device/recipe
// field if present.
func TestParseLoadedLLMPrefersIGPUOverFLM(t *testing.T) {
	cases := []struct {
		name string
		body string
		want string
	}{
		{
			// FLM listed FIRST in the array (the regression case: old code
			// returned the first type:"llm" slot, i.e. the FLM).
			name: "flm_first_in_array",
			body: `{
			  "all_models_loaded": [
			    {"model_name": "qwen3-it-4b-FLM", "type": "llm", "device": "npu"},
			    {"model_name": "qwen3.6-35b-a3b-uncensored-q6kp", "type": "llm", "device": "gpu"}
			  ],
			  "status": "ok"
			}`,
			want: "qwen3.6-35b-a3b-uncensored-q6kp",
		},
		{
			// iGPU listed first — must still resolve to the iGPU slot.
			name: "igpu_first_in_array",
			body: `{
			  "all_models_loaded": [
			    {"model_name": "qwen3.6-35b-a3b-uncensored-q6kp", "type": "llm", "device": "gpu"},
			    {"model_name": "gemma3-1b-FLM", "type": "llm", "device": "npu"}
			  ],
			  "status": "ok"
			}`,
			want: "qwen3.6-35b-a3b-uncensored-q6kp",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ParseLoadedLLM([]byte(tc.body))
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Fatalf("got %q, want the iGPU chat slot %q", got, tc.want)
			}
		})
	}
}

// When ONLY an FLM/NPU slot is loaded, ParseLoadedLLM must still return it —
// we must not break NPU-only setups by insisting on a non-FLM slot.
func TestParseLoadedLLMReturnsFLMWhenOnlyFLMLoaded(t *testing.T) {
	body := []byte(`{
	  "all_models_loaded": [
	    {"model_name": "bge-embed", "type": "embedding"},
	    {"model_name": "qwen3-it-4b-FLM", "type": "llm", "device": "npu"}
	  ],
	  "status": "ok"
	}`)

	got, err := ParseLoadedLLM(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "qwen3-it-4b-FLM" {
		t.Fatalf("got %q, want the FLM slot (only llm loaded)", got)
	}
}

// A single non-FLM llm slot resolves unchanged (preserve existing behavior).
func TestParseLoadedLLMSingleLLMUnchanged(t *testing.T) {
	body := []byte(`{
	  "all_models_loaded": [
	    {"model_name": "qwen3.6-35b-a3b-uncensored-q6kp", "type": "llm", "device": "gpu"}
	  ],
	  "status": "ok"
	}`)

	got, err := ParseLoadedLLM(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "qwen3.6-35b-a3b-uncensored-q6kp" {
		t.Fatalf("got %q, want the single llm slot", got)
	}
}
