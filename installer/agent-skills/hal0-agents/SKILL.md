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
Auth: Bearer token (set `HAL0_MCP_TOKEN` env var)

Only Hermes can be run: `agent` is `hermes` (its default profile) or `hermes/<profile>`;
any other id, Pi included, and a `hermes/<profile>` the box does not have, is refused with
`bridge.unknown_agent`.

## Available Tools

### Agent Lifecycle
| Tool | Description |
|------|-------------|
| `agents_list` | List the agents hal0 can run, each Hermes profile's health, and whether an agent is runnable |
| `agent_run` | Start a run on a Hermes profile; returns its view with `run_id`. `wait_s` above 0 also waits |
| `agent_run_wait` | Block on an existing run until it ends, asks a question, or `timeout_s` passes |
| `agent_run_status` | A run's current view: status, output so far, error, and what it waits on |
| `agent_run_events` | A run's stored events after `since_seq`, oldest first |
| `agent_run_answer` | Answer the clarification question a run is waiting on |
| `agent_run_steer` | Add guidance to a run that is still running |
| `agent_run_stop` | Ask Hermes to stop a run |
| `agent_runs_list` | Your runs, newest first; filter with `status` |

### Agent Memory
| Tool | Description |
|------|-------------|
| `agent_memory_add` | Add a note to the box's shared memory on an agent's behalf (never its private memory) |
| `agent_memory_search` | Search the box's shared memory (never an agent's private memory) |

### Skills Management
| Tool | Description |
|------|-------------|
| `agent_skills_list` | List the skills Hermes loads (shipped, read-only; and operator drop-ins) |
| `agent_skill_get` | Get a skill's `SKILL.md` text and file names |
| `agent_skill_put` | Propose a drop-in skill; queued until the operator approves it |
| `agent_skill_delete` | Propose removing a drop-in skill; queued until the operator approves it |

### Capabilities
| Tool | Description |
|------|-------------|
| `agent_capabilities` | What a Hermes agent can do now: models, toolsets, MCP server names, approvals mode |

## Common Patterns

### List all agents
```
→ agents_list
  ← {agents: [{id, kind, runnable, profiles}]}
```

### Run an agent and wait for the result
```
→ agent_run
  agent: "hermes"
  input: "Summarize the hal0 slot status"
  wait_s: 120
  ← run view; status is "completed" when it finished, output holds the answer
```
If the view says `timed_out`, the run is still going: call `agent_run_wait` again.

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

1. **Check `agent_runs_list` first** (it lists your own runs only) before starting a run — avoid duplicates
2. **Use `agent_run` with `wait_s` for quick queries**, `agent_run` then `agent_run_wait` or `agent_run_status` for long-running tasks
3. **Stop agents** that appear stuck or idle for too long
4. **Use `agent_memory_add` and `agent_memory_search`** for notes about agents; they use the box's shared memory, not a Hermes agent's private memory
5. **Never store secrets** in agent memory or skills
6. **A skill put is a proposal** — it is not live until the operator approves it, and a shipped skill cannot be changed; use `agent_skill_get` first to read before proposing a change
