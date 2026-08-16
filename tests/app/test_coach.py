from datetime import date, datetime

from app.db import ensure_app_schema
from core.storage import repository
from core.storage.db import connect
from core.storage.models import CoachNote


def _note(**overrides):
    defaults = dict(
        id="n1",
        date=date(2026, 8, 16),
        category="milestone",
        note="Created a 30-week training plan.",
        tags_json=None,
        created_at=datetime(2026, 8, 16, 22, 26),
    )
    defaults.update(overrides)
    return CoachNote(**defaults)


def test_coach_route_requires_admin_login(client):
    response = client.get("/coach", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_coach_route_shows_empty_state_with_no_notes(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/coach")

    assert response.status_code == 200
    assert "No coach sessions" in response.text


def test_coach_route_renders_saved_note(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    repository.save_coach_note(conn, _note())
    conn.close()

    response = client.get("/coach")

    assert response.status_code == 200
    assert "Created a 30-week training plan." in response.text
    assert "Milestone" in response.text
    assert "Aug 16, 2026" in response.text
