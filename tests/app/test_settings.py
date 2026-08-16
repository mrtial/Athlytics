import pytest

from app.db import ensure_app_schema
from app.settings import (
    DEFAULT_PERSONA,
    DEFAULT_THEME,
    PERSONA_METRIC_TYPES,
    PERSONAS,
    THEMES,
    get_persona,
    get_setting,
    get_theme,
    set_persona,
    set_setting,
    set_theme,
)
from core.storage.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    ensure_app_schema(c)
    return c


def test_get_setting_returns_none_when_unset(conn):
    assert get_setting(conn, "persona") is None


def test_set_then_get_setting_roundtrips(conn):
    set_setting(conn, "persona", "endurance_runner")

    assert get_setting(conn, "persona") == "endurance_runner"


def test_set_setting_overwrites_existing_value(conn):
    set_setting(conn, "theme", "light")
    set_setting(conn, "theme", "dark")

    assert get_setting(conn, "theme") == "dark"


def test_get_persona_none_until_set_then_roundtrips(conn):
    assert get_persona(conn) is None

    set_persona(conn, "sleep_recovery_focus")

    assert get_persona(conn) == "sleep_recovery_focus"


def test_set_persona_rejects_unknown_value(conn):
    with pytest.raises(ValueError, match="unknown persona"):
        set_persona(conn, "not_a_real_persona")


def test_get_theme_none_until_set_then_roundtrips(conn):
    assert get_theme(conn) is None

    set_theme(conn, "dark")

    assert get_theme(conn) == "dark"


def test_set_theme_rejects_unknown_value(conn):
    with pytest.raises(ValueError, match="unknown theme"):
        set_theme(conn, "sepia")


def test_all_four_personas_defined():
    assert set(PERSONAS) == {
        "endurance_runner",
        "strength_general_fitness",
        "sleep_recovery_focus",
        "full_overview",
    }


def test_every_persona_has_a_nonempty_metric_type_list():
    for persona in PERSONAS:
        assert persona in PERSONA_METRIC_TYPES
        assert len(PERSONA_METRIC_TYPES[persona]) > 0


def test_full_overview_includes_all_eighteen_garmin_metric_types():
    all_18 = {
        "resting_hr", "hrv", "vo2max", "body_battery", "weight", "sleep_score",
        "steps", "stress", "respiration", "spo2", "training_load",
        "race_predictor_5k", "race_predictor_10k", "race_predictor_half_marathon",
        "race_predictor_marathon", "activity_duration", "activity_distance", "activity_calories",
    }
    assert set(PERSONA_METRIC_TYPES["full_overview"]) == all_18


def test_defaults_are_valid_members():
    assert DEFAULT_PERSONA in PERSONAS
    assert DEFAULT_THEME in THEMES


def test_onboarding_persona_get_requires_admin_login(client):
    response = client.get("/onboarding/persona", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_onboarding_persona_post_sets_persona_and_redirects_to_theme(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/onboarding/persona", data={"persona": "endurance_runner"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/theme"
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    assert get_persona(conn) == "endurance_runner"


def test_onboarding_persona_post_rejects_unknown_value(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post("/onboarding/persona", data={"persona": "not_real"})

    assert response.status_code == 400


def test_onboarding_theme_post_sets_theme_and_redirects_to_connect(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})

    response = client.post("/onboarding/theme", data={"theme": "dark"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/connect"
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    assert get_theme(conn) == "dark"


def test_settings_get_requires_admin_login(client):
    response = client.get("/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_settings_get_shows_current_persona_and_theme(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "strength_general_fitness"})
    client.post("/onboarding/theme", data={"theme": "dark"})

    response = client.get("/settings")

    assert response.status_code == 200
    assert "strength_general_fitness" in response.text
    assert "dark" in response.text


def test_settings_post_persona_updates_and_redirects(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.post(
        "/settings/persona", data={"persona": "endurance_runner"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    assert get_persona(conn) == "endurance_runner"


def test_settings_post_theme_updates_and_redirects(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.post("/settings/theme", data={"theme": "dark"}, follow_redirects=False)

    assert response.status_code == 303
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    assert get_theme(conn) == "dark"


def test_settings_post_persona_rejects_unknown_value(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.post("/settings/persona", data={"persona": "not_real"})

    assert response.status_code == 400
