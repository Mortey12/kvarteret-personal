# Kvarteret Personal

`kvarteret-personal` is the current FastAPI backend and server-rendered admin UI for Kvarteret personnel workflows.

It owns:

- the personnel admin UI at `personal.samfunnetibergen.no`
- Supabase Postgres migrations for personnel, auth-support, and registration tables
- the mobile-card API used by `kvarteret-internbevis`
- public volunteer prospect intake used by `samfunnetibergen`
- media proxy routes for private personnel photos
- the Spotify-backed now-playing API
- the checked-in OpenAPI contract in `openapi.json`

The documentation home is [docs/README.md](docs/README.md).

## Quick Start

Install dependencies:

    kv install

Run the full dev stack (Dockerized Postgres, migrations, seed, app, CSS watch):

    kv start

Typing `kvarteret` (or `kv`) with no arguments shows an interactive command
menu; `kv info` prints local URLs and credentials.

Check health:

    curl http://127.0.0.1:8000/health

Expected body:

    {"status":"ok"}

The root web page redirects to `/login` when there is no admin session.

## Common Commands

    kv start          # full dev stack in phrocs/mprocs panes
    kv info           # dev URLs, logins, passwords
    kv seed           # migrate + reseed the dev database
    kv nuke           # destroy the dev database and rebuild
    kv test
    kv lint           # ruff + import-linter contracts
    kv audit
    kv openapi --check
    kv smoke-auth

The `kv` CLI lives in the sibling [infra](../infra) repo and is on PATH via
[mise](https://mise.jdx.dev) (`mise trust`), or call `../infra/bin/kv`
directly. Use [Local development](docs/how-to/local-development.md) for
daily workflows.

## API Contract

FastAPI is the source of truth for the API contract. `openapi.json` is checked in so sibling clients can generate stable types from git history.

After changing API routes, request models, response models, or operation IDs:

    kv openapi

Before committing API changes:

    kv openapi --check

Consumer boundaries and client regeneration are documented in [API boundaries](docs/reference/api-boundaries.md) and [Update OpenAPI clients](docs/how-to/update-openapi-clients.md).

## External Systems

Direct runtime dependencies include Supabase Postgres, Supabase Auth, Azure Blob Storage, Resend, Linear, Spotify, PostHog, and Vercel.

See [Configuration](docs/reference/configuration.md).

## Deployment Shape

Vercel loads `api/index.py`, which exposes a module-level ASGI app from `app.main:create_app`.

The Vercel build runs the CSS/static preparation steps so `/static/...` assets are served from `public/static`, while non-static routes are rewritten to FastAPI.

Operational checks are documented in [Deploy and runtime checks](docs/how-to/deploy-and-runtime-checks.md).
