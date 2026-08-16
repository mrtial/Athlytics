"""Athlytics AI Coach & Actionable MCP Server.

Provides bidirectional tools (reading metrics/trends, writing targets and plans),
living dynamic context resources (athlytics://), and evidence-based workflow prompts.
"""
import os
from contextlib import contextmanager
from pathlib import Path

from mcp.server import MCPServer

from core.storage.db import connect

DB_PATH_ENV_VAR = "ATHLYTICS_DB_PATH"
DEFAULT_DB_PATH = Path.home() / ".athlytics" / "athlytics.db"

mcp = MCPServer("Athlytics")


def _db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH)))


@contextmanager
def _connection():
    conn = connect(_db_path())
    try:
        yield conn
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
