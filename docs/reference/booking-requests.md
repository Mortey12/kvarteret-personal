# Booking request storage

Personal stores versioned booking snapshots from the website at
`POST /api/v1/booking-requests`. The website waits for the committed receipt
before sending anything to Crescat. Storage failure returns a retry error to the
browser and prevents forwarding. Crescat failure leaves the snapshot intact.
Crescat still owns booking approval.

## Snapshot and receipt

Schema version 1 records the browser submission UUID, room/karaoke kind, event
and contact names, email, room IDs, each date's mandatory doors open/close,
validated form, and exact generated Crescat payload. Reservation get-in/get-out
remain in the form and payload, separate from public doors times. A closing
time earlier than opening means the following day, in Europe/Oslo.

The unique `(submission_id, content_hash)` constraint deduplicates identical
normalized snapshots, including concurrent retries. Editing a form while
reusing its submission UUID preserves a new immutable snapshot. The 201 receipt
contains `booking_request_id`, `submission_id`, and `content_hash` and is only
returned after commit. This guarantees capture before forwarding, not
exactly-once delivery to Crescat. No automatic Crescat replay worker or booking
read/prefill endpoint is introduced.

## Authentication and access

Sign with the shared server-only `PERSONAL_SIGNING_SECRET` (Personal also
accepts `PERSONAL_SIGNING_PREVIOUS_SECRET` during rotation and the deprecated
`VOLUNTEER_PROSPECT_HMAC_SECRET` until the website has switched). Headers are `X-Kvarteret-Timestamp` (Unix seconds),
`X-Kvarteret-Nonce` (fresh lowercase UUID), `X-Kvarteret-Idempotency-Key`
(submission UUID), and `X-Kvarteret-Signature` (`v1=` plus HMAC-SHA256 hex).
The signed message has no trailing newline:

```text
booking-v1
<timestamp>
<nonce>
<submission UUID>
POST
/api/v1/booking-requests
<SHA256 hex of exact body bytes>
```

Authentication expires after five minutes. Verified nonces are consumed once
for ten minutes, the route allows 120 requests/minute, and body size is capped
at 256 KiB. Missing configuration and limiter/database failures fail closed.
Each HTTP retry needs a new nonce but retains the same body and submission ID.

The `public.booking_requests` table has RLS enabled and grants revoked from
PUBLIC, anon, and authenticated. Only the existing privileged Personal backend
connection can access it. There is no public read API. Do not copy snapshots
into logs or PostHog. Records currently persist until an authorized operator
removes them through the backend database; this change adds no automatic expiry.
Use the submission UUID to identify all snapshots for a deletion request.

## Deployment

Apply migration `20261006_1500` and deploy Personal first. Verify the deployed
OpenAPI includes the endpoint and both projects have the existing signing key.
Then release the website. Releasing the website first blocks booking submissions.
Rolling the website back restores direct forwarding; it does not delete stored
snapshots. Do not drop the archive as a routine rollback.

## Source evidence

- `app/api/v1/booking_requests.py`: validation and commit-before-receipt.
- `app/api/booking_request_auth.py`: body-bound signature and limits.
- `app/domain/booking_requests/`: models, canonical hashing, atomic storage.
- `migrations/versions/20261006_1500_booking_requests.py`: table and access.
- Website `apps/web/src/lib/integrations/kvarteret-personal/booking-requests.ts`:
  signing, bounded retries, receipt validation.
- Website room and karaoke `actions/submit-*-booking.ts`: capture before Crescat.
