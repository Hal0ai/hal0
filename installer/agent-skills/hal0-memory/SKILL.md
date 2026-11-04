---
name: hal0-memory
description: >
  Teach agents how to read/write/search/manage the hal0-Memory MCP -- a Hindsight-backed
  vector+graph memory bank system. Use for cross-session recall, agent coordination,
  project context sharing, identity card maintenance, durable long-term knowledge.
  Trigger: "store this in memory", "remember that", "what do we know about X",
  "check the memory bank", "update my identity card", "hal0 memory".

---

# Hal0-Memory MCP -- Usage Guide

## Available Tool Groups

Bank Management: bank/create, bank/list, bank/configure, bank/delete
Memory (agents dataset): agents/store, agents/retrieve, agents/search, agents/delete
Memory (facts dataset): facts/store, facts/retrieve, facts/search, facts/delete
Mental Models: mental-models/create, mental-models/apply, mental-models/list
Directives: directives/set, directives/list, directives/remove
Operations: operations/stats, operations/health

## Namespace Standards

Scope   | Pattern              | Example
Global  | global:<domain>      | global:hal0
Project | project:<name>       | project:rework
Agent   | agent:<agent_id>     | agent:hermes
Private | private:<agent_id>   | private:hermes

## Setting Up Your Private Bank

Check if your private bank exists:

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"bank/list","arguments":{}},"id":1}

If missing, create it:

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"bank/create","arguments":{"name":"private:hermes"}},"id":1}

## Storing a Fact

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"facts/store","arguments":{"bank":"private:hermes","content":"Your fact here","tags":["#hal0","#2026-07"]}},"id":1}

## Searching Memories

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"facts/search","arguments":{"bank":"global:hal0","query":"search terms","limit":5}},"id":1}

## Identity Card Maintenance

Store your identity card in global:hal0:

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"agents/store","arguments":{"bank":"global:hal0","agent_id":"hermes","content":"Hermes Agent orchestrator, minimax coordinator","capabilities":["orchestration","minimax-swarm","hal0-admin","hal0-memory"],"tags":["#hal0","#agent:hermes","#identity","#2026-07"]}},"id":1}

Check for peer agents:

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"agents/search","arguments":{"bank":"global:hal0","query":"agent identity","limit":10}},"id":1}

## Bank Configuration

Tune your bank settings:

  curl -s -X POST http://127.0.0.1:8080/mcp/memory/mcp \
    -H "Content-Type: application/json" \
    -d {"jsonrpc":"2.0","method":"tools/call","params":{"name":"bank/configure","arguments":{"name":"private:hermes","config":{"embedding_model":"builtin.bge-small-en-v1.5","chunk_size":512,"similarity_threshold":0.75}}},"id":1}

## When to Use

DO use when: cross-session recall, agent coordination, project context, identity cards, long-term knowledge.
DONNOT use when: ephemeral single-session data, need raw hindsight API (see hindsight-mcp skill), need admin operations (see hal0-admin skill).

