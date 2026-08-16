from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_PATH = PROJECT_ROOT / "docker-compose.yml"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


def test_docker_compose_file_exists_and_is_valid_yaml():
    compose = _load_compose()
    assert isinstance(compose, dict)
    assert "services" in compose


def test_docker_compose_defines_exactly_one_service():
    compose = _load_compose()
    assert list(compose["services"].keys()) == ["app"]


def test_app_service_has_a_stable_container_name_for_docker_exec():
    compose = _load_compose()
    assert compose["services"]["app"]["container_name"] == "athlytics"


def test_app_service_builds_from_the_local_dockerfile():
    compose = _load_compose()
    assert compose["services"]["app"]["build"] == "."


def test_app_service_mounts_exactly_one_persistent_volume_at_data():
    compose = _load_compose()
    service = compose["services"]["app"]
    assert service["volumes"] == ["athlytics_data:/data"]
    assert "athlytics_data" in compose["volumes"]


def test_app_service_points_fastapi_app_and_mcp_server_at_the_same_db_file():
    compose = _load_compose()
    env = compose["services"]["app"]["environment"]
    assert env["ATHLYTICS_DATA_DIR"] == "/data"
    assert env["ATHLYTICS_DB_PATH"] == "/data/athlytics.db"


def test_app_service_exposes_port_8000_with_a_configurable_host_side():
    compose = _load_compose()
    ports = compose["services"]["app"]["ports"]
    assert len(ports) == 1
    assert ports[0].endswith(":8000")
    assert "ATHLYTICS_PORT" in ports[0]


def test_compose_defines_no_second_service_or_reverse_proxy():
    # Design doc Deployment section + Non-Goals: no separate DB container,
    # no reverse proxy/TLS termination in v1.
    compose = _load_compose()
    assert len(compose["services"]) == 1
