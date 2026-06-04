package normalize

import (
	"encoding/json"
	"errors"
	"strings"
)

// ErrNoLLMLoaded is returned when lemond reports no loaded llm slot.
var ErrNoLLMLoaded = errors.New("normalize: no llm slot loaded in lemond health")

type loadedModel struct {
	ModelName string `json:"model_name"`
	Type      string `json:"type"`
	// Device/Recipe/Backend are optional hints lemond may surface per loaded
	// slot. When present they let us tell an NPU/FLM extraction slot apart from
	// the llamacpp/Vulkan iGPU chat slot without relying on the name alone.
	Device  string `json:"device"`
	Recipe  string `json:"recipe"`
	Backend string `json:"backend"`
}

type lemonHealth struct {
	AllModelsLoaded []loadedModel `json:"all_models_loaded"`
}

// isNPUorFLM reports whether a loaded llm slot is the NPU/FLM extraction model
// rather than the iGPU chat primary. The robust discriminator is the model
// name: lemond's FLM/NPU models carry an "-FLM" suffix (lemond's documented
// naming convention, e.g. "qwen3-it-4b-FLM", "gemma3-1b-FLM"). We also honor an
// explicit device/recipe/backend field if lemond surfaces one (npu / flm), so
// a richer future health schema sharpens the call rather than breaking it.
func isNPUorFLM(m loadedModel) bool {
	if strings.HasSuffix(m.ModelName, "-FLM") || strings.Contains(m.ModelName, "FLM") {
		return true
	}
	for _, hint := range []string{m.Device, m.Recipe, m.Backend} {
		h := strings.ToLower(hint)
		if strings.Contains(h, "npu") || strings.Contains(h, "flm") {
			return true
		}
	}
	return false
}

// ParseLoadedLLM extracts the currently-loaded LLM slot's model_name from a
// lemond /api/v1/health response body. Non-llm slots (embedding, tts, …) are
// ignored. When BOTH the iGPU chat primary and the NPU/FLM extraction model are
// loaded as llm slots, the iGPU chat slot wins regardless of array order (see
// isNPUorFLM). If only an FLM/NPU slot is loaded it is returned (NPU-only setups
// must keep working). Returns ErrNoLLMLoaded if no llm slot is loaded.
func ParseLoadedLLM(healthBody []byte) (string, error) {
	var h lemonHealth
	if err := json.Unmarshal(healthBody, &h); err != nil {
		return "", err
	}
	var flmFallback string
	for _, m := range h.AllModelsLoaded {
		if m.Type != "llm" || m.ModelName == "" {
			continue
		}
		if isNPUorFLM(m) {
			// Remember the FLM slot but keep scanning for a non-FLM (iGPU) slot,
			// which is preferred when both are loaded.
			if flmFallback == "" {
				flmFallback = m.ModelName
			}
			continue
		}
		return m.ModelName, nil
	}
	if flmFallback != "" {
		return flmFallback, nil
	}
	return "", ErrNoLLMLoaded
}
