import tomllib
from pathlib import Path

from setuptools import find_packages

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def _load_include_patterns() -> list[str]:
    data = tomllib.loads(PYPROJECT_PATH.read_text())
    return data["tool"]["setuptools"]["packages"]["find"]["include"]


def test_pyproject_declares_core_app_and_mcp_server_packages():
    patterns = _load_include_patterns()

    assert "core*" in patterns
    assert "app*" in patterns
    assert "mcp_server*" in patterns


def test_setuptools_find_packages_discovers_mcp_server_under_declared_patterns():
    patterns = _load_include_patterns()

    discovered = find_packages(where=str(PROJECT_ROOT), include=patterns)

    assert "mcp_server" in discovered
    assert "core" in discovered
    assert "app" in discovered
