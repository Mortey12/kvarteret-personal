# Configuration Reference

Configuration is loaded by `app/config.py` through Pydantic settings. Values come from environment variables and, in local development, `.env`.

Do not commit real secrets. The checked-in `.env.example` should contain names and safe placeholders only.

## Core Runtime

| Setting | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | required | Runtime environment: `development`, `test`, or `production`. Startup fails when it is omitted or invalid. |
| `APP_SECRET_KEY` | `change-me` | Signs web sessions, media tokens, mobile-card tokens, CSRF tokens, and Spotify OAuth state. In production it must be non-default and at least 32 characters. |
| `APP_PUBLIC_BASE_URL` | unset | Set `https://personal.samfunnetibergen.no` in production. Public base URL used in email links, media URLs, and OAuth redirects. Production requires an HTTPS origin without credentials, a path, query, or fragment. |
| `PERSONAL_SIGNING_SECRET` | unset | Active shared secret that authenticates every signed request from `samfunnetibergen`: volunteer prospects, booking requests and event interest. Production requires at least 32 characters. Each request type signs a distinct canonical prefix, see [Website boundary](api-boundaries.md#signed-website-requests). |
| `PERSONAL_SIGNING_PREVIOUS_SECRET` | unset | Optional previous signing secret accepted temporarily during zero-downtime rotation. When set in production it requires at least 32 characters. |
| `VOLUNTEER_PROSPECT_HMAC_SECRET` | unset | Deprecated 2026-10-13; remove on or after 2027-01-12. Same value as `PERSONAL_SIGNING_SECRET`. Still accepted for volunteer prospects and booking requests; satisfies the production requirement when `PERSONAL_SIGNING_SECRET` is unset. |
| `VOLUNTEER_PROSPECT_HMAC_PREVIOUS_SECRET` | unset | Deprecated 2026-10-13; remove on or after 2027-01-12. Previous value for `VOLUNTEER_PROSPECT_HMAC_SECRET`. |
| `EVENT_INTEREST_SECRET` | unset | Deprecated 2026-10-13; remove on or after 2027-01-12, once event-interest source cookies issued with it have expired. Still accepted for event interest. |
| `VOLUNTEER_PROSPECT_MAX_BODY_BYTES` | `16384` | Maximum exact request-body size accepted by the volunteer-prospect endpoint. Larger bodies return `413` before JSON parsing. Keep this synchronized with the website proxy. |
| `VOLUNTEER_PROSPECT_ROUTE_LIMIT` | `120` | Route-wide volunteer-prospect requests allowed per window. |
| `VOLUNTEER_PROSPECT_ROUTE_WINDOW_SECONDS` | `60` | Route-wide volunteer-prospect fixed-window duration. |
| `VOLUNTEER_PROSPECT_CLIENT_LIMIT` | `10` | Volunteer-prospect requests allowed for one opaque client key per window. |
| `VOLUNTEER_PROSPECT_CLIENT_WINDOW_SECONDS` | `600` | Per-client volunteer-prospect fixed-window duration. |
| `LOG_LEVEL` | `INFO` | Application log level. |
| `DEV_ADMIN_EMAIL` | unset | Development-harness admin email. Refused unless `APP_ENV=development`. |
| `DEV_ADMIN_PASSWORD` | unset | Development-harness admin password. Refused unless `APP_ENV=development`. |

## Database and Supabase

| Setting | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | unset | SQLAlchemy connection string for Supabase Postgres. |
| `DATABASE_USE_NULL_POOL` | `false` | Use SQLAlchemy `NullPool`; recommended for Vercel/serverless. |
| `DATABASE_POOL_SIZE` | `5` | Local pooled connection count when not using `NullPool`. |
| `DATABASE_MAX_OVERFLOW` | `5` | Extra local pooled connections. |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `10` | How long to wait for a pooled connection. |
| `DATABASE_POOL_RECYCLE_SECONDS` | `1800` | Connection recycle interval. |
| `SUPABASE_URL` | unset | Supabase project URL for Auth APIs. |
| `SUPABASE_SECRET_KEY` | unset | Supabase secret/service key for backend Auth operations. |

## Storage

| Setting | Default | Purpose |
| --- | --- | --- |
| `AZURE_BLOB_CONNECTION_STRING` | unset | Enables Azure Blob photo storage and signed URLs. Required for media storage. |
| `AZURE_BLOB_ACCOUNT_NAME` | unset | Optional explicit account name for Azure photo SAS generation. |
| `AZURE_BLOB_ACCOUNT_KEY` | unset | Optional explicit account key for Azure photo SAS generation. |
| `AZURE_PHOTO_CONTAINER` | `images` | Azure Blob container for photos. |
| `PHOTO_UPLOAD_MAX_BYTES` | `41943040` | Maximum uploaded photo size. |
| `PHOTO_MAX_DIMENSION` | `2048` | Maximum processed photo dimension. |
| `PHOTO_DEFAULT_SIZE` | `512` | Default served photo size. |

When `APP_ENV=development` and Azure is not configured, the local harness
stores photos under `.devdata/photos/`.

## Email

Outbound email uses Resend's HTTPS API through `app/infrastructure/email/resend.py`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `RESEND_API_KEY` | unset | Server-only Resend sending key, scoped to the sender domain. |
| `EMAIL_SENDER_NAME` | `Samfunnet i Bergen` | Display name in outbound email. |
| `EMAIL_SENDER_EMAIL` | `hallaien@samfunnetibergen.no` | Verified sender address. |

Store `RESEND_API_KEY` in the deployment's secret environment, never in Git or
public frontend variables. Old `SMTP_*` and `Email__*` transport settings are no
longer used. Supabase Auth's SMTP settings are separate from this application's
HTTPS transport.

When `APP_ENV=development` and `RESEND_API_KEY` is unset, outbound HTML is written
to `.devdata/outbox/`. Other environments require a Resend key.

The volunteer transactional retry queue remains enabled: domain changes and
email requests commit together, and the existing dispatch cron submits them to
Resend. `EmailOutboxService` sends a stable `email-delivery/<uuid>` idempotency key
for each delivery; API errors and rate limits retain the existing retry schedule.
Automatic replays stop 23 hours after the first attempt, conservatively within
[Resend's 24-hour deduplication window](https://resend.com/docs/dashboard/emails/idempotency-keys).
Resend acceptance means queued by the provider, not delivery to the recipient.
The database's historical `smtp` stage and nullable SMTP status columns remain
for compatibility with existing delivery history; new HTTP errors use Resend
categories and leave SMTP status empty. A changed recipient or inviter name can
change a retry payload; Resend rejects that key/payload mismatch permanently,
requiring explicit admin recovery. A manual retry creates a successor delivery
with a new key and can send another message if an earlier acceptance was uncertain.

Email authors use typed React Email components under `app/templates/emails/react/`.
`bun run build:emails` type-checks them, runs
`scripts/compile_react_email_templates.tsx`, and rebuilds the preview gallery.
The build adapter restores Jinja variables in the generated HTML; Python's
existing renderers still autoescape runtime values and handle the optional admin
greeting. React is used only during the asset build, not by the Python service.
`bun run preview:emails` opens the React Email authoring preview on port 3001.

The shared layout uses a fluid table with a 600px maximum width, wrapping text
and buttons, and safe font fallbacks. Brand font declarations are embedded from
`app/templates/emails/react/fonts.ts`. The logo PNG and font files are bundled
under `app/static/email` and served from `personal.samfunnetibergen.no`, alongside
their font licenses and source manifest, so email resources align with the
sending domain. Templates remain borderless. Imported
Resend template drafts are previews and are not required or published by the
delivery adapter.

## PostHog Observability

| Setting | Default | Purpose |
| --- | --- | --- |
| `POSTHOG_OBSERVABILITY_ENABLED` | `false` | Enables OTLP logs/traces and server exception issues. |
| `POSTHOG_PROJECT_TOKEN` | unset | Shared PostHog project token (`phc_`); required when enabled. |
| `POSTHOG_HOST` | `https://eu.i.posthog.com` | EU ingestion origin, not the PostHog dashboard URL. |
| `OTEL_SERVICE_NAME` | `kvarteret-personal` | Service filter in shared logs and error events. |

See [PostHog diagnostics](../how-to/posthog-diagnostics.md) for verification and
the distinction between application telemetry and Vercel drains.

## Spotify and Now Playing

| Setting | Default | Purpose |
| --- | --- | --- |
| `SPOTIFY_CLIENT_ID` | unset | Spotify OAuth client id. |
| `SPOTIFY_CLIENT_SECRET` | unset | Spotify OAuth client secret. |
| `SPOTIFY_REFRESH_TOKEN` | unset | Fallback refresh token if no database token exists. |
| `NOW_PLAYING_CACHE_SECONDS` | `10.0` | Fresh cache window for now-playing data. |
| `NOW_PLAYING_STALE_GRACE_SECONDS` | `30.0` | Grace window for serving stale data during refresh failure. |
| `SPOTIFY_NOW_PLAYING_ENABLED` | `false` | Kill switch for Spotify now-playing polling. When `false` the service never calls Spotify and `GET /api/now-playing` reports an authorized-but-idle state. Temporarily off by default while a recurring upstream `HTTPStatusError`/`413` retry loop is investigated; set to `true` once resolved and verified. |

## Sessions, Caches, and Mobile-card Limits

| Setting | Default | Purpose |
| --- | --- | --- |
| `SESSION_COOKIE_NAME` | `kvarteret_session` | Web admin session cookie name. |
| `SESSION_COOKIE_DOMAIN` | unset | Optional cookie domain, e.g. `.samfunnetibergen.no`, for sharing the session across trusted subdomains. The domain is only applied when the request host equals or is a subdomain of it; requests through another alias (for example `personal.kvarteret.no`) fall back to a host-only cookie, because browsers reject a `Domain` attribute that does not match the request host. |
| `SESSION_TTL_HOURS` | `12` | Web session lifetime. |
| `SESSION_CACHE_TTL_SECONDS` | `300` | In-process session cache lifetime. |
| `PENDING_VOLUNTEER_APPLICATIONS_CACHE_TTL_SECONDS` | `30` | Pending application count cache lifetime. |
| `VOLUNTEER_DETAIL_CACHE_TTL_SECONDS` | `300` | Volunteer detail cache lifetime. |
| `ADMIN_ACCOUNTS_CACHE_TTL_SECONDS` | `60` | Admin account lookup cache lifetime. |
| `MOBILE_CARD_ACCESS_CODE_TTL_MINUTES` | `10` | Access-code lifetime. |
| `MOBILE_CARD_ACCESS_CODE_COOLDOWN_SECONDS` | `60` | Cooldown before sending/reusing another code. |
| `MOBILE_CARD_ACCESS_CODE_REQUEST_LIMIT` | `5` | Request limit per window/source. |
| `MOBILE_CARD_ACCESS_CODE_REQUEST_WINDOW_SECONDS` | `300` | Access-code request window. |
| `MOBILE_CARD_SESSION_ATTEMPT_LIMIT` | `5` | Failed session attempt limit. |
| `MOBILE_CARD_SESSION_ATTEMPT_WINDOW_SECONDS` | `600` | Failed session attempt window. |
| `MOBILE_CARD_SESSION_TTL_DAYS` | `90` | Signed mobile-card session lifetime. |
| `MOBILE_CARD_SESSION_RENEWAL_THRESHOLD_DAYS` | `30` | Threshold for returning a renewed session token. |

## Feedback and Review Bypass

| Setting | Default | Purpose |
| --- | --- | --- |
| `LINEAR_API_KEY` | unset | Linear API key used to create feedback issues. |
| `LINEAR_TEAM_ID` | unset | Linear team where feedback issues are created. |
| `LINEAR_PROJECT_ID_PERSONAL` | unset | Linear project for Personalplattformen feedback. |
| `LINEAR_PROJECT_ID_INTERNBEVIS` | unset | Linear project for internbevis app feedback. |
| `LINEAR_PROJECT_ID_NETTSIDE` | unset | Linear project for kvarteret.no feedback. |
| `LINEAR_STATE_ID_TRIAGE` | unset | Legacy Linear triage state setting. New feedback issues let Linear assign triage/default state. |
| `REVIEW_BYPASS_ENABLED` | `false` | Enables the app-store review bypass path for mobile-card review. |
| `REVIEW_BYPASS_EMAIL` | unset | Review bypass email. |
| `REVIEW_BYPASS_TOKEN` | unset | Review bypass token. |

The review bypass exists for app-store review compatibility. Keep it disabled unless the deployed mobile release flow requires it.
