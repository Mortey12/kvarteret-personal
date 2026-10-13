# API Boundaries

This page is the canonical reference for API boundaries between `kvarteret-personal` and the Kvarteret repositories in scope.

## Source of Truth

FastAPI route declarations and Pydantic response models in `kvarteret-personal` are the source of truth for the HTTP API. The checked-in artifact is `openapi.json`.

After changing API routes, request models, response models, or operation IDs, run:

    kv openapi

Before committing an API change, run:

    kv openapi --check

Consumers that generate typed clients should regenerate from `openapi.json`, preferably from a sibling checkout during local development or from the `develop` branch artifact when consuming remotely.

## Boundary Summary

| Consumer | Personal routes | Auth |
| --- | --- | --- |
| `kvarteret-internbevis` | `/api/v1/mobile-card/*`, `GET /api/now-playing`, `POST /api/v1/feedback/` | mobile-card bearer session; none |
| `samfunnetibergen` | `POST /api/v1/volunteer-prospects`, `/api/v1/booking-requests*`, `/api/v1/event-interest/*`, `GET /api/v1/me/statistikk-tilgang`, `POST /api/v1/feedback/`, `GET /api/now-playing` | HMAC; forwarded session cookie; none |

## `kvarteret-internbevis`

The React Native app defaults to:

    EXPO_PUBLIC_KVARTERET_PERSONAL_API_BASE_URL=https://personal.samfunnetibergen.no/api/v1
    EXPO_PUBLIC_INTERNKORT_BASE_URL=https://personal.samfunnetibergen.no/api/v1/mobile-card

It generates a client from `openapi.json` into `src/core/api/kvarteret-personal`. The generator first prefers a sibling `../kvarteret-personal/openapi.json`, then falls back to the remote `develop` artifact.

### Mobile-card API

`POST /api/v1/mobile-card/access-codes`

Requests a one-time access code for an email address. The endpoint intentionally returns `202` for unknown or duplicate people so the caller cannot use it to enumerate valid volunteer emails. Rate limits are Postgres-backed (they hold across serverless instances) and return `429`. Codes are single-use and stored only as keyed hashes; a new code is generated for every request.

`POST /api/v1/mobile-card/sessions`

Exchanges an email and access code for a signed session token and a mobile-card profile. Current supported app versions pass `include_role_history=true` because older strict parsers needed a compatibility gate for additive response fields.

`GET /api/v1/mobile-card/me`

Reads the current profile with `Authorization: Bearer <mobile-card-session-token>`. The backend may return `X-Mobile-Card-Session-Token` when it renews the session.

`POST /api/v1/mobile-card/client-events/session-logout`

Accepts diagnostics when the mobile app logs a user out due to missing or invalid local credentials. This endpoint is write-only telemetry and returns `202`.

### Now-playing API

`GET /api/now-playing`

The app derives the personal base URL from the configured mobile-card base URL and calls this endpoint for shared Grondahls now-playing state. It expects `authorized`, `hasTrack`, `isPlaybackActive`, nullable track fields, and `connectUrl`.

## `samfunnetibergen`

The site defaults to:

    PERSONAL_APP_BASE_URL=https://personal.samfunnetibergen.no

`samfunnetibergen/apps/web/src/app/api/volunteer-prospects/route.ts` validates the
public recruitment form, then posts server-to-server to
`POST /api/v1/volunteer-prospects`.
Sanity choice slugs are forwarded unchanged. Most choices resolve directly
through Personal's stable, unique `groups.slug` column. Public bar-area choices
such as Halvtimen and Grøndahls instead resolve to a suggested role in the
operational Skjenkegruppen parent group. Personal snapshots both submitted
choice labels on the application so this many-to-one routing does not erase
what the applicant selected. Only the primary choice controls the suggested
group and role used during promotion; the optional secondary choice is review
metadata. Unknown slugs are rejected and public requests cannot create
organizational groups or roles.

Direct group slugs use the same deterministic Norwegian-safe generation rule
in both repositories. The explicit bar-area routing table is owned by Personal
because it maps public Sanity choices to Personal roles. An established group
slug is immutable even if the group's display name is later changed.

The request body accepts `friend_emails` (up to two). When present, the backend creates one ordinary application for the submitter and one ordinary application per friend, linked by invitation relationship records. Each friend gets a personal `/apply/{token}` link delivered by email; applications are reviewed and approved independently. Field-level validation errors for friend emails come back under `fieldErrors.friendEmails`.

### Signed website requests

Every server-to-server call from the website is signed with one shared,
server-only `PERSONAL_SIGNING_SECRET`. Each request type starts its canonical
message with its own purpose label, so a signature for one route never verifies
on another:

| Route | Canonical prefix | Verifier |
| --- | --- | --- |
| `POST /api/v1/volunteer-prospects` | `v2` (legacy `v1`) | `app/api/request_auth.py` |
| `POST /api/v1/booking-requests`, `…/prefill` | `booking-v1` | `app/api/booking_request_auth.py` |
| `POST /api/v1/event-interest/read`, `…/response` | `event-interest-v1` | `app/api/v1/event_interest.py` |

The website also signs browser-visible tokens (booking promotion links,
booking continuation and event-interest source cookies) with the same key under
their own `booking-promotion-v1:`, `booking-continuation-v1:` and
`event-interest-source-v1:` prefixes. Personal never verifies those.

Personal accepts `PERSONAL_SIGNING_SECRET` and
`PERSONAL_SIGNING_PREVIOUS_SECRET` for all signed website requests. The shared
key replaced the old volunteer-specific key; existing booking promotion links
remain valid because the shared key already had the same value. The website
derives volunteer client-key pseudonyms from the shared key with HKDF-SHA256 and
a fixed purpose string. `EVENT_INTEREST_SECRET` was retired on 2026-10-13.

### Volunteer-prospect request authentication

Personal accepts prospect creation only from a caller that holds the shared
`PERSONAL_SIGNING_SECRET`. The website signs the exact serialized JSON
body in
`samfunnetibergen/apps/web/src/lib/integrations/kvarteret-personal/volunteer-prospect-signing.ts`.
Personal verifies it in `app/api/request_auth.py` before the application service
runs. The secret is server-only and must never use a browser-visible
`NEXT_PUBLIC_*` variable.

Each request carries:

    X-Kvarteret-Timestamp: <Unix seconds>
    X-Kvarteret-Nonce: <lowercase UUID>
    X-Kvarteret-Idempotency-Key: <lowercase UUID>
    X-Kvarteret-Client-Key: v1=<64 lowercase HMAC-SHA256 hex characters>
    X-Kvarteret-Signature: v2=<64 lowercase HMAC-SHA256 hex characters>

The v2 signed message is the following newline-separated canonical form, with no
trailing newline:

    v2
    <timestamp>
    <nonce>
    <idempotency key>
    <client key>
    POST
    /api/v1/volunteer-prospects
    <SHA-256 hex digest of the exact body bytes>

The client key is an opaque `v1=` HMAC-SHA256 pseudonym derived by the website
from a browser-scoped visitor identifier when available, with a trusted IP and
user-agent fallback. The website never forwards, logs, or stores the raw
identity material. The separate client-key secret must not be shared with
Personal or exposed to the browser. Because the client key is bound into the
server-to-server v2 signature, a browser cannot choose a new key to evade the
limit through the Personal API.

Personal permits at most five minutes of clock skew and consumes each verified
nonce once through the Postgres-backed rate limiter. Missing, stale, altered,
malformed, and replayed requests fail before prospect creation. HMAC
authenticates the server holding the secret; it does not prove that a human
submitted the website form.

The exact body is capped at 16,384 bytes before JSON parsing. Personal then
rejects unknown fields and enforces these maxima: full name 201 characters,
email 254, phone 16, study institution 160, background details 2,000, each
choice slug 100, each friend email 254, and two friend emails. The website uses
matching or narrower limits for first and last names.

Every authenticated request counts against a shared Postgres fixed-window route
limit. V2 requests also count against the opaque client key. Defaults are 120
requests per route per minute and 10 per client per ten minutes. Exceeding a
limit returns `429` with `Retry-After`; a limiter storage failure fails closed
with `503`.

The idempotency UUID and an application-secret-keyed HMAC of the normalized
payload are stored transactionally with prospect creation. Reusing a key with
different normalized content returns `409`. Retrying identical content, even
with a different key, returns the original `registrationId` and complete
`registrationIds` batch and does not enqueue
duplicate emails or create duplicate friend applications. The idempotency
records, application, lifecycle events, and durable email rows commit atomically.
Deleting the application also removes its normalized-payload claim, so a later
genuine reapplication is not blocked by an orphaned deduplication result; the
original idempotency key remains bound to the same content.

New requests treat the optional second target as an independent application.
Existing volunteers and prospects with another target's application are allowed.
Active same-target applications are reused without overwriting the snapshot.
A different normalized request body can therefore refer to an existing
application and send an email continuation link. Public responses expose IDs
only, never tokens or the existing person's canonical profile. See
[The Volunteer Application Lifecycle](../explanation/volunteer-application-lifecycle.md).

The v2 request includes a client-key pseudonym derived from the shared signing
secret. No independent client-key secret is configured or stored.

To rotate the signing secret without downtime, put the new value in Personal's
`PERSONAL_SIGNING_SECRET` and the old value in
`PERSONAL_SIGNING_PREVIOUS_SECRET`; deploy Personal, switch the website
to the new value, verify submissions, and finally remove the previous value.
There is deliberately no unsigned compatibility mode.

## Booking request capture

The website now calls `POST /api/v1/booking-requests` before forwarding room or
karaoke requests to Crescat. Personal owns the private snapshot archive and
migration; the website owns form validation, availability checks, request
building, and Crescat dispatch.

See [Booking request storage](booking-requests.md) for the signed contract,
deduplication, access, and deployment sequence.
