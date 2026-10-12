# First Local Run

This tutorial gets a new contributor from a fresh checkout to a running `kvarteret-personal` server.

## 1. Install Dependencies

From the repository root:

    uv sync
    bun install

If `bun` is not installed, install it first or use the project machine setup used by E-tjenesten. The frontend tooling builds Tailwind and email assets; the Python app uses `uv`.

## 2. Configure Local Environment

Create `.env` from the example if it does not already exist:

    cp .env.example .env

For a minimal local smoke run, the app can start without all third-party integrations. Protected auth, storage, email, and live database paths require real values for the relevant settings in [Configuration](../reference/configuration.md).

## 3. Start the App

Run:

    kv run

This starts Uvicorn with `app.main:create_app` as a factory.

Open:

    http://127.0.0.1:8000/health

Expected response:

    {"status":"ok"}

The root web page redirects to `/login` when there is no admin session. That is expected.

## 4. Run Tests

Run:

    kv test

For API contract work, also run:

    kv openapi --check

## 5. Where to Go Next

Use [Local development](../how-to/local-development.md) for daily commands, and [API boundaries](../reference/api-boundaries.md) before changing public API shape.
