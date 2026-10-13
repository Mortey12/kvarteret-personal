# Inspect Kvarteret Personal in PostHog

The shared EU project is [Samfunnet i Bergen, 202551](https://eu.posthog.com/project/202551).
Application logs use `kvarteret-personal` and `samfunnetibergen`; website console
logs use `samfunnetibergen-browser` after the browser update. Historical browser
logs remain under `posthog-browser-logs`.

## Find logs and exceptions

Open [Logs](https://eu.posthog.com/project/202551/logs), choose service
`kvarteret-personal`, and select the time range. Filter severity to error/warn.
New log records expose sanitized fields such as `event`, `error_category`,
`registration_id`, and `request_id` as attributes. OTLP bodies contain the event name; stdout retains JSON.
Use `trace_id` or `registration_id` to correlate volunteer intake across apps.

Open [Error Tracking](https://eu.posthog.com/project/202551/error_tracking) and
filter event property `service` to `kvarteret-personal`. After deploying the
exception integration, `logger.exception` and ERROR records with `exc_info`
produce exception issues. Unhandled request exceptions use this same path.
Ordinary 4xx responses and log records without an exception do not create issues.
Browser reports through `/api/v1/telemetry/client-errors` appear in Logs. A
`RepeatedFormSubmissionFailure` report with `attempt_count=3` also creates an
exception issue with safe field names and validation codes.

Exception issues contain exception types and stack frame file/function/line
metadata. Messages, source context, local variables, request payloads, and user
identity are excluded. A fixed server distinct ID avoids creating personnel
profiles; affected-user counts therefore do not represent individual people.
The SDK can deduplicate repeated capture of the same exception object.

## Configuration and delivery

Observability is always on when `APP_ENV=production`. Set the shared project's
`POSTHOG_PROJECT_TOKEN`, `POSTHOG_HOST=https://eu.i.posthog.com`, and
`OTEL_SERVICE_NAME=kvarteret-personal` in the desired Vercel environment, then
redeploy. Production already had these variables when inspected. Preview must
be configured separately. Never use a personal PostHog API key for ingestion.

`app/telemetry.py` exports named application events at INFO and warnings/errors.
Successful HTTP responses, query timings, page views, searches, and
mobile-card renewal and cache diagnostics are DEBUG and are excluded from
PostHog even when `LOG_LEVEL=DEBUG`. Invalid mobile sessions are WARN with safe
reasons such as `expired` or `bad_signature`. All 4xx/5xx responses retain a generic
`http.request.failed` fallback; unhandled exceptions retain sanitized diagnostics
and Error Tracking. Request traces carry routine HTTP activity.

Successful database-backed admin mutations and email enqueue events are exported
only after their transaction commits; rolled-back events are discarded. Lifecycle
logs are emitted by the workflow after commit, before optional side effects, so
an email or cleanup failure cannot hide a completed transition. An idempotent
public prospect replay does not emit another registration event. Mobile-card
access-code delivery/session creation and feedback issue creation have named
outcome events. Request/trace IDs, domain IDs, status, and categorical errors are
allowlisted; credentials, personal data, raw exception text, and arbitrary
messages are excluded by `app/observability.py`.

Traces still record every produced span (AlwaysOn), including spans
with an incoming unsampled parent. Incoming trace IDs are preserved. Its ASGI middleware
awaits provider flushes when each request finishes, including failures, rather
than relying only on background timers. Exports remain best-effort: abrupt
process termination or network failure can lose data. `app/error_tracking.py`
sends exceptions synchronously with a two-second HTTP timeout and no retries;
this can add latency to error paths. Normal requests do not send exception events.
Environment metadata uses `VERCEL_ENV` when available, otherwise `APP_ENV`.

The sibling implementation is in
`samfunnetibergen/apps/web/src/instrumentation.node.ts` (OTLP service
`samfunnetibergen`) and `apps/web/src/lib/observability.ts` (operational fields
and trace propagation). Its setup report links the same PostHog project.

## Vercel drains are a separate integration

The application sends telemetry directly to PostHog. The separate platform drain
accepts Vercel runtime, firewall, static, redirect, and external records. For
Personal, `tools/vercel-log-drain/transform.mjs` drops successful request records,
routine runtime output, and structured application logs already exported by OTLP.
It retains unstructured WARN/ERROR/FATAL records and HTTP 4xx/5xx platform failures,
including non-firewall failures before application execution. Website generic successful requests
and runtime records are also dropped. Structured website domain events remain as
a fallback while direct delivery is verified. Build logs are outside its scope. Vercel log drains send
JSON/NDJSON, while PostHog Logs accepts OTLP; do not point a Vercel log drain at
`/i/v1/logs` directly. PostHog's Vercel source webhook instead captures events,
which is distinct from the Logs product.

The `PostHog platform logs` drain (`drn_O0CCLHIbGya3HfXt`) sends 100% of
production and preview records for the two source projects to
`https://kvarteret-telemetry.vercel.app/api/logs`. The receiver is maintained in
`tools/vercel-log-drain/` and deployed as the separate `kvarteret-telemetry`
project. Never include that collector project in the drain sources: doing so
would create a feedback loop.

The collector requires `VERCEL_DRAIN_SECRET` and `POSTHOG_PROJECT_TOKEN` in
Vercel. It authenticates the Authorization header, maps Vercel JSON to OTLP,
redacts query values and credential paths, drops firewall records, and exports safe diagnostic fields.
Arbitrary console bodies and personal data are not exported. It acknowledges
only accepted PostHog batches; failure returns 502 for Vercel to retry. Delivery
is at least once: retries can duplicate records; `vercel.log.id` identifies the
original record. Personal application records sent directly by OTLP are filtered out of the
platform stream. Website runtime copies remain under its platform service.

Search services `kvarteret-personal-platform` or `samfunnetibergen-platform`,
then filter `vercel.request.id` using the request ID from Vercel. HTTP 4xx and
5xx responses receive WARN and ERROR severity even when Vercel labels them INFO.
Their event names are `http.request.failed`; unstructured runtime errors are
`platform.runtime.failed`. All `source=firewall` records are excluded from
PostHog, regardless of path, status or severity. Investigate challenges and
blocks in Vercel using the Vercel request ID. A 429 challenge is not proof of a
bot. Application auth/API errors and non-firewall platform failures remain.
No firewall summary or alert is configured by this change.
Sanity API/CDN connection errors carry `dependency=sanity` plus safe error
type/code, without exporting arbitrary messages.
A platform rejection before application execution has no application span;
the drain preserves that fact rather than inventing a trace ID.

The BFF uses the shared `NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN` for analytics and OTLP.
GitHub production release secrets explicitly supply it during the prebuilt release;
Sensitive Vercel exports can contain placeholders and must not become build tokens.
The release validates configuration and smoke-tests `/ingest/flags/?v=2` before promotion.
`apps/web/src/instrumentation.node.ts` registers a `NodeTracerProvider` and batched
OTLP logs. `apps/web/src/lib/telemetry-flush.ts` uses Next's built-in `after()`
to await logger/tracer provider flushes when domain events/spans complete. Ordinary
successful HTTP requests do not produce operational log records.

References: [Python error tracking](https://posthog.com/docs/error-tracking/installation/python),
[Python logs](https://posthog.com/docs/logs/installation/python),
[Vercel source webhook](https://posthog.com/docs/cdp/source_webhooks/source-vercel-log-drain),
[Vercel drain formats](https://vercel.com/docs/drains/using-drains).


## Validation and repeated submissions

Empty optional group/role selections in the role-field GET fragment are treated
as absent values. Invalid nonempty identifiers still return 422. The fragment
only submits group, role, year, and term; it excludes CSRF and other form fields
from the GET query. The management authorization requirement remains enforced.
FastAPI request validation emits `http.validation.failed` at WARN with field
locations, error codes, and issue count, never rejected input. Successful request logs are DEBUG; 4xx fallbacks are WARN and 5xx fallbacks are ERROR.
Domain validation warnings remain available alongside a request failure fallback. The admin browser reporter also captures
HTMX response failures; dependent GET fragments do not count as submissions.

The four public forms (volunteer, event, room booking, karaoke) each keep a
failure tracker for their mounted form. Three unsuccessful submissions produce
one `RepeatedFormSubmissionFailure` issue with `form_id`, `attempt_count`, and
`failure_history` containing stage, field names, and validation codes. Successful
submission resets the sequence; changing fields does not. Remounting/reloading
starts a new sequence. Admin HTMX form HTTP failures use the same threshold and
include the union of safe validation fields/codes from failed responses.
Browser issues retain PostHog session context; no entered form values or
arbitrary validator messages are added by this tracking.

Next server hooks must live beside `src/app`, under `apps/web/src`. A compiled
instrumentation file at the app root is insufficient for Next's production hook
detection when using `src/app`. The server uses `NodeTracerProvider`, records all
produced spans, propagates context to Personal, and retains export completion
with Next `after()`. Browser logs remain separate and need not carry a trace ID.
The Logs severity facet named Trace is unrelated to the Tracing product.

Platform counts include static assets, middleware, redirects, and the analytics
proxy. One page visit therefore produces many more platform records than server
request logs. Use application services for business diagnostics and platform
services for Vercel failures and request IDs. Personal now filters these routine records at the collector rather than
sampling domain events. This policy takes effect after deploying both Personal
and the separate collector; changing source alone does not change production
volume. The shared drain subscription is unchanged; both source platform streams filter
generic successful records. Website analytics events (pageviews and domain actions)
are separate from platform Logs and remain enabled.


## Mobile logout diagnostics and correlation

Personal uses the standard Python `logging.LoggerAdapter`, JSON formatter and
an INFO export allowlist (`DOMAIN_OUTCOME_EVENTS` in `app/observability.py`).
Call sites use standard `logger.info`, `logger.warning`, `logger.error` and
`logger.exception` methods with allowlisted `extra` fields. Database outcomes use
`after_commit=True` on the same adapter. Standard `stacklevel` preserves the
emitting file/function/line through context-building helpers; there is no
`emit_event` function. Domain outcomes and all WARN/ERROR records are exported. Reads, visits, query
timings, session renewal, and cache fallback/recovery are DEBUG. Records have
`schema_version=1` and `domain`; admin records use the action's domain. Database
outcomes publish after commit, with captured request and trace context.

The app calls `POST /api/v1/mobile-card/client-events/diagnostics`. Signed-out
clients can report here; payloads are bounded and the existing Postgres limiter
allows 60 requests per minute per source IP. The original
`/client-events/session-logout` endpoint remains available to older installs.
The new route accepts only named diagnostics and declared fields:

| App event | Exported event | Level |
| --- | --- | --- |
| `logout_succeeded` | `mobile_card.logout.succeeded` | INFO |
| `logout_failed` | `mobile_card.logout.failed` | WARN |
| `session_invalidated` | `mobile_card.session.invalidated` | WARN |
| `credentials_missing_after_login` | `mobile_card.credentials.missing_after_login` | WARN |
| `response_invalid` | `mobile_card.response.invalid` | WARN |
| `session_token_persist_failed` | `mobile_card.session.persistence.failed` | WARN |
| `cache_fallback_started` | `mobile_card.cache.fallback.started` | DEBUG (excluded) |
| `cache_fallback_recovered` | `mobile_card.cache.fallback.recovered` | DEBUG (excluded) |

The legacy logout endpoint also maps its accepted failure names to these domain
events; existing app payloads need no update. Historical generic event names remain.

Filter `event_name=session_invalidated` to investigate a forced logout. It has
`failure_stage=reauthorization`, auth code/status and credential/cache/marker
presence flags. Raw error messages, account names, email addresses and session
tokens are excluded. Client event/operation/attempt IDs, occurrence time,
platform, app/runtime version and update ID connect retries and releases.
Delivery is best-effort with a bounded 100-record, 24-hour client queue; retries
can produce duplicate log records, identifiable by `event_id` and `attempt_id`.

The app attaches a random `X-Session-ID` to auth requests and diagnostics for the
current app process; queued diagnostics preserve their original session ID.
For mobile-card API routes, a valid app diagnostic header takes precedence over
an incidental browser CSRF cookie. Personal writes `session_id` on logs and
`session.id` on OTel server spans. Each
request has its own trace; `session_id` connects requests, including a rejected
`/me` request followed by forced logout. The diagnostic ID grants no access.
Browser correlation uses a purpose-specific keyed digest of the existing valid
CSRF cookie. Cookie changes start a new diagnostic session. Requests without a
cookie or valid diagnostic header receive a fresh ID.

`X-Request-ID` in the response identifies a request. Follow its OTel trace for
dependencies or filter `session_id` for surrounding domain outcomes. ASGI
send/receive spans are disabled; server, domain and HTTPX spans remain
unsampled. Named domain spans suppress raw exception events. Error logs retain
exception types, final frame file/function/line, dependency status and SQLSTATE;
Error Tracking retains sanitized stack metadata. Exporter failure or abrupt
termination can still lose telemetry.

Policy reference: https://posthog.com/docs/logs/best-practices


## Controlled verification traffic

Personal requests with `X-Telemetry-Synthetic: true` set `synthetic=true` on
application logs and OTel server spans. Ordinary requests have `synthetic=false`.
Exclude true records when reviewing real user failures. This is a diagnostic
label declared by the caller; it grants no privileges and does not suppress
logging or bypass rate limiting. Previous proof records can be identified by
`logging-proof` / `prod-proof` request IDs; they cannot be retroactively marked
by changing the application. Use the header on future controlled probes.


## Connect mobile logout to a volunteer

`mobile_card.session.created` and `mobile_card.access_code.sent` include a
server-resolved `volunteer_id` for volunteer subjects. Trial applications keep
`subject_type=trial_application` and their application `subject_id` instead.
Session creation also carries the app's diagnostic `session_id`. Find a logout
record, copy its `session_id`, then filter Personal logs by that ID and inspect
the preceding `mobile_card.session.created` record's `volunteer_id`.

This associates records without accepting a volunteer ID from signed-out
clients or storing a new identity map. The session ID is caller supplied,
so correlation is diagnostic evidence rather than proof of authorization.
An app restart changes that ID; if no login occurred in the new process, the
new logout's session ID may have no identity association. Historical login logs
already have `subject_type=volunteer` and `subject_id` for the same lookup.
Treat volunteer IDs as personal data and restrict access/retention accordingly.
