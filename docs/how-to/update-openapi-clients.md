# Update OpenAPI Clients

Use this guide when changing any route path, request body, response model, status code, or operation id used by another repo.

## 1. Update the Backend Contract

From `kvarteret-personal`:

    kv openapi
    kv openapi --check

Commit `openapi.json` together with the backend change.

## 2. Update `kvarteret-internbevis`

From `/Users/kluvin/dev/kvarteret/kvarteret-internbevis`:

    npm run api:generate:personal

The generator reads the sibling `../kvarteret-personal/openapi.json` (or `KVARTERET_PERSONAL_OPENAPI`) and keeps only operations tagged `mobile-shifts`; `npm run api:check:personal` verifies the checked-in client. Run the app tests that cover the changed surface.

For mobile-card response changes, keep backend responses additive. Installed app versions can remain in use after the backend deploys. Do not remove fields or change parser-sensitive shapes until the supported app versions are confirmed.

## 3. Update `samfunnetibergen`

From `/Users/kluvin/dev/kvarteret/samfunnetibergen`:

    npm run api:sync

This runs `../infra/bin/kv openapi` and regenerates the generated client/snapshot.

## 4. Acceptance

The backend `openapi.json` is current, relevant sibling generated clients are updated, and consumer tests pass for the changed boundary.
