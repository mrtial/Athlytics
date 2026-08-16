FROM python:3.11-slim

WORKDIR /app

# Install the project and every runtime dependency declared in
# pyproject.toml (cryptography, python-dotenv, garminconnect, fastapi,
# uvicorn[standard], jinja2, python-multipart, mcp -- assembled across
# Plans 1/2/4/5; see pyproject.toml, the single source of truth). No
# separate requirements.txt. This also installs core/app/mcp_server as
# real site-packages (Task 1's packaging fix makes mcp_server part of
# that install); the full source tree copied below is a second,
# belt-and-suspenders way the same three packages are importable from
# /app regardless of packaging correctness.
COPY . .
RUN pip install --no-cache-dir .

# The FastAPI app (app.main:create_production_app) serves the dashboard
# here. Its own lifespan hook starts the in-process background sync
# scheduler (app.sync.BackgroundSyncScheduler, a daemon thread) --
# there is no separate process or command for the scheduler.
EXPOSE 8000

# GET /login is registered unconditionally by app/routes/auth.py
# regardless of onboarding/admin state (Plan 4 Task 4), so it's a safe,
# credential-free healthcheck target that's always reachable once the
# server is up. Uses stdlib urllib rather than curl/wget, neither of
# which python:3.11-slim installs by default.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/login', timeout=3).status == 200 else 1)"

# Default command: the dashboard/API server + background scheduler.
# The MCP server (mcp_server/server.py) is deliberately NOT started
# here -- per the design doc's Deployment section, it is a separate
# stdio entrypoint launched on demand by the user's AI client (via
# `docker exec` against this running container, or `docker run` with a
# different command against this same image), never a long-lived
# compose service. See DEPLOYMENT.md.
CMD ["uvicorn", "app.main:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
