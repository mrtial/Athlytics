import secrets
import sqlite3

PERSONAS: list[str] = [
    "endurance_runner",
    "strength_general_fitness",
    "sleep_recovery_focus",
    "full_overview",
]

THEMES: list[str] = ["light", "dark"]

SKINS: list[str] = ["lime", "cyan", "amber", "blue", "violet", "pink", "coral", "teal"]

DEFAULT_PERSONA = "full_overview"
DEFAULT_THEME = "light"
DEFAULT_SKIN = "lime"

# Every metric_type string below is one of GarminProvider's 18 registered
# canonical types (core/providers/garmin.py's _registry, verified directly
# against the merged Plan 2 code) -- analytics/dashboard widgets operate on
# these generic metric_type strings, never Garmin-specific shapes.
PERSONA_METRIC_TYPES: dict[str, list[str]] = {
    "endurance_runner": [
        "vo2max",
        "resting_hr",
        "hrv",
        "training_load",
        "activity_distance",
        "activity_duration",
        "race_predictor_5k",
        "race_predictor_10k",
        "race_predictor_half_marathon",
        "race_predictor_marathon",
    ],
    "strength_general_fitness": [
        "weight",
        "steps",
        "activity_calories",
        "activity_duration",
        "training_load",
        "resting_hr",
        "exercise_minutes",
        "walking_asymmetry",
        "walking_steadiness",
    ],
    "sleep_recovery_focus": [
        "sleep_score",
        "hrv",
        "body_battery",
        "stress",
        "resting_hr",
        "respiration",
        "sleep_duration",
        "mindful_minutes",
        "stand_hours",
    ],
    "full_overview": [
        "resting_hr",
        "hrv",
        "vo2max",
        "body_battery",
        "weight",
        "sleep_score",
        "steps",
        "stress",
        "respiration",
        "spo2",
        "training_load",
        "race_predictor_5k",
        "race_predictor_10k",
        "race_predictor_half_marathon",
        "race_predictor_marathon",
        "activity_duration",
        "activity_distance",
        "activity_calories",
        "sleep_duration",
        "mindful_minutes",
        "stand_hours",
        "exercise_minutes",
        "walking_asymmetry",
        "walking_steadiness",
    ],
}


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_setting (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def get_persona(conn: sqlite3.Connection) -> str | None:
    """None until explicitly set -- app.dependencies.onboarding_status relies
    on this to detect the persona onboarding step is still incomplete.
    Does NOT fall back to DEFAULT_PERSONA (that default is only used to
    pre-fill the settings form, Task 10)."""
    return get_setting(conn, "persona")


def get_theme(conn: sqlite3.Connection) -> str | None:
    """Same contract as get_persona: None until explicitly set."""
    return get_setting(conn, "theme")


def set_persona(conn: sqlite3.Connection, persona: str) -> None:
    if persona not in PERSONAS:
        raise ValueError(f"unknown persona: {persona!r}")
    set_setting(conn, "persona", persona)


def set_theme(conn: sqlite3.Connection, theme: str) -> None:
    if theme not in THEMES:
        raise ValueError(f"unknown theme: {theme!r}")
    set_setting(conn, "theme", theme)


def get_skin(conn: sqlite3.Connection) -> str | None:
    """Same contract as get_theme: None until explicitly set."""
    return get_setting(conn, "skin")


def set_skin(conn: sqlite3.Connection, skin: str) -> None:
    if skin not in SKINS:
        raise ValueError(f"unknown skin: {skin!r}")
    set_setting(conn, "skin", skin)


UNITS: list[str] = ["km", "mi"]
DEFAULT_UNIT = "km"
DEFAULT_ATHLETE_NAME = "Charlie Yang"
DEFAULT_ATHLETE_AGE = "28"


def get_unit(conn: sqlite3.Connection) -> str:
    return get_setting(conn, "unit") or DEFAULT_UNIT


def set_unit(conn: sqlite3.Connection, unit: str) -> None:
    if unit not in UNITS:
        raise ValueError(f"unknown unit: {unit!r}")
    set_setting(conn, "unit", unit)


def get_athlete_name(conn: sqlite3.Connection) -> str:
    return get_setting(conn, "athlete_name") or DEFAULT_ATHLETE_NAME


def get_athlete_age(conn: sqlite3.Connection) -> str:
    return get_setting(conn, "athlete_age") or DEFAULT_ATHLETE_AGE


def set_athlete_profile(conn: sqlite3.Connection, name: str, age: str) -> None:
    set_setting(conn, "athlete_name", name.strip() or DEFAULT_ATHLETE_NAME)
    set_setting(conn, "athlete_age", age.strip())


def generate_api_token() -> str:
    """Same generation scheme as app.session.create_session's session
    tokens -- url-safe so it drops into a Shortcuts header field or a
    query string without escaping."""
    return secrets.token_urlsafe(32)


def get_api_token(conn: sqlite3.Connection) -> str | None:
    """None until a token has been generated -- app/routes/settings.py
    auto-generates one on first Settings page view."""
    return get_setting(conn, "api_token")


def set_api_token(conn: sqlite3.Connection, token: str) -> None:
    set_setting(conn, "api_token", token)
