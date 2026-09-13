# Contributing to Athlytics

Thanks for considering a contribution. Athlytics is a self-hosted, single-maintainer
project so far — real-world testing and new integrations from people running it on
their own data are exactly what it needs most right now.

## Where help is most valuable right now

- **Non-Garmin data sources need real-world testing.** Development so far has been
  driven mainly by a Garmin account. Strava, Apple Health, and Tonal have unit-test
  coverage but limited live testing; **Mi Fitness has none yet** — if you connect one
  of these and hit a bug (wrong units, a missed metric, an auth flow that doesn't
  match your account/region), please open an issue with the error and, if you can,
  a redacted sample of the API/export payload that triggered it.
- **New data sources.** Whoop, Oura, Polar, Fitbit, Wahoo, Coros, etc. The provider
  interface (below) was deliberately kept small so this is a contained addition, not
  a rewrite.
- **CI.** There's no GitHub Actions workflow yet — the test suite (650+ tests, fully
  offline/mocked) only runs locally. Adding a `pytest` workflow that runs on every PR
  is a small, high-value first contribution.
- Sports-science formula review (`core/analytics/`), UI/accessibility polish, and
  documentation fixes are all welcome too.

## Getting set up

Full local/Docker setup, running the MCP server, and the test suite are covered in
the **[Developer & Setup Guide](docs/development.md)**. The short version:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

There's no CI yet, so **run `pytest -v` yourself before opening a PR** — it's the
only signal a reviewer has that nothing broke.

## Ground rules

- Keep `core/` free of web-framework and MCP-framework imports — it should stay
  usable from `app/` (FastAPI), `mcp_server/`, and tests without dragging either in.
- Store all timestamps as naive UTC on `MetricReading` / `Activity`
  (`core/storage/models.py`); convert at the edges (see
  `core/providers/apple_health.py`'s `parse_apple_health_timestamp` for the pattern
  when a source gives you offset-aware local times).
- Never store a credential/token/secret outside `CredentialStore`
  (`core/security/credentials.py`) — it's the only place plaintext touches disk, and
  only briefly, Fernet-encrypted at rest.
- A provider sync must be idempotent and resumable: `repository.upsert_readings` /
  `upsert_activities` dedup on (source, metric_type, timestamp) / activity id, and
  `sync_all_metrics` (`core/scheduler/sync.py`) resumes from a per-metric-type
  checkpoint rather than re-fetching everything on every pass. Don't bypass either.
- No speculative abstraction — match the existing providers' shape rather than
  introducing a new pattern for one new source.
- Comments should explain *why*, not *what* — see any existing provider file for the
  house style (a comment exists to record a non-obvious constraint or a verified
  fact about a third-party API/library, not to restate the code).

## Architecture: adding a new data source

Every data source implements one of two small `Protocol`s in
`core/providers/base.py`:

```python
class Provider(Protocol):
    name: str
    def supported_metric_types(self) -> list[str]: ...
    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]: ...

class ImportProvider(Protocol):
    name: str
    def ingest(self, payload: bytes) -> Iterator[MetricReading]: ...
```

Use `Provider` for anything with an API you can poll on a schedule (credentials form
or OAuth — see `core/providers/garmin.py` for credentials-form, `strava.py` for
OAuth). Use `ImportProvider` for a one-shot file upload with no ongoing sync (see
`apple_health.py`). Pick the closest existing provider as your template rather than
starting from a blank file — `tonal.py`/`tonal_client.py` is the most recently
written and probably the clearest reference for a new credentials-form `Provider`.

For a **polled `Provider`**, wiring a new source into the app means touching:

1. **`core/providers/<your_source>.py`** — the provider itself: auth/session
   handling, `supported_metric_types()`, `fetch()`, and a `<YourSource>AuthError`
   for a non-retryable auth failure (mirror `StravaAuthError` / `GarminAuthError`).
   Raise `core.providers.base.RateLimitError` for a retryable 429 — the scheduler
   already backs off and retries on it (`core/scheduler/sync.py::_fetch_with_backoff`).
2. **`core/providers/registry.py`** — add a `ProviderInfo` entry to
   `PROVIDER_REGISTRY` with the right `flow_type` and an `is_connected` function.
3. **`app/sync.py::perform_sync_pass`** — add a credential-store parameter and a
   construct-and-call-`_run_provider_sync` block, following the existing
   Garmin/Strava/Mi Fitness/Tonal blocks.
4. **`app/main.py`** — construct the new `CredentialStore` and set it on
   `app.state`, and pass it through to `perform_sync_pass`.
5. **`app/data_sources.py`** + **`app/routes/data_sources.py`** — a `connect_*`
   function that saves credentials and validates them with one real call (see
   `connect_tonal`), plus a route to call it.
6. **`app/templates/partials/connect_sources.html`** — the connection form/card.
7. Tests under `tests/providers/` (provider logic, mocking the third-party
   client/API — see `tests/providers/test_tonal.py`) and `tests/app/` (the route).

An **`ImportProvider`** only needs steps 1 (implementing `ingest()`), 2, an
`import_*` function in `app/data_sources.py` (see `import_apple_health`), a route,
and tests — no `perform_sync_pass`/`app/main.py` changes, since there's nothing to
poll on a schedule.

## Opening a PR

1. Branch off `main` with a descriptive name.
2. Add tests for any new logic — this codebase leans heavily on its test suite in
   place of CI, so untested new code is a much harder review.
3. Run `pytest -v` and make sure it's green.
4. Open the PR with a description of the problem and what you tested it against
   (e.g. "tested live against my own Whoop account for 2 weeks of data").

No CLA, no formal code of conduct beyond the obvious: be respectful, assume good
faith, keep discussion about the code.
