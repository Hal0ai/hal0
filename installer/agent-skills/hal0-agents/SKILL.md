---
name: hal0-agents
description: >
  Teach agents how to use the hal0-Agents MCP for agent orchestration — listing,
  launching, monitoring, stopping agents; managing agent skills; and agent-scoped
  memory. Trigger: "list agents", "run an agent", "start a worker", "spawn agent",
  "check agent status", "stop agent", "agent skills", "what agents are running",
  "agents mcp", "hal0 agents".

---

# Hal0-Agents MCP — Usage Guide

## MCP Endpoint

Remote (auth required): `https://hal0.thinmint.dev/mcp/agents/mcp`
Auth: Bearer token (set `HAL0_MCP_TOKEN` env var)

## Available Tools

### Agent Lifecycle
| Tool | Description |
|------|-------------|
| `agents_list` | List all registered agents with status |
| `agent_run` | Launch an agent (async), returns run ID |
| `agent_run_wait` | Launch and wait for completion (sync) |
| `agent_run_status` | Poll one run's state |
| `agent_run_events` | Stream events for a run |
| `agent_run_answer` | Get the final answer/message from a run |
| `agent_run_steer` | Send a steering message to a running agent |
| `agent_run_stop` | Stop a running agent |
| `agent_runs_list` | List past runs for an agent or globally |

### Agent Memory
| Tool | Description |
|------|-------------|
| `agent_memory_add` | Add a memory scoped to an agent |
| `agent_memory_search` | Search agent-scoped memories |

### Skills Management
| Tool | Description |
|------|-------------|
| `agent_skills_list` | List skills for an agent |
| `agent_skill_get` | Get a skill's full content |
| `agent_skill_put` | Create or update a skill |
| `agent_skill_delete` | Delete an agent skill |

### Capabilities
| Tool | Description |
|------|-------------|
| `agent_capabilities` | List agent capabilities (providers, tools, models) |

## Common Patterns

### List all agents
```
→ agents_list
  Returns: [{name, status, type, last_run, ...}]
```

### Launch an agent with a prompt
```
→ agent_run_wait
  agent_id: "hermes"
  prompt: "Summarize the hal0 slot status"
  → Returns final answer
```

### Launch agent async and poll
```
→ agent_run
  agent_id: "pi-agent"
  prompt: "Refactor the login module"
  → Returns {run_id: "run-abc123"}

→ agent_run_status
  run_id: "run-abc123"
  → {state: "running" | "completed" | "failed" | "stopped"}

→ agent_run_answer
  run_id: "run-abc123"
  → Final message from agent
```

### Check what an agent is doing
```
→ agents_list → shows active runs per agent
→ agent_runs_list agent_id: "hermes" → recent runs
→ agent_run_events run_id: "..." → live event stream
```

### Stop a runaway agent
```
→ agent_run_stop run_id: "run-abc123"
```

### Store agent-scoped memory
```
→ agent_memory_add
  agent_id: "hermes"
  text: "Completed hal0 setup — 3 MCP servers configured"
  tags: ["setup", "hal0", "mcp"]
```

### Search agent memory
```
→ agent_memory_search
  agent_id: "hermes"
  query: "hal0 setup"
  limit: 5
```

### Manage agent skills
```
→ agent_skills_list agent_id: "hermes"
→ agent_skill_get agent_id: "hermes" skill_name: "minimax-swarm"
→ agent_skill_put agent_id: "hermes" skill_name: "my-skill" content: "..."
→ agent_skill_delete agent_id: "hermes" skill_name: "old-skill"
```

## Rules

1. **Always check agents_list first** before launching new agents — avoid duplicates
2. **Use agent_run_wait for quick queries**, `agent_run` + poll for long-running tasks
3. **Stop agents** that appear stuck or idle for too long
4. **Prefer agent_memory** over hal0-memory for agent-scoped facts
5. **Never store secrets** in agent memory or skills
6. **agent_skill_put overwrites** — use `agent_skill_get` first to read before modifying

## Skill Triggers for Pi

When a user says any of these, read this skill and use the hal0-agents MCP:
- "list agents" / "what agents are running"
- "run agent X" / "spawn a worker" / "start agent"
- "check agent status" / "what is X doing"
- "stop agent X" / "kill the agent"
- "agent skills" / "add a skill to X"
- "agent memory" / "remember this for agent X"
