package normalize

import (
	"encoding/json"
	"errors"
)

// ErrNoLLMLoaded is returned when lemond reports no loaded llm slot.
var ErrNoLLMLoaded = errors.New("normalize: no llm slot loaded in lemond health")

type lemonHealth struct {
	AllModelsLoaded []struct {
		ModelName string `json:"model_name"`
		Type      string `json:"type"`
	} `json:"all_models_loaded"`
}

// ParseLoadedLLM extracts the currently-loaded LLM slot's model_name from a
// lemond /api/v1/health response body. Non-llm slots (embedding, tts, …) are
// ignored. Returns ErrNoLLMLoaded if none is loaded.
func ParseLoadedLLM(healthBody []byte) (string, error) {
	var h lemonHealth
	if err := json.Unmarshal(healthBody, &h); err != nil {
		return "", err
	}
	for _, m := range h.AllModelsLoaded {
		if m.Type == "llm" && m.ModelName != "" {
			return m.ModelName, nil
		}
	}
	return "", ErrNoLLMLoaded
}
