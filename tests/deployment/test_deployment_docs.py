import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOYMENT_DOC = PROJECT_ROOT / "DEPLOYMENT.md"
README_DOC = PROJECT_ROOT / "README.md"


def _extract_json_blocks(markdown: str) -> list[str]:
    return re.findall(r"```json\n(.*?)\n```", markdown, re.DOTALL)


def test_deployment_doc_exists():
    assert DEPLOYMENT_DOC.is_file()


def test_deployment_doc_documents_docker_compose_up():
    text = DEPLOYMENT_DOC.read_text()
    assert "docker compose up" in text


def test_deployment_doc_documents_where_the_sqlite_volume_lives():
    text = DEPLOYMENT_DOC.read_text()
    assert "athlytics_data" in text
    assert "/data/athlytics.db" in text


def test_deployment_doc_documents_secret_provisioning_without_a_manual_step():
    text = DEPLOYMENT_DOC.read_text()
    assert ".env" in text
    assert "automatic" in text.lower() or "no manual" in text.lower() or "generated" in text.lower()


def test_mcp_client_config_snippet_is_valid_json_with_the_expected_docker_exec_command():
    text = DEPLOYMENT_DOC.read_text()
    blocks = _extract_json_blocks(text)
    matching = [b for b in blocks if "mcpServers" in b]
    assert matching, "expected a ```json code block containing an mcpServers config"

    config = json.loads(matching[0])
    athlytics = config["mcpServers"]["athlytics"]

    assert athlytics["command"] == "docker"
    assert athlytics["args"] == ["exec", "-i", "athlytics", "python", "-m", "mcp_server.server"]


def test_deployment_doc_documents_a_docker_run_fallback_for_when_the_container_is_not_running():
    text = DEPLOYMENT_DOC.read_text()
    assert "docker run" in text
    assert "mcp_server.server" in text


def test_readme_links_to_deployment_doc_instead_of_claiming_docker_is_unbuilt():
    text = README_DOC.read_text()
    assert "DEPLOYMENT.md" in text
    assert "Docker packaging" not in text
