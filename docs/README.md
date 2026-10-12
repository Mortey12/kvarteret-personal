# Kvarteret Personal Documentation

This folder is the canonical documentation home for `kvarteret-personal` and for the Kvarteret system boundaries that this service participates in.

The docs are organized by purpose:

- `tutorials/` teaches a first-time workflow.
- `how-to/` gives task-oriented commands for people who already know what they need to do.
- `reference/` contains lookup facts: APIs and configuration.
- `explanation/` explains how the system fits together and why the boundaries look the way they do.

## Start Here

- [First local run](tutorials/first-local-run.md)
- [Local development](how-to/local-development.md)
- [Update OpenAPI clients](how-to/update-openapi-clients.md)
- [Deploy and runtime checks](how-to/deploy-and-runtime-checks.md)
- [Run the tests](how-to/run-tests.md)
- [API boundaries](reference/api-boundaries.md)
- [Configuration](reference/configuration.md)
- [Kvarteret Personal architecture](explanation/kvarteret-personal-architecture.md)
- [The volunteer application lifecycle](explanation/volunteer-application-lifecycle.md)

## Canonical Ownership

`kvarteret-personal` owns:

- the FastAPI application at `personal.samfunnetibergen.no`
- the checked-in OpenAPI contract in `openapi.json`
- Supabase Postgres migrations under `migrations/`
- the personnel admin UI
- the mobile-card API used by the volunteer app
- public volunteer prospect registration (including group signup with friend invitations)
- media proxy routes for private personnel photos
- the Spotify-backed now-playing API

Sibling repositories consume some of these APIs. Their usage is documented in [API boundaries](reference/api-boundaries.md).
