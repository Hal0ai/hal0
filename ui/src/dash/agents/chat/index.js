// hal0 v0.3 PR-10 — HermesChat barrel.
//
// Reserved for future symbol re-exports. The chat surface uses the
// window-globals registration pattern (Object.assign at the bottom of
// each .jsx / .js file), so this file currently only exists so a single
// `import './dash/agents/chat'` in main.tsx loads the suite without
// listing each file. main.tsx today imports the files individually for
// load-order explicitness; this barrel is the future consolidation
// point if/when we move to ES modules across dash/*.
//
// Window-globals published by this suite (see individual files):
//   useHermesSession         (use-hermes-session.js — store hook)
//   __hal0HermesSession      (use-hermes-session.js — connection api)
//   HermesMarkdown           (markdown.jsx)
//   HermesMessageBubble      (message-bubble.jsx)
//   HermesToolCallCard       (tool-call-card.jsx)
//   HermesApprovalCard       (approval-card.jsx)
//   HermesThinkingIndicator  (thinking-indicator.jsx)
//   HermesTranscript         (transcript.jsx)
//   HermesComposer           (composer.jsx)
//   HermesSidecar            (hermes-sidecar.jsx)
//   HermesChatTab            (../hermes-chat-tab.jsx — replaced by PR-10)
