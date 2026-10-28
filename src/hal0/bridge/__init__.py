"""Agent bridge: remote coding agents reach hal0 through their own revocable key.

A bridge client (Claude Code, Codex, a CI job) authenticates with a
``hal0bk_`` key instead of a console session, calls hal0's MCP servers, and
delegates work to hal0's Hermes agent. See ADR-0043.
"""

from __future__ import annotations
