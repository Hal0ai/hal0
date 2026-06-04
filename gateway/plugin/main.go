// Command hal0-normalize is a Bifrost native (.so) plugin that sits between
// hal0's agents (Hermes first) and lemond. It does two things every chat
// request, replacing the per-client patches hal0 carries today:
//
//  1. Rewrites the requested model to lemond's currently-loaded LLM slot, so a
//     caller can use one stable virtual name ("lemonade/primary") regardless of
//     which model is loaded — killing Lemonade's strict-name 404 tool-loop.
//  2. Injects chat_template_kwargs.enable_thinking=false (unless the caller
//     opted in), so local reasoning models don't emit <think> blocks that blow
//     the request timeout.
//
// The pure transform lives in ../normalize (unit-tested); this file is the thin
// Bifrost adapter + the lemond model resolver (short-TTL cached).
//
// Build:  go build -buildmode=plugin -o build/hal0_normalize.so ./plugin
// The .so MUST be built from the same source tree + Go toolchain as the
// bifrost-http binary it loads into (see Makefile).
package main

import (
	"encoding/json"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/Hal0ai/hal0/gateway/normalize"
	"github.com/maximhq/bifrost/core/schemas"
)

type pluginConfig struct {
	// HealthURL is lemond's health endpoint; its all_models_loaded[] is the
	// source of truth for the live primary slot.
	HealthURL string `json:"health_url"`
	// TTLMillis bounds how often we poll lemond (one poll per window, cached).
	TTLMillis int `json:"ttl_ms"`
}

var (
	cfg = pluginConfig{HealthURL: "http://127.0.0.1:13305/api/v1/health", TTLMillis: 2000}

	mu          sync.RWMutex
	cachedModel string
	cachedAt    time.Time
	// Short timeout so a slow/hung lemond /health (a known failure mode while a
	// big model is loaded) fails fast and we fall back to the last-known-good
	// cached slot instead of blocking the request for 3s.
	httpClient = &http.Client{Timeout: 1500 * time.Millisecond}
)

// Init is called once when Bifrost loads the plugin.
func Init(raw any) error {
	if raw == nil {
		return nil
	}
	// Config arrives as decoded JSON; round-trip into the typed struct.
	b, err := json.Marshal(raw)
	if err != nil {
		return err
	}
	var c pluginConfig
	if err := json.Unmarshal(b, &c); err != nil {
		return err
	}
	if c.HealthURL != "" {
		cfg.HealthURL = c.HealthURL
	}
	if c.TTLMillis > 0 {
		cfg.TTLMillis = c.TTLMillis
	}
	return nil
}

// GetName returns the plugin's unique identifier.
func GetName() string { return "hal0-normalize" }

// PreLLMHook normalizes the request before it reaches lemond.
func PreLLMHook(ctx *schemas.BifrostContext, req *schemas.BifrostRequest) (*schemas.BifrostRequest, *schemas.LLMPluginShortCircuit, error) {
	if req == nil || req.ChatRequest == nil {
		return req, nil, nil // not a chat request — leave untouched
	}

	primary, stale, err := resolvePrimary()
	if err != nil {
		// No LLM loaded and nothing cached (or lemond unreachable on the very
		// first poll). Don't rewrite to an empty model; let the original request
		// fall through and surface lemond's own error rather than masking it.
		if ctx != nil {
			ctx.Log(schemas.LogLevelWarn, "hal0-normalize: primary resolve failed, passing through: "+err.Error())
		}
		return req, nil, nil
	}
	if stale && ctx != nil {
		// Health fetch/parse failed but we have a last-known-good slot. Serve it
		// (stale-but-serving) instead of 404-ing the caller; the next request
		// after lemond recovers will refresh the cache.
		ctx.Log(schemas.LogLevelWarn, "hal0-normalize: lemond health unavailable, using cached primary: "+primary)
	}

	// Adapt Bifrost -> pure view.
	view := &normalize.ChatReq{Model: req.ChatRequest.Model}
	callerOptedThinking := false
	if req.ChatRequest.Params != nil && req.ChatRequest.Params.ExtraParams != nil {
		view.ExtraParams = req.ChatRequest.Params.ExtraParams
		if _, ok := view.ExtraParams["chat_template_kwargs"]; ok {
			callerOptedThinking = true
		}
	}

	normalize.Apply(view, primary, callerOptedThinking)

	// Write back. Bifrost's openai-base custom provider only merges ExtraParams
	// into the outgoing wire body (via MergeExtraParamsIntoJSON) when the context
	// flag BifrostContextKeyPassthroughExtraParams is set true — the vllm/sgl
	// providers set it for us, but the custom provider does NOT. Without the flag
	// our chat_template_kwargs is silently dropped and reasoning models keep
	// emitting <think> blocks. So we set it ourselves whenever we have params to
	// pass through. (Bifrost core/providers/utils/utils.go merge gate.)
	req.ChatRequest.Model = view.Model
	if req.ChatRequest.Params == nil {
		req.ChatRequest.Params = &schemas.ChatParameters{}
	}
	req.ChatRequest.Params.ExtraParams = view.ExtraParams
	if ctx != nil && len(view.ExtraParams) > 0 {
		ctx.SetValue(schemas.BifrostContextKeyPassthroughExtraParams, true)
	}
	return req, nil, nil
}

// PostLLMHook is a no-op for this plugin.
func PostLLMHook(ctx *schemas.BifrostContext, resp *schemas.BifrostResponse, bifrostErr *schemas.BifrostError) (*schemas.BifrostResponse, *schemas.BifrostError, error) {
	return resp, bifrostErr, nil
}

// Cleanup releases resources on shutdown.
func Cleanup() error { return nil }

// resolvePrimary returns the live LLM slot model id, cached for cfg.TTLMillis.
//
// The bool return is "stale": true when the fresh health fetch/parse failed and
// we are serving the last-known-good cached slot instead. On the happy path it
// is false. An error is returned only when the fetch fails AND there is no
// cached value to fall back to (e.g. lemond down before the very first poll).
func resolvePrimary() (model string, stale bool, err error) {
	ttl := time.Duration(cfg.TTLMillis) * time.Millisecond

	mu.RLock()
	if cachedModel != "" && time.Since(cachedAt) < ttl {
		m := cachedModel
		mu.RUnlock()
		return m, false, nil
	}
	mu.RUnlock()

	mu.Lock()
	defer mu.Unlock()
	// Re-check after acquiring the write lock (another goroutine may have refreshed).
	if cachedModel != "" && time.Since(cachedAt) < ttl {
		return cachedModel, false, nil
	}

	m, ferr := fetchPrimary()
	if ferr != nil {
		// Fresh resolve failed. Fall back to the last-known-good slot if we have
		// one (stale-but-serving) rather than erroring the caller into a 404.
		if cachedModel != "" {
			return cachedModel, true, nil
		}
		return "", false, ferr
	}
	cachedModel = m
	cachedAt = time.Now()
	return m, false, nil
}

// fetchPrimary does one live lemond /health poll + parse. Caller holds mu.
func fetchPrimary() (string, error) {
	resp, err := httpClient.Get(cfg.HealthURL)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return normalize.ParseLoadedLLM(body)
}
