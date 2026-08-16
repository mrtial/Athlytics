# Athlytics AI Coach — Client Setup Guide

## 1. Claude Desktop Setup
Add the Athlytics MCP server to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "/absolute/path/to/athlytics/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "ATHLYTICS_DB_PATH": "/Users/USERNAME/.athlytics/athlytics.db"
      }
    }
  }
}
```

## 2. Claude Code Setup
Configure project-level MCP in `.mcp.json`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "python",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

## 3. Google Gemini CLI / Antigravity Setup
Add to your Gemini CLI or Antigravity MCP server definitions:

```json
{
  "athlytics": {
    "command": "python",
    "args": ["-m", "mcp_server.server"]
  }
}
```
