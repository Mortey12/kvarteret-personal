# Skiftlista: handoff

Read this first. It says what exists, how to run it, what was decided, and
what to build next, so a new session (human or AI) does not need to explore
the codebase again.

## Goal and hard constraints

- Shift list for Skjenke (subgroups Stjerne, Kjøkken, Grøndahls, Halvtimen)
  and samfunnettjeneste (every volunteer takes 1–2 shifts a semester).
- Leaders plan in **Personal** (this repo). Volunteers use the
  **kvarteret-internbevis** app (Expo).
- Pilot with **Stjerne** first, then other groups.
- **Only one App Store / Play Store release.** Everything after that must be
  data-driven from Personal (or Expo OTA). The app must therefore ship with
  all Skiftlista screens, switched on per group from the server.
- Never use real names in test data, prototypes or docs.

## References

- Clickable prototype: https://mstene.no/skiftliste (source:
  `Mortey12/Mortey12.github.io`, folder `skiftliste/`). `logic.js` holds the
  reference rules, including the allocation suggestion (`suggest()`) and the
  per-month warnings (`fordelingWarnings()`). Port from there.
- Plan doc and setup guide: Morten's private Claude docs (ask her for access).

## What is built (branch `skiftlista/skiftplan`)

| Area | Files |
| --- | --- |
| Tables + migration | `app/domain/shifts/tables.py`, `migrations/versions/20261027_1200_shift_planning.py` |
| Pure rules | `app/domain/shifts/planning.py` (room incl. opplæring, eligibility, labels, week/day formatting) |
| Data access | `app/domain/shifts/repository.py` |
| Service | `app/domain/shifts/service.py` (`get_week_plan`, `assign`, `move`, `remove`) |
| Leader page | `app/web/routes/shifts/pages.py` (`GET /shifts`), `app/templates/pages/shifts/shift_plan.html` |
| Drag and drop | `app/web/routes/shifts/actions.py` (POST `/shifts/assignments`, `/{id}/move`, `/{id}/delete`), `app/static/js/shift-board.js` |
| Setup (4b) | `app/domain/shifts/setup_service.py`, `generator.py`, `template_input.py`, `permissions.py`, `app/web/routes/shifts/setup_actions.py`, pages `shift_settings.html`, `shift_templates.html`, `shift_generate.html`, migration `20261030_1200_shift_closed_periods.py` |
| Test data | `scripts/dev/seed_shifts.py` (run by `scripts/dev/bootstrap.py`; skipped when a real snapshot is used) |
| Tests | `tests/unit/domain/test_shift_planning.py`, `tests/unit/domain/test_shift_service.py`, `tests/web/test_shifts_web.py`, `tests/unit/shared/test_semester.py` |
| App API (step 1) | `app/api/v1/mobile_shifts.py` (mounted at `/api/v1/mobile-card/shifts`), `app/domain/shifts/app_service.py`, `app_repository.py`, `app_rules.py`, `app_models.py`, migration `20261028_1200_shift_app_api.py` |
| App API tests | `tests/unit/domain/test_shift_app_service.py` (real SQLite DB), `tests/unit/domain/test_shift_app_rules.py`, `tests/api/mobile_card/test_mobile_shifts_api.py` |
| App client | `kvarteret-internbevis` branch `feat/skiftlista-mobile`: the app screens (step 2) live there; `npm run api:generate:personal` writes `src/core/api/kvarteret-personal` (only the `mobile-shifts` tag) |

Pilot-blocker fixes from code review (done): deleting a volunteer also
removes their shift assignments, wishes, availability rules and course
sign-ups; the generator's "already exists" key is (template, Oslo date);
leaders can remove an empty shift and close or reopen any shift on the board
(scope-checked, CSRF, logged, confirm before delete); leader assign and move
lock the shift row like the app claim; the swap and "kan ikke" texts say the
leaders get no notification yet; course checks are a per-group switch
(`courses_enabled`).

Also fixed: dev seed produced semester code `20253` (now
`get_previous_semester_code` in `app/shared/semester.py`).

### Data model

- `shift_templates` (group, venue, title, weekday 0=Mon, start/end time,
  end before start = past midnight, `training_replaces_slot`, active) +
  `shift_template_slots` (kind, count, `open_for_samf`, sort order).
- `shifts` (dated instances; group, optional template, venue, title,
  `starts_at`/`ends_at` UTC, note, `is_closed`, `training_replaces_slot`) +
  `shift_slots` + `shift_assignments` (slot, volunteer, `swap_requested`).
- Slot kinds: `skiftleder`, `skjenker`, `bartender`, `kokk`, `opplaering`,
  `garderobe` (`SLOT_KINDS` in `planning.py`). Crew kinds = skjenker,
  bartender, kokk.
- App (step 1): `shift_group_settings` (per group: `app_enabled`,
  `samf_enabled`, `meeting_point`, `courses_enabled`), `shift_week_status` (per group and
  week, `week_start` is the Monday: `open` / `allocating` / `published`; no
  row: the current week and earlier are published, later weeks are open),
  `shift_availability_rules` (weekday answers per semester),
  `shift_wishes` (per-shift answers), `course_sessions` +
  `course_session_signups` (owned by the courses module), and on
  `shift_assignments`: `cannot_attend`, `status_message`, `status_changed_at`.
- Who may take what comes from **existing** Personal data: membership and
  verv (`role_assignments` + `assignment_roles`) this semester. Verv names
  used: `Skiftleder`, `Opplæring`, `Kokk`. Subgroups use
  `groups.parent_group_id`. Course requirements per group already exist in
  `group_course_requirements`.

### Rules implemented (server is the source of truth)

- Skiftleder place: verv Skiftleder in the shift's group. Kokk: verv Kokk.
  Opplæring place: verv Opplæring in the group. Trainees may not take crew
  places in their own group. Crew places: any Skjenke member, or anyone if
  the place is open for samfunnettjeneste.
- Opplæring **takes a regular crew place** by default
  (`training_replaces_slot`); per shift it can be "i tillegg".
- Closed shift, same person twice on a shift, full place: refused.
- More than one shift in a week: allowed with a warning.
- Halvtimen: under 20 is refused, also on the leader board (unknown birth
  date: a warning for leaders, refused in the app).
- "Kan ikke i dag": the person stays on, marked on the board; the place
  counts as open and, unless it needs a verv, is open to everyone. Whoever
  takes it (app or leader) takes the absent person off.
- App only: brannkurs for everyone, barkurs for samfunnettjeneste in
  skjenker/bartender places, valid 4 semesters from `completed_semester`, or
  a sign-up for a session before the shift. Not checked on the leader board.
  Only for groups with `shift_group_settings.courses_enabled` on. It is off
  for new groups and for every existing row (migration `20261031_1200` does
  not look at course requirements). A leader turns it on in `/shifts/settings`
  once real course completions are registered; the dev seed turns it on for
  Stjerne. When it is off the app skips course checks for that group's
  places.

## Run it locally

Without access to the private `infra` repo (no `kv` command):

```bash
docker compose -f docker-compose.dev.yml up -d db
cat > .env <<'EOF'
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/kvarteret_personal_dev
DEV_ADMIN_EMAIL=dev@kvarteret.dev
DEV_ADMIN_PASSWORD=dev-password
EOF
mise use -g node@24.16.0   # tailwind build needs node
uv sync && bun install && bun run build:css
uv run python scripts/dev/bootstrap.py
uv run uvicorn app.main:create_app --factory --reload --host 0.0.0.0 --port 8000
```

Log in at http://127.0.0.1:8000 with `dev@kvarteret.dev` / `dev-password`,
then open `/shifts`.

Checks (the `.env` file breaks tests, so turn it off):

```bash
export KVARERET_DISABLE_DOTENV=1 APP_ENV=test DATABASE_URL=sqlite+aiosqlite:////tmp/kvp-tests.db
uv run ruff check . && uv run lint-imports
uv run pytest
uv run python scripts/export_openapi.py --check   # web routes are in openapi.json too
```

Postgres e2e: set `DATABASE_URL` and `E2E_DATABASE_URL` to a migrated
Postgres and run `uv run pytest tests/e2e`.

Make shifts without the seed: open `/shifts/settings` and add closed periods
(for example "Juleferie"), then `/shifts/templates` for the weekly pattern,
then `/shifts/generate`: pick the group and range (default today to the end
of the semester), check the preview and confirm. Running it again creates
nothing new; weeks keep their status, so nothing shows in the app until the
week is published on the board.
Apply the closed-periods table locally with `uv run alembic upgrade head`.

## Demo for a meeting

Fake data only. `scripts/dev/seed_demo.py` adds demo data on top of
`seed_synthetic` and `seed_shifts`: about 100 volunteers, Halvtimen members
under 20, barkurs and a few expired brannkurs, weekday answers and shift
wishes, two "kan ikke" and one more swap request in the coming two weeks,
Grøndahls and Halvtimen switched on in the app with this and next month
published, course checks on for Stjerne and Grøndahls, and two people with
verv outside the Skjenke groups (samfunnettjeneste). It also adds the demo
leader account `demo.leder@example.dev` (Admin, not a volunteer) for the demo
login below. It is safe to run twice.

Locally:

```bash
uv run python scripts/dev/bootstrap.py      # migrations, synthetic seed, shift seed
uv run python scripts/dev/seed_demo.py      # the demo data on top
```

On a hosted demo database (an empty database that holds no real people):

```bash
export DATABASE_URL=postgresql+asyncpg://...   # the demo database; direct endpoint, ssl=require
uv run alembic upgrade head
DEV_ADMIN_EMAIL=dev@example.dev uv run python -c "import asyncio, os, sys; sys.path.insert(0, 'scripts/dev'); \
from seed_synthetic import seed; from seed_shifts import seed_shifts; \
u = os.environ['DATABASE_URL']; asyncio.run(seed(u)); asyncio.run(seed_shifts(u))"
DEMO_SEED_CONFIRM=<the host name in DATABASE_URL> uv run python scripts/dev/seed_demo.py
```

`seed_demo.py` refuses to run unless the host is `localhost` / `127.0.0.1` or
`DEMO_SEED_CONFIRM` is exactly the host name, and unless every volunteer has an
email ending in `@example.dev` (a volunteer with no email also stops it). The
other two seeds have no such checks, so never point them at a real database.
`DEV_ADMIN_EMAIL=dev@example.dev` keeps the synthetic seed's admin account on the
fake domain; with the default `dev@kvarteret.dev` the demo login refuses to start.

### Demo login (hosted demo only)

The hosted demo runs with `APP_ENV=production`, where there is no web login
(no Supabase) and app codes cannot be emailed. A demo login replaces both
(`app/auth/demo_login.py`, `app/auth/demo_guard.py`). **Never enable it on
Kvarteret's real deployment.** It is off unless both are set:

| Variable | Value |
| --- | --- |
| `DEMO_LOGIN_ENABLED` | `true` |
| `DEMO_ACCESS_CODE` | at least 12 characters; the app refuses to start with a shorter or missing code |

When it is on:

- **Startup check.** At startup (`app_lifespan`) the app counts volunteers
  (`volunteer_records`) and user accounts (`user_accounts`) whose email is
  missing or does not end in `@example.dev`, and refuses to start if there is
  even one. The same check runs again before the first demo login in each
  process, so a runtime that skips the startup hook still never accepts the
  code against a database with real people.
- **App login.** For an email ending in `@example.dev`, "send code" sends
  nothing and the code is `DEMO_ACCESS_CODE`. The session is the volunteer's
  own (real `person_id`, refresh token), so Skiftlista loads. A wrong code
  falls through to the normal check and is refused; the normal attempt limits
  apply. Any other email works exactly as before.
- **Leader login.** The login page shows "Demo-leder": `DEMO_ACCESS_CODE` logs
  in `demo.leder@example.dev` as Admin (`POST /login/demo`, CSRF and the normal
  login rate limits, constant-time code check), then opens `/shifts`. The
  account must exist and be Admin; `seed_demo.py` creates it. With demo login
  off, `/login/demo` answers 404.
- Every page shows a "Demo, falske data" banner.
- Each demo login is logged as `auth.demo_login.succeeded` with the account
  name before `@example.dev` (the log pipeline redacts whole emails) and the
  kind (`app` or `leader`), never the code. A failed startup check logs
  `auth.demo_login.refused`.

Log in to the app with an email and a code: locally the code is saved under
`.devdata/outbox/`; on the hosted demo use `DEMO_ACCESS_CODE`.
Good volunteers to log in as:

| Email | Why |
| --- | --- |
| `demo.stjerne@example.dev` | Stjerne member with valid brann- and barkurs: own-group tab, "Andre" (Grøndahls), claims a place |
| `demo.mangler-kurs@example.dev` | Stjerne, brannkurs ran out and no barkurs: the app offers a course sign-up with the claim |
| `demo.under20@example.dev` (also `-b`, `-c`) | Halvtimen, 19 or 18: Halvtimen places are blocked ("20+"); the leader board refuses them too |
| `demo.kan-ikke@example.dev` | Grøndahls, has a "kan ikke" in the coming two weeks (Mine, and marked red on the board) |
| `demo.bytte@example.dev` | Stjerne, has a swap request in the coming two weeks |
| `demo.skiftleder@example.dev` | Stjerne skiftleder: skiftleder places |
| `demo.samf@example.dev`, `demo.samf-b@example.dev` | Verv in Lydgruppen / Eksponeringsetaten only: samfunnettjeneste, sees places open for samf in Stjerne and Grøndahls |

Leader side: locally, log in to Personal as the dev admin and open `/shifts`; on
the hosted demo use "Demo-leder" on the login page. Halvtimen
is switched on for the demo (it is not in the request list, but the "20+" block
only shows when Halvtimen is visible in the app).

### Hosting the demo on Render

`render.yaml` (Blueprint) describes one free web service, `skiftlista-demo`, in
Frankfurt, native Python runtime, branch `skiftlista/demo`. It never runs
migrations or seeds: migrate and seed the demo database by hand first (above).

The build installs uv and bun (Render's Python runtime has no bun), runs
`uv sync --frozen` (uvicorn is a dev dependency, so dev is included), `bun
install` and `bun run build:css`. Only the CSS is built: the i18n catalogs,
compiled emails and `vercel-observability.js` are committed. The service starts
with `.venv/bin/uvicorn app.main:create_app --factory --host 0.0.0.0 --port
$PORT` (uv itself is gone after the build) and is checked on `/health`.

In Render's dashboard:

1. New → Blueprint, connect the `kvarteret-personal` repo, pick branch
   `skiftlista/demo`. Render reads `render.yaml`.
2. Fill in the variables marked `sync: false` (table below) and create the
   service. `APP_PUBLIC_BASE_URL` is the service URL, which is
   `https://skiftlista-demo.onrender.com` unless the name is taken; check it on
   the service page and fix the variable if it differs (this redeploys).
3. Wait for the deploy to go live, open the URL and use "Demo-leder" on the
   login page. If the startup check finds a real person, the deploy fails;
   read the log for `auth.demo_login.refused`.
4. Point the app's Personal URL at the Render URL for the demo build.

Free plan: the service sleeps after 15 minutes without traffic and the first
request then takes up to about a minute, so open it shortly before the meeting.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | secret: the demo database, `postgresql+asyncpg://…?ssl=require` |
| `APP_SECRET_KEY` | secret: at least 32 characters |
| `PERSONAL_SIGNING_SECRET` | secret: at least 32 characters (unused on the demo, but required in production) |
| `DEMO_ACCESS_CODE` | secret: at least 12 characters |
| `APP_PUBLIC_BASE_URL` | typed in: the Render URL, `https://…onrender.com`, no trailing slash |
| `APP_ENV` | `production` |
| `DEMO_LOGIN_ENABLED` | `true` |
| `EMAIL_DISPATCH_ENABLED` | `false` (so no `CRON_SECRET` is needed) |
| `LEGACY_ADMIN_LOGIN_ENABLED` | `false` |
| `POSTHOG_ENABLED` | `false` (nothing is sent to PostHog) |
| `PYTHON_VERSION` | `3.13.14` |
| `UV_PYTHON_DOWNLOADS` | `never` (use Render's Python, which is kept after the build) |
| `FORWARDED_ALLOW_IPS` | `*` (uvicorn trusts Render's `X-Forwarded-Proto`, so URLs and cookies are https) |

Without Azure, Supabase, Resend and the Vercel crons the app still starts.
What does not work: photos (none are shown, uploads fail), every email
(Resend has no key; app codes are only for `@example.dev` via the demo code),
password login and password reset (no Supabase), feedback to Linear, the agent
chat, Spotify "now playing". The session cookie is host-only on
`onrender.com` (the `.samfunnetibergen.no` domain is only used on that domain).

`POSTHOG_ENABLED=false` keeps the demo out of Kvarteret's PostHog project
entirely: no OTLP logs or traces (and no per-request flush), no exception
issues, no feature-flag lookups (Spotify "now playing" and the pooler trial
stay off) and no agent-chat events. It defaults to `true`, so Kvarteret's real
deployment is unchanged; there, an unreachable `POSTHOG_HOST` makes every
request hang, because each request flushes telemetry before it answers.

## Decisions already made (do not re-ask)

- Quota: 1 shift a week; pensioner agreement 1 a month (model as verv
  `Pensjonist` unless etjenesten prefers a contract-type field). No hard
  max for people who want more; warn leaders instead (limit as a group
  setting, suggestion "more than 3 a week").
- Halvtimen requires age 20+ (show only "20+" in the app, never birth date).
- Kjøkken is part of Skjenke; kitchen places are for chefs only; kitchen
  shifts are shown in the Stjernesalen card ("Stjernesalen og kjøkken").
- Becoming skiftleder: three skiftleder training shifts, not a course.
- "(AS)/(AV)" in the old sheets = skiftledere who do not want to train
  people. Implement as a per-person setting "tar ikke opplæring"; never put
  a trainee on their shifts.
- Brann- and barkurs valid 2 years; completions are stored per semester, so
  compute expiry as 4 semesters later. Barkurs is only required for
  samfunnettjeneste in bar places; brannkurs for everyone. A person must be
  signed up for a course that takes place before the shift.
- Wishes: weekday rules (vil gjerne / kan hvis nødvendig / kan aldri / ikke
  satt) plus per-shift override. Monthly allocation is made and published
  between the 15th and 20th of the previous month. Volunteers can set
  wishes for the whole semester.
- Allocation status is per group and **week**, not per month (the 15th–20th
  monthly routine stays as the leaders' process, the code does not depend on
  it; a leader publishes a month by publishing its weeks). A week is
  identified by its Monday in Oslo time, and a shift belongs to the week of
  its Oslo start date, also when it ends after midnight. Weeks without a
  `shift_week_status` row get their status from the date: the current week
  and earlier are `published`, every later week is `open` (takes wishes; its
  shifts cannot be claimed, so no first come, first served before
  allocation). Explicit rows always win. Leaders only set `open` or
  `published`: "Fordeles nå" is gone from the board, and an old
  `allocating` row is shown to leaders as "Åpen for ønsker" (no data
  migration; the app API still accepts and reports `allocating`, which takes
  wishes like `open`). "Åpne for ønsker" is refused, in the UI and on the
  server, for the current week and earlier (`can_open_week`).
  Wishes are unchanged: per shift plus weekday rules. `GET /wishes` lists
  weeks that have shifts, from today through the end of the semester (spring
  Jan–Jun, autumn Jul–Dec) and at least through next month, so January takes
  wishes in December (`effective_week_status`, `week_start`,
  `wish_window_end` in `app_rules.py`; migration `20261029_1200`).
- Privacy: wishes visible only to the person and the group's leaders;
  "nei" never has a reason; swap/sick messages optional and visible only to
  Skjenkeleder and the leder/personalansvarlig of that bar or kitchen (not
  skiftleder), deleted after the shift; names on past shifts kept this and
  the previous semester, then only counts. Get Kvarteret's privacy person
  to confirm.
- Sick on the day: "Kan ikke i dag" keeps the person on the shift, marked,
  notifies the leaders, and opens the place to everyone incl.
  samfunnettjeneste; they come off when someone takes it.
- Leader permissions: Admin all groups; Gruppeadmin own groups plus
  subgroups; nobody else (step 3).
- Rights are explicit assignments made by an Admin, never derived from
  free-text verv. A bar's leader or personalansvarlig is Gruppeadmin of that
  bar's group, and the Skjenke leader is Gruppeadmin of the Skjenke group.
  All logins are individual people, with no shared role accounts.
- Privacy of who is on a shift (decided, **not built yet**, planned for step
  4c): what other volunteers
  see is a per-group setting (`none`, `roles`, `first_names`, `full_names`),
  default `first_names`. A member can hide their own name from other
  volunteers (shown as role only); the group setting is the ceiling. Leaders
  and the skiftleder on the day always see everyone.
- "May not work together" pairs: no reason or case reference is stored. A
  separate explicit permission "manage pairs", granted by an Admin per group
  scope (a bar, Skjenke, or everything), and not implied by Admin. A pair is
  visible and editable only by someone whose scope covers both people. It is
  enforced everywhere a person can be placed (app claim, leader assign and
  move, taking a swapped place, replacing someone who can't come), with a
  neutral refusal that never names the other person. In the app the place is
  simply not shown. Every view and change is logged. **Not built yet:**
  Kvarteret's privacy person reviews the data model first.
- Skiftlista will expand to other groups (for example Vakt), so nothing may
  depend on the Skjenke group's slug. A group takes part when it has a
  `shift_group_settings` row or shift templates, or has a descendant that
  does (the ancestor rule is for permissions, scope and the pool only:
  `/shifts/templates`, `/shifts/generate` and the settings cards list just
  groups with their own row or templates, in tree order); only an Admin adds
  a group (`/shifts/settings`, "Andre grupper"). Verv decides which places a
  person is eligible for; leader rights are explicit Admin assignments and
  never derived from verv.
- Build order: 4b, then 4c (privacy settings), then 4d (edit one shift or a
  whole series on the board), then 5 onward.
- Samfunnettjeneste is not a role; it is done by people in many groups.
  Open: Morten will describe how it works.
- Ownership is role based (Skjenkeleder, HS leder/driftsansvarlig); role
  admins can grant access; a maintenance role for etjenesten.
- Notifications: email for the pilot. Push to be built together with
  etjenesten's planned shared notifications.

## Step 1 (API for the app): done

Contract and rules are described in `docs/reference/api-boundaries.md`
(section "Skiftlista API"). Smoke-tested on Postgres against the dev seed,
where Stjerne is switched on (also for samfunnettjeneste) with course dates.

Known gaps, deliberately left to later steps:

- **Publishing a week**: on the board (`/shifts`), each group has a status
  panel for the displayed week: "Åpne for ønsker" (disabled for the
  current and past weeks), "Publiser
  uka" and "Publiser måneden" (explicit published rows for every Monday of
  that calendar month). Shifts leaders put people on in an unpublished
  (future) week are drafts and do not show in the app's Mine until the week
  is published.
- **Unpublished-week warning**: done. A banner on the board appears when a
  week with shifts starts within 10 days and is not published ("Uke 49 er
  ikke publisert. Den blir åpen for alle mandag 30. november."), in warning
  colour from 3 days before, with a "Publiser" button. For a week with an
  explicit non-published row the text says it is not visible in the app until
  published, since explicit rows never auto-publish. Email reminders come
  with step 8.
- **Pilot step when switching a group on:** open `/shifts`, pick the group
  and use "Publiser måneden" on the weeks already planned in the old sheet,
  or their shifts stay hidden from Ledige and Mine and open for wishes.
- **Course checks** are off for every group until a leader turns on "Sjekk brann- og barkurs" in
  `/shifts/settings`; do that once real completions are registered.
- **Switching on** a group: `/shifts/settings` (linked from the board) has
  the app and samfunnettjeneste switches, the meeting point and closed
  periods per participating group; an Admin adds a new group there under
  "Andre grupper". Templates are at `/shifts/templates`, and the first
  shifts come from `/shifts/generate`. Course dates have no screen in
  Personal yet (step 6).
- **Generator limits:** it only creates; changing a template does not change
  shifts that exist. "Already exists" means the same template on the same
  Oslo date (not the exact start time), so editing a template's time and
  generating again creates nothing for days that already have its shift. There
  is no unique constraint on that key yet, so two runs at the same moment
  could both create a shift (step 4d). Closed periods only affect the generator, not shifts that exist.
  They inherit down the group tree: the generator skips a day when the group
  or any ancestor has a period on it, and the preview names the period and the
  group it came from. On `/shifts/settings` an ancestor such as Skjenkegruppen
  appears only in the closed-periods section ("Hele Skjenkegruppen"), where a
  leader whose scope covers it can add and remove periods; each child lists
  inherited periods read-only ("Fra …"). A leader scoped to one bar cannot
  edit the parent's periods. No schema change: `shift_closed_periods.group_id`
  was enough.
- **Course names**: the app looks for a course named `Brannkurs` or
  `Brannvernkurs`, and `Barkurs` (`COURSE_RULES` in `app_rules.py`). A course
  that does not exist is not checked. Ask etjenesten whether `Skjenkekurs` is
  the barkurs. Register real brannkurs completions before the pilot, or
  members cannot claim in the app.
- **Swap and "kan ikke"** are stored and shown on the board, but the message
  is not shown to leaders yet, no email is sent, and messages are not
  deleted after the shift (step 8).
- **Review account** (App/Play Store): review sessions get `enabled: false`,
  so reviewers do not see Skiftlista. Decide whether the review user needs
  demo access before submitting.
- "Tar ikke opplæring" is not considered yet (step 9).

## Next steps, in order

1. ~~API for the app~~ (done, see above).
2. ~~App screens in kvarteret-internbevis~~ (done). They live in
   `src/features/shifts/` on the `kvarteret-internbevis` branch
   `feat/skiftlista-mobile` (feature-first MVVM-lite; zod is not used): tabs
   Ledige / Mine / Ønsker, detail with requirements, course sign-up + claim
   in one step, behind a server-side per-group switch. They use the
   generated client; `GET /config` decides tabs and labels.
3. **Group-scoped permissions**: done, except the HS samfunnettjeneste role
   and the maintenance role. Admin manages every group. Gruppeadmin manages
   the groups in their group-admin assignment (`group_admin_memberships`)
   and all subgroups (`groups.parent_group_id`, any depth); others are
   refused. `app/domain/shifts/permissions.py` (pure `manageable_groups`,
   `GroupScope`), `get_shift_scope` in `app/dependencies.py`, and every
   leader page and action takes `scope=` (required, no default). The plan and
   its group filter and volunteer pool only include permitted groups;
   assign, move (both shifts) and delete answer 403 with a Norwegian message;
   a Gruppeadmin with no groups sees an empty state. Eligibility checks still
   look at the person's verv in all shift groups. The app API is unchanged.
   Not seeded locally: the dev auth gateway only accepts one login
   (`DEV_ADMIN_EMAIL`), see `app/auth/dev_auth.py`.
4a. ~~Pool, settings and week status~~ (done). The volunteer pool shows
   every member of the leader's groups and of participating sibling groups to
   leaders who may manage a participating group, and every participating
   member to Admin; a Gruppeadmin of any other group gets no
   shifts and no pool. The shifts shown and changed stay limited to
   permitted groups. `/shifts/settings` and the week status panel and banner
   on the board replace the SQL; `week_status.py` holds the pure rules.
   (4b replaced the Skjenke slug rule with the participating-group rule.)
4b. ~~Participating groups, templates, closed periods, semester generator~~
   (done). A group takes part when it has a `shift_group_settings` row or
   templates, or a descendant that does (`participating_groups` in
   `permissions.py`; the slug rule is gone). Leader scope is limited to
   participating groups. The pool is the leader's groups plus participating
   siblings (same parent); Admin sees all participating members. Only an
   Admin adds a group (`/shifts/settings`, "Andre grupper"). New table
   `shift_closed_periods` (migration `20261030_1200`), managed per group on
   `/shifts/settings`. `/shifts/templates` creates, edits and deactivates
   templates and their places. `/shifts/generate` previews and creates a
   semester of shifts (`generator.py` pure rules, `setup_service.py`).
   Code: `template_input.py` validates the template form.
   Follow-up (done, same step): removed "Fordeles nå" from the leader UI,
   current-week guard on "Åpne for ønsker", closed periods inherited down the
   tree, templates and generate list only groups with their own settings row
   or templates, in tree order. Step 4b status: done. Next is 4c.
4c. **Privacy settings** (see decisions): per-group visibility of who is on
   a shift, and a member's own hide setting.
4d. **Edit one shift on the board** (time, staffing, note, opplæring mode).
   "Fjern skift" (only when nobody is placed; with a confirm) and "Steng
   skift" / "Åpne skift" are done. The per-group app switches and meeting
   point are done (step 4a). Dropped: editing a whole series, and the daily
   top-up job that would generate the next 12 weeks. Still to do:
   - A unique index so two generator runs cannot both create the same shift.
     It uses the same key as the generator: (template, Oslo date of the
     start), which on Postgres is an expression index on
     `(template_id, (starts_at AT TIME ZONE 'Europe/Oslo')::date)`. Not built
     yet; the generator itself already skips on that key.
   - What happens to shifts that already exist when a closed period is added
     later: offer to remove the empty ones, warn about the ones with people,
     and never remove a shift that has people on it.
5. **Wishes and weekly allocation**: port `suggest()` and
   `fordelingWarnings()` from the prototype's `logic.js`; set
   `shift_week_status` (open → published for leaders; `allocating` is legacy) per week, with a
   "Publiser måneden" shortcut that publishes all weeks of a month, and the
   unpublished-week warning described under known gaps. Wishes are already
   stored by the app.
6. **Courses**: leader screens for `course_sessions` (date, place, capacity,
   cancel) and the sign-up lists. Sign-up and expiry are in the app API.
7. **Samfunnettjeneste**: events, places open for samf, progress per group.
8. **Swaps and sick days** with email to the right leaders, show
   `status_message` to them (not to skiftleder), delete it after the shift.
9. Small: keyboard alternative to drag and drop ("Legg til" on each place),
   semester-end data cleanup, per-person "tar ikke opplæring".

## Security review: open items

- **Finding D (fixed):** leader assign only accepts a volunteer in the leader's
  pool (`list_shift_members` over `_pool_group_ids(scope)`), checked before
  anything else. Outside the pool and nonexistent give the same answer,
  "Fann ikke personen i gruppa." (400), so no name, age rule or existence
  leaks. Move is unchanged. A side effect: a leader can no longer place a
  samfunnettjeneste volunteer who is in no participating group; they take
  open places themselves in the app.
- **Finding A:** the CSRF cookie is not tied to the session (app-wide). Fix by
  renaming it to `__Host-` or making it an HMAC with the session. Reported to
  etjenesten, not fixed in this branch.
- **Finding C:** the CSP allows `'unsafe-inline'` (app-wide). Reported to
  etjenesten, not fixed in this branch.
- **Finding B:** `?notice=` and `?error=` text in URLs. Low priority.
- Mobile login session handling was not reviewed.

## Not code, but on the critical path

- Review by etjenesten (show the prototype and this branch).
- Real data in Personal: is Stjerne etc. already a subgroup of
  Skjenkegruppen? Verv Skiftleder/Opplæring/Kokk set per person? Templates.
- Test with a few Stjerne people, then App Store / Play review.
- Ask etjenesten how Personal is hosted and billed. It affects polling from
  the app, the daily top-up job (4d) and the `/open` endpoint, which
  evaluates 92 days per call.
- Kvarteret's privacy person must review the data model before "may not work
  together" pairs are built.
- Stjerne has no weekend shifts, so check that templates and the generator
  match that when the first templates are entered.
