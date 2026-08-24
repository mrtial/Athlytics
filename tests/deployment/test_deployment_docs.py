import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
README_DOC = PROJECT_ROOT / "README.md"
DEV_DOC = PROJECT_ROOT / "docs" / "development.md"
AI_COACH_DOC = PROJECT_ROOT / "docs" / "ai-coach.md"


def _extract_json_blocks(markdown: str) -> list[str]:
    return re.findall(r"```json\n(.*?)\n```", markdown, re.DOTALL)


def test_readme_doc_exists():
    assert README_DOC.is_file()


def test_readme_documents_docker_compose_up():
    text = README_DOC.read_text()
    assert "docker compose up" in text


def test_docs_document_where_the_sqlite_volume_lives():
    text = DEV_DOC.read_text()
    assert "athlytics_data" in text
    assert "/data/athlytics.db" in text


def test_docs_document_secret_provisioning_without_a_manual_step():
    text = DEV_DOC.read_text()
    assert ".env" in text
    assert "automatic" in text.lower() or "no manual" in text.lower() or "generated" in text.lower()


def test_mcp_client_config_snippet_is_valid_json_with_the_expected_docker_exec_command():
    text = AI_COACH_DOC.read_text()
    blocks = _extract_json_blocks(text)
    matching = [b for b in blocks if "mcpServers" in b]
    assert matching, "expected a ```json code block containing an mcpServers config"

    config = json.loads(matching[0])
    athlytics = config["mcpServers"]["athlytics"]

    assert athlytics["command"] == "docker"
    assert athlytics["args"] == ["exec", "-i", "athlytics", "python", "-m", "mcp_server.server"]


def test_docs_document_a_docker_run_fallback_for_when_the_container_is_not_running():
    text = DEV_DOC.read_text()
    assert "docker run" in text
    assert "mcp_server.server" in text


def test_readme_documents_complete_deployment_and_features():
    readme_text = README_DOC.read_text()
    assert "Developer & Setup Guide" in readme_text
    assert "AI Coach & MCP Guide" in readme_text
    assert DEV_DOC.is_file()
    assert AI_COACH_DOC.is_file()

