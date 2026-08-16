import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"


def _dockerfile_text() -> str:
    return DOCKERFILE_PATH.read_text()


def test_dockerfile_exists():
    assert DOCKERFILE_PATH.is_file()


def test_dockerfile_uses_a_python_311_or_newer_base_image():
    text = _dockerfile_text()
    assert "FROM python:3.11" in text or "FROM python:3.12" in text


def test_dockerfile_exposes_port_8000():
    assert "EXPOSE 8000" in _dockerfile_text()


def test_dockerfile_default_command_runs_the_fastapi_app_via_uvicorn_factory():
    text = _dockerfile_text()
    assert "uvicorn" in text
    assert "app.main:create_production_app" in text
    assert "--factory" in text


def test_dockerfile_declares_a_healthcheck_against_an_unauthenticated_route():
    text = _dockerfile_text()
    assert "HEALTHCHECK" in text
    assert "/login" in text


def test_dockerfile_does_not_start_the_mcp_server_as_the_default_command():
    # Design doc: the MCP server is launched on demand by the user's AI
    # client, never as a long-lived compose/container service.
    text = _dockerfile_text()
    cmd_lines = [line for line in text.splitlines() if line.startswith("CMD")]
    assert len(cmd_lines) == 1
    assert "mcp_server" not in cmd_lines[0]


def _docker_daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _docker_daemon_available(), reason="Docker daemon is not available in this environment")
def test_docker_build_succeeds():
    result = subprocess.run(
        ["docker", "build", "-t", "athlytics:test", str(PROJECT_ROOT)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
