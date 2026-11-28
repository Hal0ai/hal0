---
name: hal0-agents
description: >
  Teach agents how to use the hal0-Agents MCP for agent orchestration — listing,
  launching, monitoring, stopping agents; managing agent skills; and agent notes in
  shared memory. Trigger: "list agents", "run an agent", "start a worker", "spawn agent",
  "check agent status", "stop agent", "agent skills", "what agents are running",
  "agents mcp", "hal0 agents".

---

# Hal0-Agents MCP — Usage Guide

## MCP Endpoint

Remote (auth required): `https://<hal0-host>/mcp/agents/mcp`
Auth: Bearer token. hal0's own agents hold it in `HAL0_MCP_TOKEN`; a remote client uses a bridge key or the admin key.

Only Hermes can be run: `agent` is `hermes` (its default profile) or `hermes/<profile>`.
`agent_run` refuses any other id, Pi included, with `bridge.unknown_agent`, and a profile hal0 holds no key for with
`bridge.agent_unkeyed`; a keyed profile Hermes does not answer for fails with `bridge.agent_unavailable` or
`bridge.agent_refused`. The memory tools and `agent_capabilities` check that the profile exists and give
`bridge.unknown_agent` for one the box does not have.

## Available Tools

### Agent Lifecycle
| Tool | Description |
|------|-------------|
| `agents_list` | List the agents hal0 can run, each Hermes profile's health, and whether an agent is runnable |
| `agent_run` | Start a run on a Hermes profile; returns its view with `run_id`. `wait_s` above 0 (at most 600) also waits, as `agent_run_wait` does |
| `agent_run_wait` | Block on an existing run until it ends, asks a question, or `timeout_s` (default 120, at most 600) passes |
| `agent_run_status` | A run's current view: status, output so far, error, and what it waits on |
| `agent_run_events` | A run's stored events after `since_seq`, oldest first |
| `agent_run_answer` | Answer the clarification question a run is waiting on |
| `agent_run_steer` | Add guidance to a run that is still running |
| `agent_run_stop` | Ask Hermes to stop a run |
| `agent_runs_list` | Your own runs, newest first (an admin key sees every client's); filter with `status`; `limit` is at most 200 |

Statuses: `queued`, `running`, `waiting_for_approval`, `waiting_for_clarification`, `stopping`, `completed`, `failed`, `cancelled`, `interrupted`, `unknown`. The last five are final (`unknown`: hal0 lost track of the run); `error` holds the reason when a run fails.

### Agent Memory
| Tool | Description |
|------|-------------|
| `agent_memory_add` | Add a note to the box's shared memory on an agent's behalf (never its private memory) |
| `agent_memory_search` | Search the box's shared memory (never an agent's private memory); `limit` is 1 to 50 (default 10, other values are clamped) |

`agent` is `hermes`, `hermes/<profile>` or `shared`; every value uses the same shared memory, which other clients can read. Tags starting `agent:`, `visibility:`, `project:` or `source:bridge:` are dropped. `bridge.memory_disabled` means the box runs no memory.

### Skills Management
| Tool | Description |
|------|-------------|
| `agent_skills_list` | List the skills Hermes loads (shipped, read-only; and operator drop-ins) |
| `agent_skill_get` | Get a skill's `SKILL.md` text and file names |
| `agent_skill_put` | Propose a drop-in skill; queued until the operator approves it |
| `agent_skill_delete` | Propose removing a drop-in skill; queued until the operator approves it |

`agent_skill_put` creates or replaces a drop-in. `name` is lowercase letters, digits and hyphens (at most 64, starting with a letter or digit); `skill_md` starts with frontmatter holding the same `name` and a `description`. Limits: `skill_md` 16 KiB, the whole push 48 KiB, 16 files, UTF-8 text only. Secret-shaped text is refused unless it is a placeholder such as `<token>` or `$ENV_VAR`. Errors: `bridge.skill_invalid`, `bridge.skill_readonly` (a shipped skill), `bridge.skill_pending_elsewhere` (another client has a change to it waiting), `bridge.too_many_pending` (more than 8 of yours waiting), and `bridge.skill_not_found` (get, delete).

### Capabilities
| Tool | Description |
|------|-------------|
| `agent_capabilities` | What a Hermes agent can do now: models, toolsets, MCP server names, approvals mode |

## Common Patterns

### List all agents
```
→ agents_list
  ← {agents: [{id, kind, runnable, profiles: [{name, agent_id, health, keyed, model_id}]}]}
```
Pass a profile's `agent_id` as `agent` to `agent_run`; `health` is the HTTP status of its `/health`, and `keyed` says whether hal0 holds its key.

### Run an agent and wait for the result
```
→ agent_run
  agent: "hermes"
  input: "Summarize the hal0 slot status"
  wait_s: 120
  ← run view; status is "completed" when it finished, output holds the answer
```
`agent_run` with `wait_s` and `agent_run_wait` return when the run reaches a final status
(on `failed`, read `error`), when it asks a clarification question (`status` is
`waiting_for_clarification`; answer it, see below), or when the time runs out (`timed_out: true`, a field
of the view, not a status). A run in `waiting_for_approval` does not end the wait: the operator approves it.
After a timeout call `agent_run_wait` again, at most 3 times. If the run is still not final, tell the person its
`run_id` and `status`, and leave it running.

### Run async and poll
```
→ agent_run
  agent: "hermes"
  input: "Refactor the login module"
  ← {run_id: "run-abc123", status: ...}

→ agent_run_status
  run_id: "run-abc123"
  ← {status, output, error, pending, ...}

→ agent_run_wait
  run_id: "run-abc123"
  timeout_s: 300
  ← run view, events_tail, timed_out
```
Long output comes in 16 KiB windows: read the next one with `agent_run_status`
and `offset` set to the view's `next_offset`.
```
→ agent_run_status run_id: "run-abc123" offset: 16384
```

### Answer a run's question
A run waiting on a clarification has `status: "waiting_for_clarification"`, and its
`pending` field holds the question and a `request_id`.
```
→ agent_run_answer
  run_id: "run-abc123"
  request_id: "<request_id from pending>"
  answer: "Use the staging database"
```
A run waiting on an approval is the operator's to approve in the hal0 inbox.

### Check what an agent is doing
```
→ agents_list → each profile's health
→ agent_runs_list status: "running" → your runs that are running
→ agent_run_events run_id: "run-abc123" since_seq: 0 → the run's events
```

### Steer a running agent
```
→ agent_run_steer run_id: "run-abc123" input: "Leave the tests alone"
```

### See what an agent can do
```
→ agent_capabilities agent: "hermes"
```

### Stop a runaway agent
```
→ agent_run_stop run_id: "run-abc123"
```

### Store a note in shared memory
```
→ agent_memory_add
  agent: "hermes"
  text: "Completed hal0 setup — 3 MCP servers configured"
  tags: ["setup", "hal0", "mcp"]
```

### Search shared memory
```
→ agent_memory_search
  agent: "hermes"
  query: "hal0 setup"
  limit: 5
```

### Manage agent skills
```
→ agent_skills_list
→ agent_skill_get name: "hal0-bridge"
→ agent_skill_put name: "my-skill" skill_md: "---\nname: my-skill\ndescription: ...\n---\n..."
→ agent_skill_delete name: "old-skill"
```
`agent_skill_put` and `agent_skill_delete` return `{status: "pending_approval", approval_id, name}`
and change nothing until the operator approves the row in the hal0 inbox.

## Rules

1. **Check `agent_runs_list` first** (a bridge key lists its own runs only) before starting a run — avoid duplicates
2. **Use `agent_run` with `wait_s` for quick queries**, `agent_run` then `agent_run_wait` or `agent_run_status` for long-running tasks
3. **Do not stop a run in `waiting_for_clarification`**: it is idle because you owe it an answer, so answer it with `agent_run_answer` first. An unanswered question ends the run itself after `[bridge].clarify_timeout_s` (default 900 seconds, set from 60 to 3600). Stopping cannot be undone: stop only a `running` run that is no longer wanted or is clearly stuck
4. **Use `agent_memory_add` and `agent_memory_search`** for notes about agents; they use the box's shared memory, not a Hermes agent's private memory
5. **Never store secrets** in agent memory or skills
6. **A skill put is a proposal** — it is not live until the operator approves it, and a shipped skill cannot be changed; use `agent_skill_get` first to read before proposing a change
