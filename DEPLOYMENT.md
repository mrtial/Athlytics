# Deploying Athlytics

This covers running the finished application via Docker. For local development
from source (no Docker), see `README.md`.

## Prerequisites

- Docker Engine with the Compose plugin (`docker compose version` should print
  something, not "command not found"). Docker Desktop on macOS/Windows includes
  this; on Linux, install `docker-compose-plugin` alongside Docker Engine.

## First run

From the project root:

```bash
docker compose up -d --build
```

This builds the image from the local `Dockerfile`, starts one container named
`athlytics`, and binds it to `http://localhost:8000` (override the host port
with `ATHLYTICS_PORT=8001 docker compose up -d --build`, or by creating a
`.env` file next to `docker-compose.yml` containing `ATHLYTICS_PORT=8001` --
Compose loads that file automatically).

Open `http://localhost:8000` in a browser and complete onboarding: create the
admin account, choose a persona and theme, then connect your Garmin account.
The initial full-history backfill starts automatically in the background once
you connect -- the dashboard is usable immediately, with data appearing as it
syncs (see the sync-status panel on the dashboard for progress and any auth
errors).

## Where your data lives

Everything persists in a single named Docker volume, `athlytics_data`, mounted
at `/data` inside the container:

- `/data/athlytics.db` -- the SQLite database (metric readings, sync
  checkpoints, admin account, sessions, settings, sync status).
- `/data/.env` -- the encryption secret key.
- `/data/garmin_credentials.enc` -- your encrypted Garmin credentials.
- `/data/garmin_tokens/` -- Garmin's cached session/OAuth token, so the app
  doesn't need to log in fresh on every sync.

Recreating the container (`docker compose up -d --build` after a rebuild, or
`docker compose restart`) does not touch this volume -- your data and
connection survive image rebuilds and upgrades. Removing it (`docker compose
down -v`) deletes everything permanently; do not run that unless you mean to
start over.

To back up the volume to a local tarball:

```bash
docker run --rm -v athlytics_data:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/athlytics-backup.tar.gz -C /data .
```

## How the encryption secret is provisioned

No manual step is required. `core.config.get_or_create_secret_key` runs the
first time the app starts, checks for `/data/.env` (inside the mounted
volume), and generates one automatically if it isn't there yet -- written with
restrictive file permissions, same as local development. Because `/data` is a
persistent volume, that generated secret survives container restarts and
rebuilds; only removing the volume (`docker compose down -v`) would lose it,
which would also make any previously-encrypted `garmin_credentials.enc` file
unreadable (you'd need to reconnect Garmin after that).

This is why the design doc's "mounted as a Docker secret/volume" is resolved
here as a plain persistent **volume**, not the literal `docker secret`
mechanism: `get_or_create_secret_key` reads and writes an ordinary `.env` file
at a path you give it, not `/run/secrets/*` (the Swarm-specific location
`docker secret` mounts at) -- there is nothing in the codebase for a Docker
secret to plug into. A volume containing that same `.env` file gives the
identical property the design doc actually wants (the secret persists across
container recreation, isn't baked into the image) with zero extra machinery.

## Connecting an AI client to the MCP server

The MCP server (`mcp_server/server.py`) is **not** started by
`docker compose up` -- per the design doc, it's a stdio subprocess your AI
client launches on demand, not a long-lived service. With the `athlytics`
container already running (from `docker compose up -d`), point your client at
it via `docker exec`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "docker",
      "args": ["exec", "-i", "athlytics", "python", "-m", "mcp_server.server"]
    }
  }
}
```

Where this JSON goes depends on your client:

- **Claude Desktop**: paste the `"athlytics": {...}` entry into the
  `mcpServers` object in your `claude_desktop_config.json`.
- **Claude Code**: run
  `claude mcp add athlytics -- docker exec -i athlytics python -m mcp_server.server`
  (equivalent to the JSON above, added via the CLI instead of hand-editing a
  config file).
- **Gemini CLI**: add the same `"athlytics": {...}` entry to the `mcpServers`
  object in your `settings.json`.

`docker exec -i` (no `-t`) attaches to the running container's stdin/stdout
without allocating a pseudo-TTY -- a `-t` pty would corrupt the MCP protocol's
JSON-RPC framing over stdio. The MCP server reads `/data/athlytics.db` inside
the container -- the exact same file the FastAPI app is writing to -- because
`docker-compose.yml` sets `ATHLYTICS_DB_PATH=/data/athlytics.db` in the
container's environment, matching the FastAPI app's own `ATHLYTICS_DATA_DIR`.

### If you don't keep the container running long-term

If you only run `docker compose up` occasionally rather than leaving the
container up continuously, use a one-off `docker run` against the same image
and volume instead of `docker exec`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "athlytics_data:/data",
        "-e", "ATHLYTICS_DB_PATH=/data/athlytics.db",
        "athlytics:latest",
        "python", "-m", "mcp_server.server"
      ]
    }
  }
}
```

This starts a short-lived container reusing the same named volume, runs the
MCP server against it, and removes the container (`--rm`) when the client
disconnects. It reads the same `/data` volume the main `athlytics` container
uses, so it sees the same data either way.

## Advanced: running without Compose

`docker compose` is the supported path (it wires the volume, port, and both
environment variables together correctly). If you need to run the image
directly:

```bash
docker build -t athlytics:latest .
docker run -d --name athlytics -p 8000:8000 \
  -v athlytics_data:/data \
  -e ATHLYTICS_DATA_DIR=/data \
  -e ATHLYTICS_DB_PATH=/data/athlytics.db \
  athlytics:latest
```

Omitting `-e ATHLYTICS_DATA_DIR=/data` (or the volume mount) means the app
falls back to its default, `./data` relative to the container's working
directory (`/app`) -- which is *inside* the container's writable layer, not a
volume, and is lost the next time the container is removed. Always set both
the volume and both environment variables together.

## Updating

```bash
docker compose down          # stops the container, keeps the volume
docker compose up -d --build # rebuilds the image, starts fresh
```

## Uninstalling

```bash
docker compose down -v       # stops the container AND deletes the volume
```

This permanently deletes your synced health data, encryption secret, and
Garmin connection. Back up first (see above) if you might want this data
again.
