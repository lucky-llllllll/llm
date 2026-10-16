# Upgrade Prompt

This directory contains a standalone Python MCP adapter for the Go pet hospital REST API.

It intentionally uses the official SDK 2.x `MCPServer` and the 2026-07-28 protocol, not the old FastMCP 1.x programming model.

The server is stateless and exposes a single tool, `list_pets`, which adapts `GET /api/v1/pets`.
