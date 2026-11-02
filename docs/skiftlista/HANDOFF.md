# Skiftlista: handoff

Read this first. It says what exists, how to run it, what was decided, and
what to build next, so a new session (human or AI) does not need to explore
the codebase again.

## Start here

Update by hand at the end of every session. Last updated 2026-11-02 (night, after L1a).

- **State** (2026-11-02 evening; replaces the earlier State text): Personal,
  branch `skiftlista/skiftplan`: S0 `b154264` (`app_service.py` split into
  mixins, pure move), S1 `d6df7c6` (wish calendar API, weekday rules retired in
  reads, "Har svart" on the board), S1d `af06d65` (calendar months start at the
  semester start). L1a `19871a7` (the leader week page, see "Built 2026-11-02, L1a"). Docs
  commits before these: `ba27207`, `80362cd`. Tests 1164 passed, 26 skipped; no migration; Alembic head `20261101_1200`. The app,
  `kvarteret-internbevis` branch `feat/skiftlista-mobile`: A1 `f4772cd`, A1b
  `179797b`, A1c `ba5ec80`; Jest 398 passed; checked on an Android phone in
  Expo Go, **not checked on iOS**. Details under "Built 2026-11-02" in the
  package section. **Demo:** `skiftlista/demo` was updated on 2026-11-02 (merge
  commit `644be17`, pushed to the fork, Render deployed, `/health` ok). On Neon
  production: Alembic at `20261101_1200`, `seed_demo.py` added 1110 wishes,
  week status has explicit rows only for 2026-11-02 and 2026-11-09. New Neon
  rollback branch `before-wishcalendar-2026-11-02` (never expires); the older
  `before-skiftplan-2026-10-29` expires 2026-11-05. The leader board on the
  hosted demo shows the large banner and "Har svart" per bar; the app was
  verified through Expo Go with the tunnel. Rule for later: a new migration
  must run on Neon before the matching merge into demo.
- **Done 2026-11-02 (read-only passes and a docs session):** the design pass
  for the wish calendar and leader page package,
  `docs/skiftlista/design-pass-wishes.md`, and Morten's decisions on it; the
  conformance pass against Kvarteret's own repos and guidelines,
  `docs/skiftlista/conformance-pass.md` (it ran on Sonnet; an Opus pass can
  spot-check its judgement calls if that turns out costly). Both decision sets
  are written into this file under "Design pass results (decided 2026-11-02)"
  and "Conformance pass results (2026-11-02)" in the package section. The
  first slice was built the same day (S0, S1, S1d, A1, A1b, A1c; see "Built
  2026-11-02"), and some decisions changed in the evening (see "Changes after
  the design pass (2026-11-02 evening)"). Do not edit the two pass files; they
  are the record of the reasoning, and they still describe the monthly plan.
- **Next action** (decided 2026-11-02 evening; replaces the earlier list). The
  reasons and details are under "Changes after the design pass (2026-11-02
  evening)" in the package section:
  (1) **L1b**, the rest of the new leader page layout (client side search and
  filters in the people panel, "Vis hvem" polish, the data block read by
  `shift-week.js`; L1a is built); (2) **L2**, selecting, popover,
  "Vis andre", swap; (3) demo update 2; (4) conformance pass 2 (wider: pull
  request history and review comments of the two repos, samfunnetibergen,
  org-level rules; public repos only); (5) Morten runs the Postgres e2e tests;
  (6) design pass 2 (read-only Opus: weekly planning and the dials turned into
  a data model, a migration split, the leader settings page and the app's
  request flow, then ask Morten); (7) **S2** (the migration or migrations from
  design pass 2, including the RLS and REVOKE fixes for `shift_week_status` and
  `shift_closed_periods`, the index rename, the weekly table, the weekly
  deadline and the dials; the weekly "Har svart" and "Ferdig"; copy from last
  week; read-only Opus review of every migration before Neon); (8) the leader
  settings page for the dials; (9) **A2** (mockup first: week-number column,
  "Ferdig", deadline banner, note and maks, copy last week, agreements and
  absence requests); (10) **G1**, then **L3** and **L4** (the suggestion uses
  the dials); (11) demo update 3; (12) **A3** and the calendar feed; (13) the
  lists "before the pilot", "before the store build" and "before any upstream
  PR" (under "Conformance pass results"). Then 4d-plus and 5d step 3. Step 8
  notifications (Slack) and 5c have no slot in this order yet (open, see the
  package section).
- **Parked, waiting for etjenesten:** OTA publishing (blocks the store
  build); real verv names (block running the copy script on real data);
  Findings A, C and E and hosting limits (block the official pilot, not the
  build); the Slack app and its questions (step 8; the direction is decided,
  see step 8); Crescat (event drafts). Finding E is known to etjenesten and
  being fixed (per Morten, 2026-11-02).
- **Parked, running the copy script on real data** needs, in this order:
  templates or settings rows for the bars and the kitchen; the real verv names
  from etjenesten; an answer from Samfunnet's privacy person; a read-only Opus
  review of the script; a dry run first.
- **Can proceed without etjenesten:** all of 5d, 4d-plus, 5a2, 5b, the wish
  calendar and leader page package and the server side of 4c, all against
  fake data. (Plan B, plain Python, is chosen for the suggestion function, so
  nothing waits for etjenesten's OR-Tools answer.)
- **Rules for Claude Code prompts:** start with "Read
  docs/skiftlista/HANDOFF.md fully"; say which files may change; no commit,
  push, seed or migration run by Claude Code; never `ruff format`; never
  `git add -A` or `git add .` (Morten adds files by name after
  `git status --short`); never push to Kvarteret's repos or open PRs
  (`origin` is the fork; in both repos, Personal and kvarteret-internbevis, the
  `upstream` remote is set to `no_push`; the app repo's was done 2026-11-02);
  fetch only from upstream; never run
  `eas update` or `eas build`; no real names in data or docs; no em dashes;
  end with a plain report and the exact list of changed files. Commit
  messages end with `Co-Authored-By: Claude <model> <noreply@anthropic.com>`
  and `Claude-Session: <ID from /status>`. Claude Code ends every report with
  `Claude-Session: <id>` (set in Morten's `~/.claude/CLAUDE.md`). Test environment:
  `export KVARERET_DISABLE_DOTENV=1 APP_ENV=test DATABASE_URL=sqlite+aiosqlite:////tmp/kvp-tests.db`,
  then `uv run ruff check . && uv run lint-imports && uv run pytest && uv run python scripts/export_openapi.py --check`.
  Opus reviews only where needed, to save credits: before something touches
  real data or the demo (migrations, Neon), for security-relevant changes, and
  when a design decision is unclear. Not after every build step when the
  checks pass. Long prompts go in two halves; the first ends with "reply only
  READY". The `claude` launch command goes in its own command block.
  Commands that change something go one per command block, because pasting
  several lines sometimes drops the first letter of a line.
  Standing rules from 2026-11-02: (a) new code follows the guidelines the
  conformance pass found (`docs/skiftlista/conformance-pass.md`); a rule we
  deliberately break is written down with the reason (in the code or docs next
  to it); a short repeat of the pass runs before anything goes toward
  etjenesten or the pilot; it is read-only and only fetches. (b) The GitHub
  fork is public, like Kvarteret's own repositories: write this file with that
  in mind (no secrets, no real names, no hosting credentials). A security
  finding that is not yet reported to etjenesten is never put in a committed
  file; it is kept in `~/kvarteret-private-notes.md`, outside the repo.

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
- Plan doc and setup guide: Morten's private Claude docs (ask Morten for access).
- Mockups for the wish calendar and leader page package (decided 2026-10-30):
  `docs/skiftlista/mockups/Main.dc.html` (the app, "Ønsker, desember 2026",
  Nynorsk; the app itself is Bokmål plus English) and
  `docs/skiftlista/mockups/Leader.dc.html` (the leader page on a laptop,
  Bokmål like today's board). Morten's copy in Claude Design:
  https://claude.ai/artifact/McyzpMmfTYqwf5vJvmA6wM (only Morten can open it).
  Reference only: see "Mockups" in the package section.

## What is built (branch `skiftlista/skiftplan`)

| Area | Files |
| --- | --- |
| Tables + migration | `app/domain/shifts/tables.py`, `migrations/versions/20261027_1200_shift_planning.py` |
| Pure rules | `app/domain/shifts/planning.py` (room incl. opplæring, eligibility, labels, week/day formatting) |
| Data access | `app/domain/shifts/repository.py` |
| Service | `app/domain/shifts/service.py` (`get_week_plan`, `assign`, `move`, `remove`) |
| Leader page | `app/web/routes/shifts/pages.py` (`GET /shifts`), `app/templates/pages/shifts/shift_plan.html` |
| Leader week page (L1a) | `GET /shifts/week` in `pages.py`, `app/domain/shifts/week_page.py` (loads), `week_view.py` (pure view model and JSON block), templates `shift_week.html` and `_week_*.html`, `app/static/js/shift-week.js`, styles at the end of `app/static/css/input.css`; tests `tests/unit/domain/test_shift_week_view.py`, `tests/web/test_shifts_week_web.py` |
| Drag and drop | `app/web/routes/shifts/actions.py` (POST `/shifts/assignments`, `/{id}/move`, `/{id}/delete`), `app/static/js/shift-board.js` |
| Setup (4b) | `app/domain/shifts/setup_service.py`, `generator.py`, `template_input.py`, `permissions.py`, `app/web/routes/shifts/setup_actions.py`, pages `shift_settings.html`, `shift_templates.html`, `shift_generate.html`, migration `20261030_1200_shift_closed_periods.py` |
| Test data | `scripts/dev/seed_shifts.py` (run by `scripts/dev/bootstrap.py`; skipped when a real snapshot is used) |
| Tests | `tests/unit/domain/test_shift_planning.py`, `tests/unit/domain/test_shift_service.py`, `tests/web/test_shifts_web.py`, `tests/unit/shared/test_semester.py`, `tests/unit/domain/test_shift_eligibility_db.py` (eligibility from qualifications, real SQLite DB) |
| Qualifications (5d) | `volunteer_qualifications` in `app/domain/shifts/tables.py`, migration `20261101_1200_shift_qualifications.py`, `list_qualifications` / `list_group_qualifications` / `get_qualification_settings` in `repository.py`; read for eligibility by `placement_problem` (`planning.py`), `service.py` and `app_service.py` |
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
  `shift_availability_rules` (weekday answers per semester; retired from reads
  2026-11-02, the table is dropped in a later migration after the store build,
  see 'Design pass results'),
  `shift_wishes` (per-shift answers), `course_sessions` +
  `course_session_signups` (owned by the courses module), and on
  `shift_assignments`: `cannot_attend`, `status_message`, `status_changed_at`.
- `volunteer_qualifications` (5d step 1; per volunteer, qualification, status
  `training` / `approved` and group) is now read for eligibility (5d step 2):
  qualifications plus the person's area decide who may take which places, not
  verv (see the "Qualifications" decision and step 5d). Membership and verv
  (`role_assignments` + `assignment_roles`, this semester) are still read for
  the app's audience, tabs, wishes, samf counting, `in_training` and the
  Pensjonist quota. Verv names used: `Skiftleder`, `Opplæring`, `Kokk`,
  `Pensjonist`; the copy script reads the Skiftleder and Opplæring verv once
  (kokk comes from kitchen membership; Pensjonist is read by the quota, not
  the script). Real
  qualifications still need that script to run before the pilot (migration A
  is schema only). Subgroups use
  `groups.parent_group_id`. Course requirements per group already exist in
  `group_course_requirements`, but that table is display-only (courses
  module) and Skiftlista does not use it: the course checks use `COURSE_RULES`
  in `app_rules.py` and the per-group `courses_enabled` switch.
  Course rights are global on purpose, since brann- and barkurs are for all of
  Kvarteret: any Admin or Gruppeadmin can manage courses, sessions and
  completions for everyone, not only for their own groups.

### Rules implemented (server is the source of truth)

- **Eligibility** (5d step 2, built 2026-10-29; see "Qualifications"): comes
  from `volunteer_qualifications` rows and the person's area (member groups,
  their participating siblings, and the participating direct children of a
  member group; decided 2026-10-29), not from verv. Restricted kinds are
  skiftleder, kokk and opplaering (opplaering until step 3). Refusal texts are
  "Kun for medlemmer av gruppa", "Kun for
  godkjente skiftledere / skjenkere / kokker" and "I opplæring: bruk
  opplæringsplassen". Verv is still read for the app's audience, tabs, wishes
  and samf counting (role rows in participating groups, expanded to their
  participating direct children), for `in_training` (`TRAINEE_ROLE`, until 5d step 6)
  and for the Pensjonist quota.
- Opplæring **takes a regular crew place** by default
  (`training_replaces_slot`); per shift it can be "i tillegg". (To be removed:
  see "Training simplification".)
- Closed shift, same person twice on a shift, full place: refused.
- More than one shift in a week: allowed with a warning.
- Halvtimen: under 20 is refused, also on the leader board (unknown birth
  date: a warning for leaders, refused in the app).
- "Kan ikke i dag": the person stays on, marked on the board; the place
  counts as open and, unless it is a skiftleder or kokk place (and an
  opplaering place until step 3), is open to everyone. Skjenker is now a
  qualification (since 5d step 2), but a sick person's skjenker place still opens to everyone,
  as decided under "Sick on the day" (decided 2026-10-29). Whoever
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

Logging in to the app locally (added 2026-11-02): the dev login code is saved
in `.devdata/outbox`. Print the latest one with:

```bash
grep -E '^ +[0-9]{6}$' "$(ls -t .devdata/outbox/*.html | head -1)" | tr -d ' '
```

Expo's LAN address printed from WSL starts with `172.` and is not reachable
from a phone; start Expo with `npx expo start --go --tunnel --clear`. The local
LAN `.env.local` in `kvarteret-internbevis` uses the PC's Wi-Fi address (backup
at `~/internbevis-env.local.lan-backup`); for the hosted demo see the runbook.

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
wishes (after S1 the seed writes per-shift answers instead of weekday rules,
see 'Design pass results'), two "kan ikke" and one more swap request in the coming two weeks,
Grøndahls and Halvtimen switched on in the app with this and next month
published, course checks on for Stjerne and Grøndahls, and two people with
verv outside the Skjenke groups (samfunnettjeneste). It is safe to run twice.

Locally:

```bash
uv run python scripts/dev/bootstrap.py      # migrations, synthetic seed, shift seed
uv run python scripts/dev/seed_demo.py      # the demo data on top
```

On a hosted demo database (an empty database that holds no real people):

```bash
export DATABASE_URL=postgresql+asyncpg://...   # the demo database; direct endpoint, ssl=require
uv run alembic upgrade head
uv run python -c "import asyncio, os, sys; sys.path.insert(0, 'scripts/dev'); \
from seed_synthetic import seed; from seed_shifts import seed_shifts; \
u = os.environ['DATABASE_URL']; asyncio.run(seed(u)); asyncio.run(seed_shifts(u))"
DEMO_SEED_CONFIRM=<the host name in DATABASE_URL> uv run python scripts/dev/seed_demo.py
```

`seed_demo.py` refuses to run unless the host is `localhost` / `127.0.0.1` or
`DEMO_SEED_CONFIRM` is exactly the host name, and unless every volunteer has an
email ending in `@example.dev` (a volunteer with no email also stops it). The
other two seeds have no such checks, so never point them at a real database.

Log in to the app with an email and a code. In development the code is saved
under `.devdata/outbox/`; on a hosted demo there is no way to read it without
email delivery (Resend) or the demo login, which lives on the
`skiftlista/demo` branch (its `DEMO_APP_CODE` is the code for every
`@example.dev` volunteer).
The volunteers to log in as, and the leader side, are listed under "Demo login
(hosted demo only)" below; the same logins work locally.

### Demo login (hosted demo only)

The hosted demo runs with `APP_ENV=production`, where there is no web login
(no Supabase) and app codes cannot be emailed. A demo login replaces both
(`app/auth/demo_login.py`, `app/auth/demo_guard.py`). **Never enable it on
Kvarteret's real deployment.** It is off unless all three are set:

| Variable | Value |
| --- | --- |
| `DEMO_LOGIN_ENABLED` | `true` |
| `DEMO_ACCESS_CODE` | at least 12 characters (leader web login); the app refuses to start with a shorter or missing code |
| `DEMO_APP_CODE` | exactly 6 digits (app login; the app's code box only takes 6 digits); the app refuses to start with a missing or non-6-digit code |

When it is on:

- **Startup check.** At startup (`app_lifespan`) the app counts volunteers
  (`volunteer_records`) and user accounts (`user_accounts`) whose email is
  missing or does not end in `@example.dev`, and refuses to start if there is
  even one. The same check runs again before the first demo login in each
  process, so a runtime that skips the startup hook still never accepts the
  code against a database with real people.
- **App login.** For an email ending in `@example.dev`, "send code" sends
  nothing and the code is `DEMO_APP_CODE` (`DEMO_ACCESS_CODE` is refused there). The session is the volunteer's
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
`.devdata/outbox/`; on the hosted demo use `DEMO_APP_CODE`.
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
| `DEMO_APP_CODE` | secret: exactly 6 digits |
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

## Demo update runbook

Done twice. First on 2026-10-29 (the demo went from the merge of `961a0e4` to
`4513caa`, a merge of skiftplan at `cbaba3a`). Second on 2026-11-02, an update
without a migration (see "Update without a migration" below). Keep it for the
next update.
Migration A is schema only, so the old demo code keeps working on the new
schema until the merge is deployed. A future migration must run on Neon before
the matching merge.

1. Check the demo database revision: `uv run alembic current` with
   `DATABASE_URL` set to the Neon direct endpoint (`ssl=require`). Expected now
   `20261101_1200` (after the 2026-10-29 update); `20261031_1200` was the
   revision before that update, verified 2026-10-29.
2. Create a Neon branch of the demo database as the rollback; note its name.
3. From the skiftplan checkout: `uv run alembic upgrade head` on the direct
   endpoint. Schema only; the old demo code keeps working on the new schema.
4. Optional Postgres test of the copy script: run it as a dry run only (no
   `--apply`) against the demo database. It writes nothing.
5. Merge `skiftlista/skiftplan` into `skiftlista/demo` locally and resolve the
   conflicts (see H1 under "Migration A and step 2 review"; the merge also
   conflicted in `docs/skiftlista/HANDOFF.md`, resolve it by taking the
   skiftplan side). Run the checks. Commit. Do not push yet.
6. From the merged checkout: `DEMO_SEED_CONFIRM=<exact host in DATABASE_URL>
   uv run python scripts/dev/seed_demo.py`. Required: it writes the
   qualification rows; without it the demo people have no bar places.
7. Check: `SELECT qualification, status, count(*) FROM
   volunteer_qualifications GROUP BY 1,2;` and that `shift_group_settings` has
   no row for Skjenkegruppen.
8. Push `skiftlista/demo` to the fork; Render deploys it (assumed
   auto-deploy; Render never migrates).
9. Check `/health`, the board as the demo leader, and the app logins in the
   "Demo for a meeting" table.
10. Rollback: use the Neon branch and revert the demo merge. Avoid
    `alembic downgrade` (it drops the qualification rows).

Update without a migration (done 2026-11-02): skip steps 1 to 4 (no revision
check, no migration, no copy script dry run). A fresh rollback branch is still
created in the Neon console (see the lessons below). Then:
- merge `skiftlista/skiftplan` into `skiftlista/demo` locally (merge commit
  `644be17`; only `docs/skiftlista/HANDOFF.md` conflicted), run the checks;
- run `seed_demo.py` against Neon (it added 1110 wishes; the demo seed also
  keeps only the current and next week explicitly published);
- check the week status in the Neon SQL Editor (explicit rows only for
  2026-11-02 and 2026-11-09);
- push to the fork, wait for Render, check `/health`, then the board as
  "Demo-leder", then the app.
Neon state afterwards: Alembic at `20261101_1200`; rollback branch
`before-wishcalendar-2026-11-02` (never expires); the older
`before-skiftplan-2026-10-29` expires 2026-11-05.

Lessons from 2026-11-02:
- Use the DIRECT address of the Neon production branch: turn off "Pooled
  connection" (the host has no `-pooler`) and show the password before copying.
  Write it in a private file (`chmod 600`) as
  `postgresql+asyncpg://...?ssl=require` (change `sslmode` to `ssl`, remove
  `channel_binding`). Check the shape with a masked `sed`. Delete the file and
  `unset DATABASE_URL` afterwards. Never paste the address or password into a
  chat.
- Run SQL checks in the Neon SQL Editor, never in the terminal.
- Create the rollback as a child branch of production in the Neon console.
- `git switch` fails while a merge is in progress.
- In the demo merge only `docs/skiftlista/HANDOFF.md` conflicted:
  `git checkout --theirs docs/skiftlista/HANDOFF.md`, `git add` it,
  `git commit --no-edit`.
- Run the code checks in a tab without a Neon address and the seed in a
  separate tab.
- The seed output counts 0 for things that already existed.
- The app on the demo: change the host in `.env.local` to
  `https://skiftlista-demo.onrender.com` (`sed` on the two `BASE_URL` lines);
  the app login uses `DEMO_APP_CODE`, the leader login uses `DEMO_ACCESS_CODE`
  ("Demo-leder").

What we learned on 2026-10-29:

- Run the code checks in a terminal that does not have the Neon `DATABASE_URL`
  set, and the seed in the one that has it.
- Keep the Neon address in a private file (`chmod 600`) and delete it
  afterwards.
- The app in Expo needs `.env.local` pointing at
  `https://skiftlista-demo.onrender.com` (both
  `EXPO_PUBLIC_KVARTERET_PERSONAL_API_BASE_URL` and
  `EXPO_PUBLIC_INTERNKORT_BASE_URL`), then restart with
  `npx expo start --go --tunnel --clear`. Never `eas update` or `eas build`.
- On 2026-10-29 the local `.env.local` in `kvarteret-internbevis` was switched
  to the Render demo; the local (LAN) version is backed up at
  `~/internbevis-env.local.lan-backup`. Before local development, restore it
  with `cp ~/internbevis-env.local.lan-backup .env.local` (in
  `kvarteret-internbevis`) and restart Expo.

## Decisions already made (do not re-ask)

- Quota and requirements (from the demo meeting with the bar leaders, incl.
  Grøndahls) replace the old "1 shift a week" idea as the general model.
  Requirements are **per bar and warnings only, never blocking**. Max per
  week and the pensioner rule "1 a month" (verv `Pensjonist`) become rule
  types in the same per-bar settings (the fixed pensioner rule is replaced
  2026-11-02 evening by per-person agreements, see "Dials and absences"). Pensjonist stays a verv, not a
  qualification: it is a per-semester agreement. No hard max for people who want more; warn leaders
  instead (suggestion "more than 3 a week").
  - Weekend shifts: a requirement set per bar. Weekend days are set per bar
    (Grøndahls: Friday and Saturday; Kvarteret is not open on Sundays except
    special events).
  - Counted per semester (spring: January to early June; autumn: mid-August
    to just after mid-December), for example "at least 4 weekend shifts per
    semester". The leader gets a pace warning halfway through the semester.
    Closed periods never count against anyone.
  - Option B, splitting each semester into two halves with its own target, is
    kept open for later as a per-bar choice.
  - The volunteer sees progress in Mine ("Helgeskift 1 av 4"), and
    `suggest()` prioritises people who are behind.
- Leaders see who has sent in wishes: per bar and period, "x av y har
  svart", with the list of who has not answered. A reminder button comes
  with email (step 8).
- Training and approval: one shared pattern for skjenker training, skiftleder
  training and new chefs.
  - The system counts completed training shifts from the schedule: the shift
    has happened, the person was on a training place, and no "kan ikke". No
    attendance confirmation.
  - The number of shifts before approval is set per bar (default 3). At that
    number the leader gets "Klar for godkjenning" with "Godkjenn" or "Ett
    skift til".
  - Approval can be done by anyone who is Gruppeadmin of that bar (the
    leader, the personalansvarlig, or anyone an Admin assigns), the Skjenke
    Gruppeadmin (Skjenkeleder), and Admin, as in the rights decision below.
  - The volunteer sees "x av 3" and "venter på godkjenning" in the app.
  - **Open, to discuss with the bar leaders** (not decided, nothing to build):
    many people switch bars, also part way through training. Morten's
    leaning: training shifts for skjenker count across the Skjenke bars, the
    person shows a mark "opplæring i annen bar: N skift", and the new bar's
    leader decides whether to start training. The count comes from the
    assignments (`training_for`), not from the row. Skiftleder and kokk stay
    per bar. **Until decided, the existing rule stays:** training is valid
    only in the row's own group. Note also that when a member is moved to
    another bar, an old training row becomes inert, because it needs
    membership of its group.
- Skiftleder training: the trainee is the skiftleder on the shift, with an
  experienced skiftleder who "tar opplæring" present. The kitchen has no
  skiftledere; a new chef works with a kokk who "tar opplæring".
- **Qualifications, not verv, decide who may take which places** (decided;
  eligibility built in 5d step 2, the screens for training and approval are
  not built yet). Each person has qualifications (`skjenker`,
  `skiftleder`, `kokk`), each with "tar opplæring" yes/no, who approved it
  and when. For now they last forever. A place is open to someone who has the
  needed qualification valid for the shift's group (see "Where
  qualifications are valid"), whatever their verv (Skjenker, Styremedlem,
  Skjenkeleder, etc.); board members usually take shifts. Verv only says what
  job someone has this semester. Approval in 5d creates the qualification.
  Being in training is a training row a leader starts (not the Opplæring
  verv, which the copy script reads once). The old verv rule in `planning.py`
  is replaced by this (step 2), and existing verv need a one-time copy into
  qualifications **before the pilot**, by
  `scripts/shifts/copy_verv_to_qualifications.py` (see "Migration" under 5d;
  decided 2026-10-29).
  - **Where qualifications are valid** (decided 2026-10-29; replaces "active
    member of the shift's group" for crew places). The person must have the
    shift's group in their **area** (decided 2026-10-29). A person's area is
    (a) the participating groups they are an active member of this semester,
    plus (b) the participating siblings of those groups (same parent, the same
    rule as the leader pool), plus (c) the participating direct children of
    those member groups (decided 2026-10-29: a group above Skjenkegruppen
    covers nothing). Reason: the Skjenkeleder, and some board members, are
    registered only in Skjenkegruppen and must still be able to take bar
    places. Unchanged: kokk places still need an approved kokk row for the
    kitchen group, so the widening gives nobody a kokk place; training still
    needs membership of the row's own group; a skjenker qualification still
    decides bar places; Halvtimen keeps its 20+ rule. A skjenker
    qualification works in that group and in its participating sibling
    groups (same parent, the same rule as the leader pool; no Skjenke slug).
    Halvtimen is included, and its 20+ rule stays. A skiftleder
    qualification works in the group where it was approved, and in sibling
    bars only when the receiving bar has switched that on (next bullet).
    Kokk stays kitchen only (the group where it was approved), with no
    switch. Anyone who is Gruppeadmin of a sibling bar (leader,
    personalansvarlig, or anyone an Admin assigns), the Skjenke Gruppeadmin
    and Admin can place the person, since the pool already allows it; people
    often agree this on Slack first.
  - **Skiftledere from other bars** (decided 2026-10-29): a per-bar switch
    on the receiving bar, in `/shifts/settings`: "Skiftledere fra andre barer
    kan lede skift her". Off by default. Off: a skiftleder place needs a
    skiftleder row for the shift's own group (approved there, or by
    "Godkjenn direkte"). On: any approved skiftleder row in a participating
    sibling group also counts. Kokk has no switch. Anyone who is Gruppeadmin
    of the receiving bar, the Skjenke Gruppeadmin and Admin can flip it. The
    column (`shift_group_settings.sibling_skiftleder_allowed`) is added in 5d
    step 1; the logic is 5d step 2.
  - **"Godkjenn direkte"** (decided): anyone who is Gruppeadmin of the bar
    (leader, personalansvarlig, or anyone an Admin assigns), the Skjenke
    Gruppeadmin and Admin can grant an approved
    qualification without training shifts, for people who already know bar
    work, chefs who can stand in the bar, or a skiftleder from another bar.
    Logged with who and when.
  - **"Start opplæring"** is explicit (decided): the same people (Gruppeadmin
    of the bar, the Skjenke Gruppeadmin, Admin) start it, for one or several
    people at once. Nobody is in training automatically.
  - **"Tar opplæring" on copied rows** (decided 2026-10-29; replaces "Migrated
    skiftledere and kokker get tar opplæring on"): on for a skiftleder or kokk
    row if the role was held in the current or previous semester, off for
    returning people; leaders adjust. The kitchen has no skiftleder; chefs
    differ only by "tar opplæring" (willing to teach new chefs or not).
  - **The skiftleder seeing trainees in the app**: later (decided), not
    needed in the store build.
- **Skjenkekurs is not a course.** Skjenke members are trained through
  training shifts (5d), so skjenker training is not modelled as a course.
  Barkurs is only for others (samfunnettjeneste in bar places), as the code
  already does. If a course named Skjenkekurs exists in Personal, the app must
  not show it. Nothing else uses Skjenkekurs completions.
- "Tar opplæring / tar ikke opplæring" is a setting on the **qualification**
  (skiftleder, kokk), set only by a leader, chosen at approval and changeable
  later. Anyone in training is only placed on shifts where the skiftleder (or
  kokk in the kitchen) tar opplæring; the board and `suggest()` check this,
  and leaders get a warning otherwise. This replaces the earlier per-person
  "tar ikke opplæring" note (the old "(AS)/(AV)").
- Wishes in the app become a month calendar: each day shows one dot per
  shift, coloured by the answer (vil green, kan yellow, kan ikke the app's
  maroon, not set white with border). The volunteer selects dates, or taps a
  weekday letter (M–S) to select those days in the shown month, picks which
  shifts the answer applies to ("Alle skift" plus one option per shift name; (replaced 2026-11-02 evening, see "Changes after the design pass (2026-11-02 evening)"): the options are by position in the day, per bar),
  then vil / kan hvis nødvendig / kan ikke. A weekday tap only selects dates;
  it no longer makes a standing semester rule. The three answers stay. No
  longer app-only: server changes are needed (replaced 2026-10-30, see 'Wish
  calendar and leader page package').
- Halvtimen requires age 20+ (show only "20+" in the app, never birth date).
- Kjøkken is part of Skjenke; kitchen places are for chefs only; kitchen
  shifts are shown in the Stjernesalen card ("Stjernesalen og kjøkken").
- Becoming skiftleder: skiftleder training shifts (default three, per bar),
  not a course. See the training and approval decision below.
- Brann- and barkurs valid 2 years; completions are stored per semester, so
  compute expiry as 4 semesters later. Barkurs is only required for
  samfunnettjeneste in bar places; brannkurs for everyone. A person must be
  signed up for a course that takes place before the shift.
- Wishes: the answers are vil gjerne / kan hvis nødvendig / kan aldri / ikke
  satt, one per shift. The weekday rules that were here (weekday rules plus a
  per-shift override) are retired (replaced 2026-11-02, see 'Design pass results'). Monthly allocation is made and
  published between the 15th and 20th of the previous month. Volunteers can
  set wishes for the whole semester.
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
  Wishes: per shift only; the weekday rules are retired (replaced 2026-11-02, see 'Design pass results').
  `GET /wishes` lists
  weeks that have shifts, from today through the end of the semester (spring
  Jan–Jun, autumn Jul–Dec) and at least through next month, so January takes
  wishes in December (`effective_week_status`, `week_start`,
  `wish_window_end` in `app_rules.py`; migration `20261029_1200`).
- Privacy: wishes visible only to the person and the group's leaders;
  "nei" never has a reason; swap/sick messages optional and visible only to
  Skjenkeleder and the leder/personalansvarlig of that bar or kitchen (not
  skiftleder), deleted after the shift; names on past shifts kept this and
  the previous semester, then only counts. Get Samfunnet's privacy person
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
- Privacy of names in the app (decided, **not built yet**, planned for step
  4c; replaces the earlier `none` / `roles` / `first_names` / `full_names`
  per-group plan). The first name stays the default shown.
  - Your own group sees your name by default (Grøndahls sees Grøndahls).
  - The rest of Skjenke does not, unless you choose "show me to all of
    Skjenke".
  - People on the same shift, including samfunnettjeneste volunteers, see
    each other's names.
  - You can hide yourself; then other members, including people on your
    shift, see only the role ("Skjenker").
  - The group's leader and the skiftleder on the day always see everyone.
  - The group's leader sets the default; each person can make it stricter
    (hide) or wider (all of Skjenke).
  - The hide option must be switchable from the server, per group and
    globally. The app only shows it when the server says so, so it can be
    removed without a new app release. Reason: hiding cuts both ways (the
    person someone wants to avoid can also hide). Review it after the pilot
    and with Samfunnet's privacy person. The leader remains the safety net for
    people who should not work together.
  - The app parts (the switch and showing role-only) do not need to be in
    the store release (decided 2026-10-29): they are JS and come together by
    OTA. The rules stay on the server. This depends on how OTA updates are
    published (see "Not code").
- **Dropped:** "may not work together" pairs. It would store sensitive data
  in one more place; the leader handles this when planning. The earlier
  design (explicit "manage pairs" permission, neutral refusals, logging) is
  not built and is no longer planned.
- Skiftlista will expand to other groups (for example Vakt), so nothing may
  depend on the Skjenke group's slug. A group takes part when it has a
  `shift_group_settings` row or shift templates, or has a descendant that
  does (the ancestor rule is for permissions, scope and the pool only:
  `/shifts/templates`, `/shifts/generate` and the settings cards list just
  groups with their own row or templates, in tree order); only an Admin adds
  a group (`/shifts/settings`, "Andre grupper"). Qualifications plus active
  membership decide which places a person is eligible for (verv does not; see
  "Qualifications"); leader rights are explicit Admin assignments and never
  derived from verv.
- Build order: 4b, then 4c (privacy settings), then 4d (edit one shift on the
  board; editing a whole series is dropped), then 5 onward. Exception
  (2026-10-29): the "+" and "−" per role from 4d are built as "4d-plus"
  between 5d step 2 and step 3. After 5d step 2, the wish calendar and leader
  page package comes first, then 4d-plus, then 5d step 3 (replaced 2026-10-30,
  see 'Wish calendar and leader page package').
- **Samfunnettjeneste** (decided in a design session from Samfunnet's
  spreadsheet and a read-only code review; **not built yet**, steps 7a–7h).
  It is not a role or a qualification; it is done by people in many groups.
  Terms: "samfunnettjeneste managers" hold the MANAGE right, "samfunnettjeneste
  viewers" hold the VIEW right (see Rights below). These are rights, not a
  title: Internansvarlig is only an example of who might hold one, and
  Nestleder, Skjenkeleder, Hovedstyret members or a stand-in can too, several
  at once. Never treat any of this as a system role.
  - **What it is:** extra help so the house works. Places come from two
    sources only: (a) bars and the kitchen open places on their own shifts
    when they are short, and (b) events for the whole organisation that
    Hovedstyret has decided should have samfunnettjeneste places; samf
    managers create those event shifts. A group's own events are its own work
    and never count, even if they have shifts.
  - **Event bars are not skjenke** (decided): Speilsalen, Eldorado (the bar
    for Tivoli) and Teglverket (two bars in one room) are staffed by the group
    that runs the event (committee, BORG or DORG) with its own people, and the
    barkurs lets those people stand in the bar. Skiftlista does not plan them.
    The skjenker qualification rules do not change.
  - **Group types:** an explicit group type (komité, BORG =
    brukerorganisasjon, DORG = driftsorganisasjon, other). Not
    `discount_tier`, but it may pre-fill the type. Internbevis tiers are
    1 = BORG, 2 = DORG, 3 = Samfunnet (internal groups and komitéer, the old
    "arg"), so the Integer `groups.discount_tier` fits: pre-fill 1 as BORG,
    2 as DORG, 3 as komité, and no pre-fill for "other". Still check this
    against real data first, since a group's tier might reflect a deal
    rather than its type. Standards: committees 1 shift
    per active member, DORG 1 per active member, BORG a fixed number of shifts
    as an organisation (from its agreement).
  - **Duty settings (samf managers only):** per type a standard (per active
    member, a fixed number, or none, with a number); per group an override
    with its own counting period and a required reason, logged with who and
    when. Changing a standard asks for confirmation, names the overridden
    groups that will not change, and offers "save for groups following
    standard" or "save for all and remove exceptions".
  - **Exempt groups:** a per-group setting "Medlemmene er fritatt" on every
    group, off by default, on for Skjenke and its subgroups. Only samf
    managers or Admin change it, with a required reason, logged with who and
    when; the group list shows a "fritatt" tag. The exemption follows the
    person: an exempt person is not asked for samfunnettjeneste and is not
    counted in any other group's target (a Stjerne member who is also in Fest
    does not count in Fest's active members). This replaces the hard-coded
    `is_skjenke_member` check in `counts_as_samf` (`app_rules.py`,
    `app_service.py`).
  - **People in several groups:** "Ett skift holder" (default), "Velg
    gruppe" or "Hovedgruppe", plus a maximum number of shifts asked of one
    person per semester (default 1). A shift never counts for both a committee
    and a BORG/DORG; a person in both chooses which when taking the shift
    (fixed rule, no setting).
  - **Overview page in Personal** for samf managers and viewers (viewers see
    no buttons that change anything): totals (done of target, weeks left); a
    one-line rules summary with an "Endre" link (managers only);
    "Trenger oppmerksomhet", shown only when something waits (groups behind
    pace, empty places in the next 7 days, "kan ikke", a start-of-semester
    reminder to check rules and carried-over exceptions); all groups sorted by
    who is most behind, with done and planned shown separately and "overstyrt"
    and "fritatt" tags; open samfunnettjeneste places with "Legg til
    arrangementsskift" (managers only, for events Hovedstyret has approved).
    Rules and exceptions live on a separate page behind the link. Bars still
    open their own places.
  - **Group leaders (komité, BORG, DORG):** a read-only card with their duty
    and progress and who has not done a shift; "Meld på medlem" (sign up their
    own member, for people without a smartphone or without the app working);
    and busy periods ("Når vi ikke kan"), which replace the spreadsheet's
    "Utilgjengelig" and are information only for bar leaders and samf managers
    and viewers. A group leader cannot change their own duty. An organisation
    leader sees only shifts that counted for their organisation, not their
    members' other shifts.
  - **Attribution is stored:** each samf assignment records which group it
    counted for (e.g. `shift_assignments.counted_for_group_id`). Today samf is
    derived on the fly (`counts_as_samf`), so past counts change if someone
    joins Skjenke mid-semester. Auto-filled when the person is in one group;
    asked in the app claim when in several; leader placement also sets it.
  - **No-show:** after a shift the leader can mark "møtte ikke" and record who
    replaced them. The replacement gets the shift; the no-show counts for
    neither samf nor training. Smallest model: `no_show_at` and
    `replaced_by_assignment_id` on `shift_assignments`.
  - **Reminders ("Send påminnelse"):** go to the group's leader. If the
    group's duty is per active member, they also go to members who have not
    done a shift. Uses the existing email outbox; needs new templates and a
    cron route (step 8).
  - **Descriptions:** shift descriptions become editable in Personal (a table
    by group and kind, with a default and the built-in text as fallback; a
    card in `/shifts/settings`). Event shifts may need their own meeting point
    per shift. Group leaders keep their membership lists current, as today.
  - **End-of-semester report** per committee and organisation, downloadable
    or printable, mainly for BORG agreements. Samf managers and viewers can
    see it.
  - **Future idea, not now:** exempting a single person (for example on
    leave) would use the same pattern but touches personal circumstances, so
    the privacy person must be asked first.
- **Rights for samfunnettjeneste** (decided, **not built yet**): a new
  explicit named permission across all groups (a small permission table plus a
  dependency); not Admin, and not Gruppeadmin on the root group. It is granted
  to people, not tied to a title, and several people can hold it at once. Two
  levels: MANAGE (rules and exceptions, overrides, exemptions, event shifts,
  reminders, everything on the overview) and VIEW (overview and progress only,
  nothing that changes data; for example for Hovedstyret). Only an Admin
  grants or removes it, and every change is logged with who and when. Bar role
  mailboxes (grondahls@ etc.) are not logins; the people holding those roles
  get rights individually.
- **Training simplification** (decided, **not built yet**, done together with
  5d, before the store release). Drop the `opplaering` slot kind and
  `training_replaces_slot`. Main place types are Skiftleder and Skjenker
  (Kokk in the kitchen). A person in training takes a normal place and is
  shown with a dashed outline and a counter ("opplæring 2/3"), stored per
  assignment so old counts stay correct. "In addition" is done by adding a
  place. A shift with someone in training must also have someone who tar
  opplæring, otherwise the board warns. This replaces the `opplaering` and
  `training_replaces_slot` rules in "Rules implemented" and the training
  lines in the decisions above. The app is not in the store yet, so its use of
  kind `opplaering` can change before release.
- Names: Samfunnet is the organisation, Kvarteret is the house, etjenesten is
  Samfunnet's IT group, and Samfunnet pays for hosting.
- Ownership is role based (Skjenkeleder, HS leder/driftsansvarlig); role
  admins can grant access; a maintenance role for etjenesten.
- Notifications: Slack is the main channel, with a layer that can add email
  (direction only, nothing built; see step 8) (replaced 2026-11-02, see 'Design pass results'). Push to be built
  together with etjenesten's planned shared notifications.

### Wish calendar and leader page package (decided 2026-10-30, adjusted 2026-11-02)

Decisions Morten made on 2026-10-30, adjusted on 2026-11-02. Where this
section differs from an older bullet, this section wins; the older bullets are
marked "replaced". Nothing here is built yet. The design pass has run; its
decisions are in "Design pass results (decided 2026-11-02)" below, and where
the text between here and that subsection differs from it, the subsection
wins. Items marked **Open** are still open.

**Priority.** The wish calendar (5e) is what the bar leaders wanted most at the
demo meeting. It moves to the top as one package, together with the leader page
that uses the wishes. 4d-plus and 5d step 3 come after.

**Mockups.** `docs/skiftlista/mockups/Main.dc.html` (app, "Ønsker, desember
2026", Nynorsk) and `docs/skiftlista/mockups/Leader.dc.html` (leader page on a
laptop, Bokmål like today's board). Morten's copy in Claude Design:
https://claude.ai/artifact/McyzpMmfTYqwf5vJvmA6wM (only Morten can open it).
- The files are reference only. They load `./support.js` from the Claude
  Design canvas, which is not in the repo, so they do not run on their own.
  Read the markup and the Component script.
- The decisions in this file win where a mockup differs. Known differences:
  - App mockup: it is Nynorsk, but the app is Bokmål plus English (decided
    2026-11-02); the mockup still shows the "Ukedager" idea and the weekday
    semester line, which no longer apply (weekday rules are retired). It has "Begge skift / Dagskift / Kveldsskift" (decided:
    "Alle skift" plus one option per shift name) and the line "Svar på ein
    heil ukedag blir ståande for heile semesteret" (no longer applies: a
    weekday letter only selects dates). It shows "Svarfrist 15. november" but
    lacks days left, the not-answered banner, note, maks, own shifts, "Kopier
    fra forrige måned", "Legg til i kalender" and the month switch.
  - Leader mockup: it lacks "Vis andre", note and maks in the people list, the
    maks warning, opplæring, the month strip and "Sist endret av". Its
    suggestion logic is deliberately naive; the real one is below.
  - Updated 2026-11-02 evening: `Main.dc.html` predates the month stepper, the
    "Svaret gjelder" buttons by position, the pinned action bar and the
    week-number column. The real UI is described in the package section under
    "Built 2026-11-02" and "Changes after the design pass (2026-11-02
    evening)". A new mockup for the week-number column, "Ferdig", the deadline
    banner, note and maks comes first in A2. The leader mockup shows
    Grøndahls from Tuesday; the real days are Monday to Saturday.
- **Decided 2026-11-02:** the app language is Bokmål plus English, like the
  rest of the app. The Nynorsk mockup is reference only.

**App calendar (Ønsker tab).**
- A month view; one dot per wishable shift per day, left to right by start
  time. Colours: vil green #3E9B53, kan hvis nødvendig yellow #E9B824, kan
  ikke the app's maroon, not set white with a border. Days with no shifts are
  greyed out. Full months are shown, with a month switch (replaced 2026-11-02 evening, see "Changes after the design pass (2026-11-02 evening)"): a stepper "‹ November 2026 ›" for any number of months; the months run from the first month of the current semester, earlier months are read only).
- Tapping a date toggles it (dark border). Weekday letters M T O T F L S
  select those dates in the shown month. A "Svaret gjelder" row with "Alle
  skift" plus one option per shift name, hidden when there is only one name (replaced 2026-11-02 evening, see "Changes after the design pass (2026-11-02 evening)"): by position in the day within each bar).
  While dates are selected, the dots the answer will change get a dark ring.
  Answer buttons plus "Fjern svar"; a count of selected dates; a legend.
- An answer applies to the selected shifts on the selected dates in the shown
  month only. A weekday letter only selects dates; it does not create a
  standing rule. "Kopier fra forrige måned" repeats one's own answers for the
  next month (replaced 2026-11-02 evening: "Kopier fra forrige uke", copy rule
  open; see the changes subsection). (Decided 2026-11-02; this replaces the
  idea from 2026-10-30 of standing weekday rules limited to hours of the day.)
- Weekday rules are retired (decided 2026-11-02; replaces the open question
  here): reads stop using them, the separate "Ukedager" list goes, and there is
  one place to answer. Details under "Design pass results". Rejected: an extra
  "gjelder hele semesteret" switch (clutter).
- The answer deadline shows in the app ("Svarfrist ...") with days left, and a
  banner if the person has not answered. Each bar's leader sets the deadline
  (per bar and month, soft: see "Design pass results"; replaced 2026-11-02
  evening: per bar and week, N days before the week starts).
- Saving many answers at once, all or nothing (a batch endpoint): yes
  (decided 2026-11-02, `PUT /wishes/answers`).
- Yes: a short note to the leader per month (for example "Eksamen 10.-14."); a
  personal "maks skift denne måneden" (both replaced 2026-11-02 evening: per
  week, set by the volunteer); one's own published shifts marked in the
  calendar; "Legg til i kalender".

**Times.** Hours are used only to show shift times and to count total hours
worked, both from the shifts' own start and end. A shift belongs to its start
date (as already decided for weeks): its dot is on the start date, and it
counts for that day's "two shifts the same day" check and that weekday's rule.
The overlap check uses real start and end times. Total hours and "Legg til i
kalender" must use the real end date and time (bar shifts end after midnight).

**Leader page (Personal Skiftplan).** Wishes and placing in ONE screen that
fits a laptop, with little clutter.
- One short top line: bar tabs, "Har svart N av M" with "Vis hvem", the
  deadline.
- A week bar: previous and next week; week status ("Utkast: ikke synlig i
  appen", "Publisert", "Endret etter publisering"); small buttons "! N
  problemer", "N ledige" and "N uten skift" (people who answered but have no
  shift that week; clicking selects the first so the grid shows where they
  fit); "Kopier forrige uke" (as suggestions, skipping "kan ikke"); "Skriv ut"
  (week grid only); "Godta forslag (n)" and "Forkast forslag" when there are
  suggestions; "Lag forslag for uka" (red); "Publiser uka" once no suggestions
  are left. Nothing reaches the app before the week is published, except that
  an untouched week (a future week with no `shift_week_status` row) publishes
  itself on its Monday (replaced 2026-11-02, see "Design pass results", item
  13); pending suggestions never show in the app. Only the bars' admins
  publish.
- The week grid: days as columns, shifts as rows; every shift's first place is
  the skiftleder place (only approved skiftledere), the rest are skjenker
  places; names in the places; "N vil · N kan" per cell; suggestions dashed
  with "forslag" and "Valgt fordi: ..." on hover and in the panel; problems
  marked on the person (answered "kan ikke" or two shifts the same day in red;
  more than one a week, or above their maks, in yellow); a small "Fyll" link
  on shifts with empty places.
- The side panel is the people list "Folk denne uka", scrolling inside its own
  box (the page does not scroll for it): search; filters (Alle, Ikke plassert,
  Skiftledere); "SL" marks; "N denne uka · N i des"; "0 helg" in red where a
  bar has weekend requirements; the person's note and "maks" visible.
  Selecting or dragging a person colours every shift by their answer (vil /
  kan / kan ikke / ikke svart, in words too, "opptatt" if busy that day) and
  marks places they can take; drop or click to place. Clicking a day header
  instead colours and sorts the people list by their answers that day; each
  can be cleared. Tapping a place with nobody selected opens a short list for
  that place (Vil, Kan, sorted by fewest shifts) plus "Vis andre" with others
  who could take it (sibling bars with the right qualification, marked with
  their bar; own bar's "ikke svart"; "kan ikke" last and greyed); this is also
  how a leader finds a replacement on a sick day. Tapping a placed person
  offers swap or "Fjern"; dragging a placed person onto another swaps them,
  onto an empty place moves them, refused with a reason if a non-skiftleder
  would land on a skiftleder place.
- "Angre" after every change (up to 20 steps). "Lag forslag på nytt" fills
  around what the leader already chose. Fairness stats. "Sist endret av ...".
  A warning when two leaders edit the same week. A month strip of week
  statuses to jump between weeks: yes (decided 2026-11-02).
- The separate sidebar list and its sticky fix (5a2) are replaced by this
  panel.
- Opplæring: the trainee stands in a normal skjenker place marked "opplæring",
  shown with the skiftleder who takes trainees (as decided for 5d step 3); the
  suggestion pairs them.
- **Open:** a notification when a week is published. The channel is Slack
  first (direction in step 8) with email possible later; nothing is decided or
  built. The channel question stays open until etjenesten and Slack answers
  are in (see step 8).

**Suggestion algorithm and fairness.**
- Runs on Personal's server behind one function ("lag forslag for denne
  perioden"), deterministic, with "Valgt fordi" built from the rules. Plan A:
  Google OR-Tools CP-SAT (the known shift scheduling / nurse rostering
  problem). First check that the package fits Personal's hosting (Vercel
  function size limits) and that etjenesten accepts the new dependency. Plan B
  if either fails: "scarcest place first" plus an improvement pass with swaps,
  in plain Python. **Decided 2026-11-02: plan B**, plain Python, deterministic,
  with solver-neutral input and output. OR-Tools is not used now (protobuf
  downgrade, unverified size); the question to etjenesten stays as a later,
  non-blocking note.
- Hard rules for the suggestion: the right qualification for the place;
  Halvtimen 20+; never "kan ikke" on the shift (replaced 2026-11-02: weekday rules are
  retired);
  no overlapping shifts; the person's own "maks" and the bar's quota; never
  move what a leader placed or accepted.
- Maks for leaders (decided 2026-11-02): a leader may place someone above
  their maks by hand; it is allowed and shown as a yellow warning on the person (not red like "kan ikke").
- Goal: everyone who answered gets one shift a week, not only filled places.
  The suggestion never adds places; the leader adds them where it will be
  busiest, helped by "N uten skift".
- Fairness: earlier weeks of the semester count; fewer shifts so far gives
  priority; helpfulness is rewarded by what a person offers, not only by what
  happened to them: every shift answered "vil" or "kan" (not "kan ikke")
  counts as availability over recent weeks and the semester, "vil" more than
  "kan"; being placed on a "kan" shift adds extra credit; when someone must
  take a "kan" shift it goes first to the person who has offered least lately,
  and the most helpful get their "vil" days first; nobody gets more than their
  share; weekend requirements are spread fairly; trainees go on shifts with a
  skiftleder who takes trainees.

**Samfunnettjeneste (checked in the design pass, decided 2026-11-02).** Points
that were checked, and the answers:
- The suggestion only fills places for the bar's own pool and leaves places
  open for samf alone unless a leader fills them.
- Deadline, notes, maks and fairness counters are per bar and per person,
  separate from samf duty counting (7c, `counted_for_group_id`).
- Samfunnettjeneste people send no wishes and are not in "Vis andre" (pool
  rule, Finding D). This is decided (item 10).
- The data model does not block samf later. The samfunnettjeneste plan must go
  through `tables.py` or an injected protocol (module independence in the
  architecture doc, item 21).

**Known work the package needs.**
- Server: the batch endpoint; deadline per bar; notes and "maks" per person
  per month (a migration, read-only Opus review before Neon) (replaced
  2026-11-02 evening: deadline per bar per week, note and maks per person per
  week in one weekly table; the migration is split by design pass 2); the board's wish
  counts and "who has answered" (5b, if not built) from the calendar answers;
  week status and publishing (check what exists); the suggestion function;
  "Vis andre"; permissions.
- App (kvarteret-internbevis): the calendar, deadline, note, maks, own shifts,
  "Kopier fra forrige måned" (now "Kopier fra forrige uke"), "Legg til i kalender". JS only where possible so
  it can ship by OTA; no new native packages without asking Morten.
- Personal: the new leader page.
- Outside dependency for real use: the app reaching phones (store build or
  OTA) goes through etjenesten.

**New order of work.** Replaced 2026-11-02, and again 2026-11-02 evening: the
current order is under "Next action" in "Start here". The order of item 20
below is history. The old
list, for reference: (1) the design pass, (2) server sessions, (3) the leader
page, (4) the app calendar, (5) then 4d-plus and 5d step 3.

#### Design pass results (decided 2026-11-02)

Morten accepted the recommendations of `docs/skiftlista/design-pass-wishes.md`
and changed the copy rule (item 7). The reasoning, the data model, the
endpoint shapes and the build split (section J) are in that file; this is the
short form. Item numbers are those of the decision list; items 14 and 17 are
replaced by items 14 and 20 of the later list (see "Conformance pass
results").

1. **Weekday rules are retired.** Reads stop using them: `effective_answer`
   takes only the per-shift wish; `get_wishes` and the board stop reading
   rules. `PUT /wishes/weekdays/{weekday}` stays but is deprecated and writes
   nothing that is read. `GET /wishes` returns `rules: []`. The "Ukedager"
   list in the app goes. The table `shift_availability_rules` is dropped in a
   later migration after the store build (and is then also removed from the
   volunteer delete cleanup). `seed_demo.py` writes per-shift answers instead
   of weekday rules. The suggestion's hard rule is "never 'kan ikke' on the
   shift".
2. **"Har svart N av M":** at least one `shift_wishes` row on a shift of that
   bar in that month, any answer. A note alone does not count. Board counts
   and names come from `shift_wishes` only. (Replaced 2026-11-02 evening: "har
   svart" is per week and means the person marked the week done, "Ferdig".
   Until S2 this monthly rule is the interim meaning, "har begynt"; see the
   changes subsection.)
3. **Deadline** (replaced 2026-11-02 evening: per bar per week, N days before
   the week starts; the column below is not built): per bar per month. Column
   `shift_group_settings.wish_deadline_day` (day of the month before, 1 to 28,
   null = none), set on `/shifts/settings`. Soft: the app keeps accepting
   wishes until the week is published; after the deadline the text says
   "Svarfrist gått ut". No per-month overrides now.
4. **Note and maks** (replaced 2026-11-02 evening: per person per week, in a
   weekly table of one row per person per week, `volunteer_id`, `week_start`,
   `done_at`, `note`, `max_shifts`; `shift_month_answers` is not built): new
   table `shift_month_answers` (volunteer, month, note
   up to 200 characters, `max_shifts`), per person across all bars; leaders
   whose pool includes the person see the note. Added to the volunteer delete
   cleanup.
5. **"Alle skift" plus one option per shift name** (replaced 2026-11-02
   evening: options by position in the day within each bar): built in the app
   from the distinct shift titles in the shown month; no storage. The batch request
   carries explicit `shift_ids`.
6. **Fairness counters** are computed on demand (new `fairness.py`), not
   stored.
7. **Endpoints:** `PUT /wishes/answers` (batch, all or nothing, idempotent, 1
   to 400 items, duplicates refused); `GET /wishes/calendar?month=` (month
   view: deadline, days left, `has_answered`, note, maks, shifts with day,
   answer, `accepts_wishes`, `past`, and the person's own published shifts);
   `PUT /wishes/calendar/month` (note, maks); `POST
   /wishes/calendar/copy?month=` ("Kopier fra forrige måned", server side,
   never overwrites). (The two month endpoints, `PUT /wishes/calendar/month`
   and `POST /wishes/calendar/copy?month=`, are replaced 2026-11-02 evening by
   weekly ones, shapes to be set by design pass 2. `PUT /wishes/answers` and
   `GET /wishes/calendar` are built.) **Copy rule (Morten's change to the design pass; open again 2026-11-02 evening, since the three quarter threshold was designed for a month):** copy
   a weekday-and-shift-title pattern from the previous month only when about
   three quarters or more of its dates had the same answer, so one-off answers
   (exams, trips) are not repeated; never copy the note. Published and past
   weeks show dimmed and are not selectable.
8. **Suggestions** are stored in their own table `shift_suggestions`, not in
   `shift_assignments`. "Godta forslag" re-checks each one through the normal
   assign path and reports skipped ones; publishing is refused while a week has
   suggestions. "Angre" is client side (sessionStorage, 20 steps) using
   inverse actions. Plan B (plain Python, deterministic, solver-neutral input
   and output) is chosen for the suggestion function; OR-Tools is not used
   now (protobuf downgrade, unverified size). The OR-Tools question to
   etjenesten stays as a later, non-blocking note.
9. **Migrations** are split, all additive: **M1** with S2 (`wish_deadline_day`,
   `shift_month_answers`, and the extra items in item 20 below; replaced
   2026-11-02 evening: the contents of M1 and S2 come from design pass 2, with
   the weekly table, the weekly deadline and the dials); **M2** with
   L3 (`shift_suggestions`); **M3** with L4 (`shift_week_activity` for "Sist
   endret av" and the two-leaders warning). L4 may be cut down. **Open:**
   whether the existing admin activity log can serve instead of
   `shift_week_activity`. Each migration gets a read-only Opus review before
   Neon.
10. **Samfunnettjeneste** people send no wishes and are not in "Vis andre"
    (pool rule, Finding D).
11. **App language:** Bokmål plus English, like the rest of the app. The
    Nynorsk mockup is reference only.
12. **Opplæring on the leader page:** as today (opplæring places) until 5d
    step 3. "0 helg" and the per-bar quota depend on 5c (not built) and are
    inert until then. **Open:** where 5c goes in the order.
13. **Week auto-publish and the banner.** A future week without a
    `shift_week_status` row stays open for wishes and publishes itself on its
    Monday. Kept. The text "Nothing reaches the app before the week is
    published" now reads "...except that an untouched week publishes itself on
    its Monday". Pending suggestions never show in the app. In the pilot,
    planned weeks are published with "Publiser måneden" (replaced 2026-11-02
    evening: publishing is per week; "Publiser måneden" is an optional
    shortcut). The unpublished-week
    banner becomes large: full width at the top of the board and the leader
    page, warning colour from 10 days before and the strong warning colour from
    3 days before, not dismissable. It names the consequence (how many places
    are empty and "blir åpne for alle mandag", how many pending suggestions the
    week has) and has the "Publiser" button. It is shown to everyone whose
    scope covers the bar. For the skjenkeleder (Gruppeadmin of Skjenke) and
    Admin it lists every bar in scope that has an unpublished week starting
    within 10 days, so they see all bars at once; no new right is needed. The
    banner change is small enough to go in S1 (text and style on today's
    board) and is carried into the leader page in L1. The reminder message
    (step 8) goes to the bar leaders and to the skjenkeleder.
14. **Calendar, both ways** (replaced by item 14 of the later list, see
    "Conformance pass results"). A3 is the single-shift button: iOS gets a
    short-lived signed `.ics` link opened with `expo-linking`, Android a
    Google Calendar template link; no new native packages; `expo-calendar` is
    not used; A3 gets a short read-only Opus look at the signed link. A
    separate later session adds a **personal calendar feed**: one subscribe
    link ("Abonner på vaktene dine") that works in Google Calendar, Outlook and
    Apple Calendar and updates itself; a reset-link button; the feed holds
    only that person's published shifts and no other names; it needs a stored
    secret per person (a small additive migration **M4** with a read-only Opus
    review). Google refreshes subscribed feeds slowly (often up to a day), so
    urgent changes still go by message. Messages in Slack link to the app
    (Mine tab), not to the 10-minute `.ics` link. Privacy: Google and
    Microsoft fetch the feed, so they see shift titles, times and venues; this
    is a question for Samfunnet's privacy person (see "Not code").
15. **Leader page:** built as a new route next to the old board, then
    `/shifts` switches to it; server-rendered Jinja plus one new plain
    JavaScript file with a JSON data block; the people panel replaces the
    sidebar list.
16. **Conformance pass:** see "Conformance pass results" below.
17. **Session order:** replaced by item 20 of the later list.
18. **Notifications (step 8), direction only, nothing built.** Written into
    step 8 below.

#### Conformance pass results (2026-11-02)

A read-only pass comparing our work with Kvarteret's own repos and their
guidelines. Report: `docs/skiftlista/conformance-pass.md` (do not edit it; it
ran on Sonnet, so an Opus pass can spot-check its judgement calls if that turns
out costly). Items 16, 19, 20 and 21 of the decisions.

- **16. The pass and the standing rule.** The pass compared the guideline
  inventory, our changes against each rule, drift from upstream main
  including the migration chain, and our footprint in shared code. It fetched
  only; never push to Kvarteret's repos. Standing rule: new code follows the
  guidelines the pass finds; a rule we deliberately break is written down with
  the reason. A short repeat runs before anything goes toward etjenesten or
  the pilot.
- **Summary of the report.** Personal is 33 commits ahead and 0 behind
  `upstream/develop`; the app is 6 ahead and 0 behind `upstream/development`.
  Upstream's Alembic head `20261025_1200` is the start of our chain, so
  nothing collides. Personal passes ruff, lint-imports, pytest (1080) and the
  openapi check; the app passes biome, tsc, jest and depcruise.
- **19. Answers to the report's six questions.** (1) Nothing from
  `docs/skiftlista` goes upstream; upstream gets a clean branch of code, tests
  and reference docs only, and a docs split (durable parts into
  `docs/reference` and `docs/explanation`) comes before any upstream PR. (2)
  Zod in the app: a written deviation (precedent: upstream's events client);
  add a Zod parse only for the most variable responses if reviewers ask. (3)
  Hide the Skift tab when Skiftlista is off, using the config query, before
  the store build. (4) Split `app_service.py` (1,276 lines against a written
  cap of about 800) before S1 as a pure move. (5) RLS, the index rename and the
  missing constraints go into the package's first migration M1. (6) App
  language Bokmål plus English.
- **20. Session order** (replaces item 17 of the first list).
  - **S0** (Sonnet, M): split `app_service.py` by area (claim, mine, wishes,
    courses); `ShiftsAppService` stays as a thin facade; pure move, tests
    unchanged, no behaviour change.
  - Then **S1 and A1** (first slice, no migration). S1 also logs the app-side
    wish events like claim (`shift.app.wishes`). A1 also gives the colour dots
    text equivalents, regenerates the Personal client (`npm run
    api:generate:personal`), and adds the missing "// Style escape hatch"
    comment in `ProgressBar.tsx`.
  - Then **S2** (M1 and the rest of S2 as in the design pass), **A2**, **L1**,
    **L2**, **G1**, **L3**, **L4**, **A3**, then the calendar feed (item 14).
    (Replaced 2026-11-02 evening: the order is L1, L2, demo update 2,
    conformance pass 2, Postgres e2e, design pass 2, S2, settings page, A2, G1,
    L3, L4, demo update 3, A3; see "Next action" in "Start here".)
  - **M1 contains** (replaced 2026-11-02 evening: the contents of M1 and S2
    come from design pass 2; the RLS, REVOKE, rename and constraint items stay,
    `wish_deadline_day` and `shift_month_answers` are replaced by the weekly
    table, the weekly deadline and the dials): RLS and REVOKE from PUBLIC, anon, authenticated for
    `shift_week_status` and `shift_closed_periods`; renaming
    `ix_shift_closed_periods_group_id` to `shift_closed_periods_group_id_idx`;
    `wish_deadline_day`; `shift_month_answers`. In the same session
    `tables.py` declares the 10 CHECK constraints and 9 indexes the migrations
    create, and all new tables get RLS from the start. M1 gets a read-only Opus
    review before Neon. Morten runs the Postgres e2e tests (needs a local
    Postgres and the Supabase stubs described in `run-tests.md`) before that
    review.
  - **Before the pilot:** confirm dialogs use `hx-confirm` (the new leader page
    does); a keyboard alternative on the board; check test and seed names.
  - **Before the store build:** hide the Skift tab; review the English strings.
  - **Before any upstream PR:** docs split; update
    `docs/explanation/kvarteret-personal-architecture.md` and
    `docs/README.md`; `api:check:personal` in the app's CI; commit subjects;
    split the work into reviewable series (about 23,700 new lines in Personal
    and 9,400 in the app); talk to etjenesten first about an ADR or issue,
    since the samfunnettjeneste plan touches groups and rights.
  - The demo needs `seed_demo.py` re-run on Neon after S1 (runbook step 6,
    host confirmation, and a fresh Neon rollback branch per runbook step 2).
- **21.** The samfunnettjeneste plan must go through `tables.py` or an
  injected protocol (module independence in the architecture doc).

Other 2026-11-02 decisions that are not in the lists above: Finding E is known
to etjenesten and being fixed, and unreported security findings are never put
in a committed file (see "Security review: open items" and the standing rules
under "Start here"); in the app repo the `upstream` remote is now `no_push`,
like Personal's.

#### Built 2026-11-02

Built and committed on 2026-11-02, after the docs commit `ba27207`, in the
build and demo session.

1. **Personal** (branch `skiftlista/skiftplan`; no migration, Alembic head
   `20261101_1200`; tests 1111 passed, 26 skipped):
   - **S0** `b154264`: `app_service.py` split into `app_claim`, `app_mine`,
     `app_wishes`, `app_courses` and `app_common` as mixins; the facade stays;
     pure move.
   - **S1** `d6df7c6`: weekday rules retired in reads; `GET /wishes/calendar`;
     `PUT /wishes/answers`; "Har svart N av M" with "Vis hvem" on the board;
     the `shift.app.wishes` audit event; `seed_demo` writes a rolling window of
     per-shift answers; a larger unpublished-week banner.
   - **S1d** `af06d65`: the calendar months run from the first month of the
     current semester; earlier months are read only; the default month stays
     the current month.
2. **App** (`kvarteret-internbevis`, branch `feat/skiftlista-mobile`; Jest 398
   passed): **A1** `f4772cd` (the calendar replaces the Ønsker list; the
   Personal client regenerated), **A1b** `179797b` (read from git log: positions
   per bar, month stepper, earlier months read only), **A1c** `ba5ec80` (answer
   bar, shorter day cells, collapsible explanation). Tested on an Android phone
   in Expo Go. **Not checked on iOS:** the NativeTabs bar placement of the
   answer bar is unverified.

#### Built 2026-11-02, L1a (leader week page, `19871a7`)

Personal only; no migration; tests 1164 passed, 26 skipped; Alembic head
unchanged.
- **Route** `GET /shifts/week` (query `group`, `week`), next to the old board,
  which is untouched except the link "Ny ukevisning" (and "Gammel skiftplan"
  back). Same scope and permissions as the old board; a group outside the
  scope is 403 before anything is read; bar tabs list only bars in scope.
- **Modules:** `week_page.py` reuses `get_week_plan` (shifts, pool with place
  tokens, status, banners) and reads the rest with existing repository
  methods; `week_view.py` is the pure builder (no I/O) and makes the JSON
  block `<script type="application/json" id="week-data">`. `service.py` and
  `repository.py` are unchanged.
- **Grid rows** are positions in the day within the bar (1., 2., 3. shift,
  rooms merged, start-time order); the row label is the most common title and
  time of that position in the week (tie: earliest day); Monday to Sunday
  always; days without that shift are greyed.
- **`shift-week.js`** only handles the print button (delegated click). The
  button may not work after an `hx-boost` navigation until a full reload
  (unchecked in a browser).
- **`return_to`** on `POST /shifts/weeks/status`: optional; `safe_return_path`
  in `actions.py` accepts only `/shifts` or `/shifts/...` (no scheme, host,
  fragment, backslash, control character, `..` or leading `//`); anything else
  falls back to `/shifts`. The new page's status buttons and banner send it.
- **Polish after browser checks** (`fa7166f`, then L1a-polish-4; tests 1172
  passed, 26 skipped). Decisions that replace the L1a brief where they differ:
  (a) a place box shows the person's answer as a round dot plus the word, only
  when the person has answered; unanswered persons show nothing in the grid
  and "ikke svart" only in the people panel (changes brief item 2d); problems
  tint the box (red or yellow) and are separate from answers. (b) The people
  panel's height follows the grid row (22rem minimum), its list scrolls inside
  (header outside the scroll), and below the breakpoint **1180px** it is
  stacked under the grid in a 24rem box (calculation in `input.css`: 7-day
  grid 51rem + borders 4px + gap 12px + panel 260px + page padding 64px +
  scrollbar 15px). (c) The grid keeps all seven columns; a day with no shift at
  any position collapses to a 2.5rem column ("Ma" and the date number, full
  name as hidden text), the others share the width, and no horizontal scroll
  is needed while the panel is beside the grid. (d) The status buttons sit on
  the same row as the week navigation when there is room, "Skriv ut" on the
  right. (e) "svarte kan ikke" shows as "!" plus the word "kan ikke" in the
  box, with the hidden text "Problem: svarte kan ikke".
- **Not in L1a:** suggestions, "Fyll", copy last week, "Angre", "Sist endret
  av", deadline, note and maks, "0 helg", "Vis andre", selecting a person, the
  place popover, drag and drop, search and filters (L1b, L2 and later).

#### L1a decisions and universal design (2026-11-02)

- **Week status badge:** published is "Publisert"; an open week with people
  placed is "Utkast: ikke synlig i appen"; an open week with nobody placed is
  "Åpen for ønsker". "Endret etter publisering" comes with L4.
- **"Har begynt N av M"** is interim (until the weekly "Ferdig" in S2): members
  of the bar in the leader's pool with at least one `shift_wishes` row on a
  shift of that bar in the shown week. "Vis hvem" lists the rest.
- **Counts across all groups** (shifts per day, per week) follow the old board,
  also for groups outside the leader's scope. **The month** for "denne
  måneden" and the week strip follows the Thursday rule (`week_month`).
- **Status buttons have no confirm dialog** yet, as on the old board; a confirm
  on "Publiser uka" is a later option (`hx-confirm` needs htmx on the form).
- **Universal design (decided):** WCAG 2.1 AA is the target for everything
  Skiftlista builds (the week page, the app calendar). L1a was built to it
  (table with caption and scopes, text markers "Problem:" and "Advarsel:",
  `details` instead of tooltips, focus outlines, checked contrast, labelled
  scroll region). The next big Opus check includes a code-level universal
  design audit. **Morten checks by hand before the pilot:** TalkBack on
  Android for the app calendar; keyboard only and a screen reader (NVDA or
  VoiceOver) for the leader page; 200% zoom and 320px reflow; forced colors;
  print preview; axe or Lighthouse on `/shifts/week`. Checklist for the leader
  page (cannot be verified without a browser): tab order and visible focus,
  also inside open `details`; the screen reader reads caption, headers,
  "Ingen skift", "Problem:" and "Advarsel:", and skips the decorative "!";
  the `details` lists are not clipped by the grid's scroll box at narrow
  widths; the table is usable at 200% zoom and 320px (it scrolls
  horizontally); colours as rendered; print preview (header and footer hidden,
  no blank pages, hidden text not printed); the print button after a boosted
  navigation.

#### Changes after the design pass (2026-11-02 evening)

Decisions made on the evening of 2026-11-02 after the build and demo session.
Where an earlier text differs, it is marked "(replaced 2026-11-02 evening)" in
place.

**Wish calendar changes** (replace the earlier text on the buttons, the month
pills, and history):
- **"Svaret gjelder" is by position in the day within each bar, not by shift
  name.** One bar in the month: "Alle skift", "1. skift", "2. skift" and so on
  up to the bar's largest day; the row is hidden when that is 1. Several bars:
  "Alle skift", then per bar "<Bar> 1. skift", "<Bar> 2. skift" (or just
  "<Bar>" for a bar with one shift a day). Selecting an option rings and sends
  only the matching bar and position, only shifts that accept wishes. The row
  is one line with a fixed height and scrolls sideways if needed.
- **The month pills are a stepper** "‹ November 2026 ›" that works for any
  number of months. It also shows with a single month (known small item).
- **Earlier months of the current semester are readable, read only:** dimmed,
  stored answers, own shifts marked, and the line "Denne måneden kan ikke
  endres". **Parked:** longer history (earlier semesters). It depends on the
  semester-end data cleanup and on Samfunnet's privacy person, and fits Mine or
  a profile better than the wish calendar.
- **The calendar always reserves six week rows,** so nothing below it moves
  between months.
- **A1c layout:** 48 point day cells; the hint and the legend are in one
  collapsed "Forklaring" row at the end of the scroll; the count and the four
  answer buttons (2 by 2) are in an action bar pinned above the tab bar, shown
  only when days are selected.
- **The default month:** the current month if any shift in it accepts wishes;
  otherwise probe forward from the current month; never fetch earlier months to
  decide it.
- **The "shift type" idea is rejected.** The dev seed names Stjerne's Tuesday
  evening shift "Quizskift"; it is left as is, since the buttons are by
  position.
- **Facts:** Stjerne usually has two shifts a day, Grøndahls and Halvtimen one;
  Grøndahls runs Monday to Saturday (the leader mockup's Tuesday to Saturday
  was a guess).

**Publishing is per week.** "Publiser måneden" is an optional shortcut. The
demo seed publishes only the current week and the next week (explicit rows),
deletes old explicit published rows for later weeks inside the wish window, and
leaves hand-set open or allocating rows alone.

**Weekly planning** (decided by Morten 2026-11-02; replaces the earlier monthly
decisions 2, 3 and 4 of "Design pass results", the month endpoints
`PUT /wishes/calendar/month` and `POST /wishes/calendar/copy?month=`, and the
M1 contents). Why: volunteers cannot commit a month ahead; the unit for
planning, answering and publishing is the week; a bar that plans further ahead
is a bonus, not a requirement.
- **a. Answering is unchanged:** any open day, any time; the month calendar
  stays for browsing.
- **b. "Har svart" is per week:** a person has answered week W when they have
  marked it done ("Ferdig" per week). "Påbegynt" means some answers in the week
  but not done; "ikke svart" means neither. The leader page shows "Har svart N
  av M" with "Vis hvem" for the displayed week. Until S2, "Har svart" on the
  board and `has_answered` in the calendar response mean "har begynt" (at least
  one answer in that month), and the first leader page (L1) shows the weekly
  count with that interim meaning, labelled accordingly. The board panel
  "Ønsker for <month>" becomes weekly.
- **c. The deadline is per bar per week:** N days before the week starts, set
  by the bar's leader; the app shows "Svarfrist" and days left for the week
  being asked about. It replaces the planned `wish_deadline_day` (day of the
  month before); that column is not built yet.
- **d. The personal "maks" is per week and the note to the leader is per
  week** (both replace per month), both set by the volunteer. The fairness
  counters over the semester stay.
- **e. "Kopier fra forrige uke"** replaces "Kopier fra forrige måned".
  **Open:** the copy rule (the three quarter threshold was designed for a
  month).
- **f. Storage:** one table, one row per person per week (`volunteer_id`,
  `week_start`, `done_at`, `note`, `max_shifts`), replaces `shift_month_answers`.
  There is no separate session S1e: the weekly "Har svart" arrives with S2.
- **g. Calendar (A2):** a week-number column on the left of the grid. Tapping a
  week number selects all selectable days of that week; its check state shows
  done, started or empty; a "Ferdig" action per week. Draw a mockup before
  building it. **Open:** where exactly "Ferdig" sits.
- **h. The leader page is a week grid** (days as columns, shifts as rows, the
  people panel beside it); the volunteer app is a month calendar for browsing
  and answering ahead.

**Dials and absences** (decided by Morten 2026-11-02 as the model; the details
go through design pass 2). Replaces the fixed "pensioner 1 a month" rule in 5c
and the by-role `quota_for` in `app_rules.py`: a pensioner quota is agreed
between the leader and the volunteer, so it is a setting. Nothing here is
built; the tables are not designed yet.
- **a. Everything is adjustable.** Bar defaults are set by the bar's leaders.
  Per-person values are set by a leader, and a volunteer can send a request in
  the app that a leader approves or edits. For the suggestion they are hard
  rules; a leader placing someone by hand gets a warning, never a block.
- **b. Per bar (defaults):** maximum shifts per week; minimum shifts per
  period; weekend shifts per semester; rest between shifts (a minimum number of
  hours between two shifts); leader-set limits (for example a maximum of
  weekend or late shifts per person per period); the answer deadline (N days
  before the week starts).
- **c. Per volunteer:** an agreed number of shifts per period (week or month)
  with a cap and a target (cap only for people without an agreement),
  overriding the bar default and replacing the by-role pensioner rule; an
  optional minimum override; recurring days off (a weekday, optionally a
  position within the bar's day, for example never Fridays), agreed with a
  leader; absences (a period with a start date, an optional end date, a kind
  Ferie, Praksis or Annet, and an optional note; leaders set them, volunteers
  send requests); the person's own maks per week (set by the volunteer);
  leader-set limits per person.
- **d. The cap that applies** is the lowest of the person's own maks, the
  agreed number and the bar's maximum.
- **e. Effects:** the suggestion treats caps, rest, recurring days off and
  absences as hard rules, and targets and minimums as goals. Absence days count
  automatically as "kan ikke" and as answered. Fairness counters, agreed
  numbers, minimums and reminders pause during an absence. The leader page
  shows a person's agreement and absence in the people panel (for example
  "avtalt 1/mnd", "borte til 30. nov"). People who are absent for a whole week
  are not counted in "Har svart N av M" or "N uten skift" for that week. "Under
  minimum" is a chip for leaders, not a block.
- **f. Privacy:** absence kinds are neutral (Ferie, Praksis, Annet); no health
  reasons are stored; free-text notes are a risk. This goes on the list for
  Samfunnet's privacy person (see "Not code"). Samfunnettjeneste duty counting
  is not changed by an absence (open).
- **g. Open:** default values per bar; the period unit of each dial; how
  recurring days off and agreements show in the calendar; the details of the
  request flow; whether the minimum is per week or per month.

**Open points from the weekly planning and the dials:** the copy rule for
"Kopier fra forrige uke"; where "Ferdig" sits in the calendar; default values
per bar; the period unit of each dial; how recurring days off and agreements
show in the calendar; the details of the request flow; whether the minimum is
per week or per month; whether an absence changes samfunnettjeneste duty
counting.

#### Known small items (2026-11-02)

- `repository.py` is 786 lines, near upstream's cap of about 800, so nothing
  more goes in it. `seed_demo.py` is about 611 lines.
- `list_rules_for`, `list_rules` and `list_template_weekdays` are unused in
  reads; remove them with the table drop.
- The log allow-list in `observability.py` drops unknown fields, so
  `shift.app.wishes` keeps only `count` and `volunteer_id`, and
  `shift.app.claim` loses `subject_*`.
- The month stepper also shows with a single month.
- The green and yellow dot colours are literals in `wishDotStyle.ts`; move them
  to `global.css` later.
- A batch is not split at 400 items.
- Week page: the chip's problem list says "to skift samme dag" while the place
  box says "2 skift samme dag" (the wording in `week_view.py` differs from the
  template). The banner names the group with an unpublished week regardless of
  the selected bar tab (same as the old board, left as is on purpose).
- On the demo, Grøndahls' current week is an explicit "open" row.

## Step 1 (API for the app): done

Contract and rules are described in `docs/reference/api-boundaries.md`
(section "Skiftlista API"). Smoke-tested on Postgres against the dev seed,
where Stjerne is switched on (also for samfunnettjeneste) with course dates.

Known gaps, deliberately left to later steps:

- **Publishing a week**: on the board (`/shifts`), each group has a status
  panel for the displayed week: "Åpne for ønsker" (disabled for the
  current and past weeks), "Publiser
  uka" and "Publiser måneden" (explicit published rows for every Monday of
  that calendar month; since 2026-11-02 evening publishing is per week and
  "Publiser måneden" is an optional shortcut). Shifts leaders put people on in an unpublished
  (future) week are drafts and do not show in the app's Mine until the week
  is published.
- **Unpublished-week warning**: done. A banner on the board appears when a
  week with shifts starts within 10 days and is not published ("Uke 49 er
  ikke publisert. Den blir åpen for alle mandag 30. november."), in warning
  colour from 3 days before, with a "Publiser" button. (Becomes a large,
  non-dismissable banner from 10 days before, with the consequence and the
  number of pending suggestions, shown to everyone whose scope covers the bar;
  replaced 2026-11-02, see 'Design pass results', item 13.) For a week with an
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
  that does not exist is not checked; `/shifts/settings` shows a warning on a
  group with course checks on when one of them cannot be found. `Skjenkekurs`
  is not a course in Skiftlista (see the decision above): the app must not
  show a course with that name. Register real brannkurs completions before the
  pilot, or members cannot claim in the app.
- **Swap and "kan ikke"** are stored and shown on the board, but the message
  is not shown to leaders yet, no email is sent, and messages are not
  deleted after the shift (step 8).
- **Review account** (App/Play Store): review sessions get `enabled: false`,
  so reviewers do not see Skiftlista. Decide whether the review user needs
  demo access before submitting.
- "Tar opplæring" is not considered yet (step 5d).

## Next steps, in order

1. ~~API for the app~~ (done, see above).
2. ~~App screens in kvarteret-internbevis~~ (done). They live in
   `src/features/shifts/` on the `kvarteret-internbevis` branch
   `feat/skiftlista-mobile` (feature-first MVVM-lite; zod is not used): tabs
   Ledige / Mine / Ønsker, detail with requirements, course sign-up + claim
   in one step, behind a server-side per-group switch. They use the
   generated client; `GET /config` decides tabs and labels.
3. **Group-scoped permissions**: done, except the samfunnettjeneste right
   (step 7d) and the maintenance role. Admin manages every group. Gruppeadmin manages
   the groups in their group-admin assignment (`group_admin_memberships`)
   and all subgroups (`groups.parent_group_id`, any depth); others are
   refused. `app/domain/shifts/permissions.py` (pure `manageable_groups`,
   `GroupScope`), `get_shift_scope` in `app/dependencies.py`, and every
   leader page and action takes `scope=` (required, no default). The plan and
   its group filter and volunteer pool only include permitted groups;
   assign, move (both shifts) and delete answer 403 with a Norwegian message;
   a Gruppeadmin with no groups sees an empty state. (Eligibility checks
   looked at the person's verv when this was built; since 5d step 2 they use
   qualifications.) The app API is unchanged.
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
   or templates, in tree order.
4c. **Privacy of names** (see decisions): the leader's default per group,
   each person's choice (hide / show to all of Skjenke), role-only display,
   and the server switch (per group and globally) that turns the hide option
   on or off. The app's switch and role-only display are JS and come
   together by OTA, so they do not need to be in the store release.
4d. **Edit one shift on the board** (time, staffing, note, opplæring mode).
   "Fjern skift" (only when nobody is placed; with a confirm) and "Steng
   skift" / "Åpne skift" are done. The per-group app switches and meeting
   point are done (step 4a). Dropped: editing a whole series, and the daily
   top-up job that would generate the next 12 weeks. Still to do:
   - "+" and "−" per role on each shift on the board ("−" only removes empty
     places). This is the quick part of 4d and gives "i tillegg" without the
     training flag. **Moved up (decided by Morten 2026-10-29):** built as
     "4d-plus" after 5d step 2 and before 5d step 3, not with the rest of 4d.
     Step 3 removes the opplaering place, and "i tillegg" then means adding a
     normal place, so leaders need the "+" first.
   - A unique index so two generator runs cannot both create the same shift.
     It uses the same key as the generator: (template, Oslo date of the
     start), which on Postgres is an expression index on
     `(template_id, (starts_at AT TIME ZONE 'Europe/Oslo')::date)`. Not built
     yet; the generator itself already skips on that key.
   - What happens to shifts that already exist when a closed period is added
     later: offer to remove the empty ones, warn about the ones with people,
     and never remove a shift that has people on it.
5a. ~~Show wishes to leaders on the board~~ (done). Each shift card on
   `/shifts` has a collapsed `<details>` line "Ønsker: 4 vil · 2 kan · 3 nei ·
   5 ikke satt" that lists the names under each answer. The answer per person
   and shift is `effective_answer` in `app_rules.py` (per-shift wish, else
   "ikke satt"; the weekday-rule step is removed in S1, replaced 2026-11-02,
   see 'Design pass results'); the app's `get_wishes` uses the same function. `ShiftsService._attach_wishes`
   reads the pool's wishes and rules with one query per table (rules are no
   longer read after S1) for the whole
   displayed week (`list_wishes_for`, `list_rules_for`). Only people in the
   leader's pool are counted or named; an empty scope reads nothing. No reasons
   exist, and the app API shows wishes only to the person themselves.
   Later: narrow the counts to people whose own groups include the shift's
   group (today it is the whole pool); see 5a2.
5b. **Who has answered** (demo feedback; built in S1 as the interim meaning
   "har begynt", per month; replaced 2026-11-02 evening: per week, "Ferdig",
   from S2): per bar and period, "x av y har
   svart" for leaders, with the list of who has not answered. Someone "har
   svart" for a bar in a month if they have at least one `shift_wishes` row on
   a shift of that bar in that month, any answer; a note alone does not count
   (replaced 2026-11-02, see 'Design pass results', item 2; the weekday-rule
   condition is gone). The reminder button comes with email
   (step 8). The package has "Har svart N av M" with "Vis hvem" and computes it
   from the answers; built first on today's board in S1. The reminder button
   comes with step 8 (to the leaders and the skjenkeleder, see item 13).
5c. **Per-bar requirements** (see decisions; replaced 2026-11-02 evening: the
   fixed "pensioner 1 a month" rule and the by-role `quota_for` in
   `app_rules.py` give way to adjustable dials, a pensioner quota being agreed
   between the leader and the volunteer; see "Dials and absences"): a per-bar
   settings table with
   rule types: weekend shifts per semester (weekend days per bar), max per
   week, pensioner 1 a month. Warnings only, never blocking. Pace warning to
   the leader halfway through the semester; closed periods never count. The
   app shows progress in Mine ("Helgeskift 1 av 4") and `suggest()` favours
   people who are behind. Option B (two halves per semester) later, per bar.
5d. **Qualifications, training and approval** (see decisions; design done
   in a read-only pass on 2026-10-29; steps 1 and 2 are done, the rest is not built). Do the training
   simplification in the same step.
   - **"Leader" in 5d** always means anyone who is Gruppeadmin of that bar
     (the leader, the personalansvarlig, or anyone an Admin assigns), plus the
     Skjenke Gruppeadmin and Admin, as in the rights decision. It is never
     derived from verv. This applies to approving, starting training,
     "Godkjenn direkte", flipping the sibling-skiftleder switch and placing
     people.
   - **Where verv and `opplaering` live** (corrected 2026-10-29; this
     describes the code before step 2, where eligibility read verv in
     `placement_problem` and `list_shift_members`; see step 2 below for what
     changed). Verv
     names `Skiftleder`, `Opplæring`, `Kokk` are constants in `planning.py`.
     `allowed_tokens` does not use the constants; it calls
     `placement_problem`, which does. Verv names are also read in
     `TRAINEE_ROLE` in `app_service.py` (drives `in_training`), the literals
     in `_role_label`, `PENSIONER_ROLE` in `app_rules.py` (`quota_for`; this
     stays on verv), and the seeds (`seed_shifts.py`, `seed_demo.py`). Verv are
     read by `ShiftsRepository.list_shift_members`, which is used five times,
     not two: the board pool eligibility in `_pool`, `_check`,
     `_attach_wishes` and `_require_in_pool` in `service.py`, and the pool
     itself. `ShiftsAppRepository.list_person_roles` is read by
     `_person_or_none` in `app_service.py`, and shown as `ShiftAppGroup.role` /
     `in_training` (`_role_label`). An app claim checks eligibility twice (the
     app evaluation, then `ShiftsService._check`). "Shift groups" in the code
     (`list_shift_groups`) means groups with templates, not participating
     groups. `opplaering` is plain `Text` in `shift_slots.kind` and
     `shift_template_slots.kind` (no enum). It is used in `planning.py`
     (shared room, tags, `blocked_by_training`, the "Tar en vanlig plass" /
     "I tillegg" tags and `_open_label`; `RESTRICTED_KINDS` also feeds
     `counts_as_samf` and the app config `restricted` flag), `service.py`
     (full message), `app_service.py` (`_training_done`, the Mine tag, and an
     `_evaluate` branch for `opplaering` with the training text and an "Ingen
     utlært" warning), `app_rules.py` (`SLOT_DESCRIPTIONS`,
     `TRAINING_SHIFTS_NEEDED`), `shift_plan.html` ("Brukt til opplæring",
     `training_places`, `blocked_by_training`), `shift_templates.html`
     (checkbox), seeds and tests. `generator.py`, `template_input.py`,
     `repository.py` and `models.py` carry `training_replaces_slot`, not the
     kind `opplaering`. The app reads `MyShift.kind ===
     "opplaering"` (`MyShiftsList.tsx`) and `groups[].in_training`,
     `training_done`, `training_needed` (`getTrainingProgress` in
     `shiftsView.ts`). It does not read `groups[].role` or `slot_kinds`.
   - **Data model.** One new table `volunteer_qualifications`: `volunteer_id`,
     `qualification` (`skjenker` / `skiftleder` / `kokk`), `status`
     (`training` / `approved`), `group_id` (the bar where they train or were
     approved; decides who manages the row, and where a skiftleder or kokk
     row is valid), `training_target` (copied from the bar's setting when
     training starts; null for "Godkjenn direkte" and migrated rows),
     `takes_trainees` ("tar opplæring", used for skiftleder and kokk; set by
     the copy script for copied rows, see "Migration"; chosen by the leader
     otherwise), `approved_by` (auth user, null for copied rows), `approved_at`, `created_at`; unique
     (volunteer, qualification, group), so a skiftleder in two bars has two
     rows. New columns: `shift_assignments.training_for` (null, or the
     qualification trained for on that shift; this is the per-assignment
     record, the number "2/3" is computed) and
     `shift_group_settings.training_shifts_needed` (default 3). One more new
     column on `shift_group_settings`: `sibling_skiftleder_allowed` (default
     false, see "Skiftledere from other bars"). Revoking
     deletes the row and writes to the `app.audit` log. Deleting a volunteer
     also deletes their qualification rows: add `volunteer_qualifications`
     to the Skiftlista cleanup loop in `VolunteersRepository.delete_volunteer`
     (`app/domain/volunteers/repository.py`), next to assignments, wishes,
     availability rules and course sign-ups, in the same transaction.
   - **Who manages.** By the row's `group_id` through `GroupScope`:
     anyone who is Gruppeadmin of the bar or an ancestor (leader,
     personalansvarlig or anyone an Admin assigns; Skjenkeleder is Gruppeadmin
     of Skjenke) and Admin. This covers approving, "Start opplæring",
     "Godkjenn direkte", the sibling-skiftleder switch and placing people. The samfunnettjeneste MANAGE / VIEW rights give no
     access to qualifications, and samfunnettjeneste volunteers need none:
     places open for samf stay open to all.
   - **Eligibility** (decided, see "Where qualifications are valid").
     `placement_problem` stays pure. The caller passes the person's member
     groups (any verv this semester, participating groups only), their
     `area` (decided 2026-10-29: those groups, plus their participating
     siblings, the same sibling rule as `pool_group_ids`, plus the
     participating direct children of those member groups, decided
     2026-10-29), and their qualification
     rows as
     (qualification, status, group). It returns the problem plus
     `training_for`. Needed: skiftleder for skiftleder, kokk for kokk,
     skjenker for skjenker and bartender, nothing for garderobe.
     `RESTRICTED_KINDS` becomes skiftleder and kokk. Age rules (Halvtimen
     20+) are checked as today, after this.
     ```
     need = NEEDS.get(kind); g = shift_group
     if need is None and g in area: return ok
     if need == "skjenker" and g in area and approved("skjenker"): return ok
     if need in ("skiftleder", "kokk") and g in area and approved(need, group=g): return ok
     if need == "skiftleder" and g in area and sibling_switch_on(g)
        and approved("skiftleder", group in siblings(g)): return ok
     if g in members and training(need, group=g): return ok, training_for=need
     if open_for_everyone and kind not in RESTRICTED_KINDS: return ok
     return "Kun for medlemmer av gruppa" if g not in area else "Kun for godkjente <need>"

     # Step 2 additions (decided 2026-10-29), on top of the lines above:
     # opplaering stays in RESTRICTED_KINDS until step 3. It needs g in members
     #   and a training row for g; it is not open for samf, and "kan ikke" does
     #   not open it to everyone. The "training(...)" line above applies only
     #   to the opplaering kind in step 2.
     # a person whose only row for need in g is a training row is refused crew
     #   places there with "I opplæring: bruk opplæringsplassen" (step 2 only;
     #   training_for is not written, step 3 stores it and lifts this refusal)
     # skjenker and bartender: g in area and an approved skjenker row whose own
     #   group is g or a participating sibling of g
     # garderobe: g in area
     # approved row and training row for the same qualification: approved wins
     ```
     The code uses the plural forms (skiftledere, skjenkere, kokker).
     In the pseudocode, "members" and "member groups" mean participating
     groups (a settings row or templates, plus ancestors, as in
     `permissions.py`), not only groups with templates, which is what
     `list_shift_groups` returns today.
     A skiftleder or kokk place uses `g in area`, not `g in members`, so
     "Godkjenn direkte" works for a skiftleder from a sibling bar who is not a
     member of the shift's group (the row is then for group `g`). The switch
     line is the only way a row for another bar counts, and only for
     skiftleder. Training is valid only in the row's own group, so counts and
     the "tar opplæring" check stay in one bar.
   - **Training.** No `opplaering` kind, no `training_replaces_slot`. A
     person with a `training` row placed on a place that needs that
     qualification gets `training_for` set automatically (board and app;
     recomputed on move). Done = their assignments with that `training_for`
     whose shift has ended, without "kan ikke". "Done" counts only
     assignments on shifts of the row's own group, until the cross-bar
     question is decided (decided 2026-10-29): counting across bars would
     quietly decide that open question. At `training_target` the
     leader sees "Klar for godkjenning" with "Godkjenn" (status approved,
     approved by and when, the "tar opplæring" choice for skiftleder and
     kokk, and `training_for` cleared on their future shifts) or "Ett skift
     til" (target plus one). Leaders start training with "Start opplæring" on
     a new page `/shifts/qualifications`, for one or several people at once.
     "Godkjenn direkte" creates an approved row without training shifts (bar
     experience, a chef who can stand in the bar, a skiftleder from another
     bar); it records who and when and writes to the `app.audit` log.
     Board: dashed outline and "opplæring 2/3" on the person, and a warning
     on a shift with a trainee
     and nobody else present with an approved skiftleder qualification (kokk
     in the kitchen) that tar opplæring. "I tillegg" means adding a place
     (the "+" built as 4d-plus before step 3).
   - **Migration** (three revisions, Postgres only, in this order).
     **A, additive (step 1):** create the table and columns
     (`volunteer_qualifications`, `shift_assignments.training_for`,
     `shift_group_settings.training_shifts_needed` and
     `sibling_skiftleder_allowed`); give both `training_replaces_slot` columns
     a server default so the new code can insert before B. **Migration A is
     schema only** (decided 2026-10-29; it no longer copies verv, because that
     copy would have written nothing on real data). **The verv copy** is
     `scripts/shifts/copy_verv_to_qualifications.py` (decided 2026-10-29),
     run deliberately after the bars and the kitchen have templates or
     settings rows. It is a dry run by default: it prints a summary, the
     unrecognised verv names with counts, and check lists with volunteer ids
     only. `--apply` writes in one transaction and needs `COPY_CONFIRM` equal
     to the host in `DATABASE_URL`; running it again adds nothing. A bar is a
     participating group with skjenker or bartender places in its templates;
     the kitchen has kokk places and no bar places; inactive groups are
     ignored. Rules, from all semesters:
     - skjenker approved for anyone who ever held a role in a bar with a verv
       other than Opplæring (or none), as one row on the bar of their most
       recent such semester, never on a parent group;
     - skiftleder approved per bar for anyone who ever held the Skiftleder
       verv there;
     - kokk approved in the kitchen for anyone who was ever a kitchen member
       with a verv other than Opplæring;
     - training rows only from an Opplæring verv in the current semester
       (kokk in the kitchen, skjenker in a bar), dropped when the person is
       already approved;
     - roles in groups that are not bars or the kitchen give nothing;
     - "tar opplæring" on skiftleder and kokk rows: on if the role was held in
       the current or previous semester, off for returning people; leaders
       adjust (this replaces "Migrated skiftledere and kokker get tar
       opplæring on").

     Check lists: training rows, returning people with skiftleder or kokk
     rows, people in several bars (current semester), and people whose only
     current role is in a non-bar ancestor and who got no row (they need
     "Godkjenn direkte"). The recognised verv names live in one constant. The
     kitchen has no skiftleder; chefs differ only by "tar opplæring" (willing
     to teach new chefs or not).
     **The opplaering move (step 3), its own revision** that runs together
     with the code that stops using `opplaering`: move each assignment in an
     `opplaering` slot to the shift's crew place (kokk in the kitchen; create
     a skjenker place if none) with `training_for` set, raising that place's
     count by the number moved where `training_replaces_slot` was off; add
     template `opplaering` counts to the crew slot only where the template had
     it off; delete all `opplaering` slots. **B (after step 3 is live):**
     drop both `training_replaces_slot` columns. Run on Neon through the direct
     endpoint, after taking a Neon branch as the rollback. Tests build SQLite
     from `tables.py` with `create_all`, never from migrations, so test the
     upgrades on Postgres e2e. The verv mapping now lives in the copy script,
     which has its own tests (the Postgres e2e tests for the script have not
     been run yet). The downgrade of the opplaering move revision (step 3) is
     schema only and lossy (no `opplaering` slots come back); downgrading A
     also drops the qualification rows.
   - **App API.** Shapes stay, values change: `slot_kinds` drops
     `opplaering`; `groups[].in_training`, `training_done`, `training_needed`
     come from the qualification rows; new optional `groups[].training_text`
     ("Opplæring 2 av 3", "Venter på godkjenning"); `MyShift.tag` says
     "Opplæring 2 av 3". The app change is JS only (show `training_text` and
     the server `tag` instead of checking `kind === "opplaering"`), so it can
     come by OTA, but it is small and should be in the store build. A dev
     build already installed keeps working and only loses the training tag.
   - **Privacy.** Leaders in scope see qualifications, training progress and
     "tar opplæring"; leaders whose pool includes sibling bars see the badges
     of those members. Volunteers see only their own progress; the crew list
     in the app stays first name and place. The skiftleder seeing who is in
     training in the app comes later, by OTA, and is not needed in the store
     build.
   - **Build steps** (each with its own tests; update seeds and tests where a
     step breaks them):
     1. **Done (2026-10-29).** Tables, migration A (schema only), repository
        reads, and the volunteer delete cleanup. Migration A first also copied
        verv; that copy moved to the separate script
        `scripts/shifts/copy_verv_to_qualifications.py` at `fe4acbc`.
        Built in `app/domain/shifts/tables.py` (`volunteer_qualifications`,
        `shift_assignments.training_for`, `training_shifts_needed`,
        `sibling_skiftleder_allowed`, server defaults on both
        `training_replaces_slot` columns), `app/db/tables.py`,
        `migrations/versions/20261101_1200_shift_qualifications.py`
        (schema only: the table, the columns and the server defaults),
        `app/domain/shifts/models.py` (`QualificationRecord`,
        `QualificationSettings`), `app/domain/shifts/repository.py`
        (`list_qualifications`, `list_group_qualifications`,
        `get_qualification_settings`) and
        `app/domain/volunteers/repository.py` (delete cleanup). Tests: the
        extended delete test and two repository tests in
        `tests/unit/domain/test_shift_app_service.py`, and the schema-only
        `tests/e2e/test_shift_qualifications_migration.py` (it checks that the
        upgrade creates the schema and copies nothing; it has not been re-run
        since the change). The old
        `tests/unit/test_shift_qualifications_migration.py` is deleted.
        The verv copy: the mapping is the pure function `map_verv` in
        `scripts/shifts/copy_verv_to_qualifications.py`, as are the recognised
        verv names (`RECOGNISED_VERV`: Skiftleder and Opplæring; Kokk is not
        listed, since kitchen membership gives kokk) and the printout
        (`format_report`: summary, unrecognised verv names with counts, and
        check lists with volunteer ids only, no names). Matching ignores case
        and surrounding spaces; every other verv name is printed as
        unrecognised. A training row gets the group's `training_shifts_needed`
        as target (3 when it has no settings row) and is dropped when the
        person is already approved for that qualification; approved rows get
        `approved_at` = run time and no `approved_by`. Tests:
        `tests/unit/test_copy_verv_to_qualifications.py` (pure mapping and
        refusal rules) and `tests/e2e/test_copy_verv_to_qualifications.py`
        (Postgres: dry run, apply, second apply adds nothing, apply refused
        without confirmation; not run yet). Step 2 now reads the new rows and
        columns for eligibility. The switch is read for eligibility since step 2.
        Still to do: the `/shifts/settings` control and `save_group_settings`,
        built with 5d step 4. Until then the switch can only be changed in the
        database.
     2. **Done (2026-10-29), Part 1 `e54cadb`, Part 2 `94d5107`.** Eligibility
        from qualifications in `planning.py`, `service.py`,
        `app_service.py` and `allowed_tokens`; `opplaering` still works but
        needs a training row. Tests: a case table for `placement_problem`
        (own group, participating sibling, Halvtimen with the 20+ rule,
        non-participating group, skiftleder in another bar refused, kokk only
        in its own group, training only in its own group, open for samf),
        board assign by a sibling bar's leader and app claim with and without
        the qualification.
        Step 2 clarifications (decided 2026-10-29):
        - Opplæring in step 2 only: `opplaering` stays in `RESTRICTED_KINDS`
          until step 3. An opplaering place needs membership of the shift's
          group and a training row for that group. It is not open to
          samfunnettjeneste, and a "kan ikke" does not open it to everyone.
        - Step 2 only: a person whose only row for that qualification in the
          shift's group is a training row is refused crew places there with
          today's message ("I opplæring: bruk opplæringsplassen").
          `training_for` is not written in step 2; step 3 stores it and lifts
          this refusal.
        - Skjenker and bartender places: valid when the shift's group is in
          the person's area and the person has an approved skjenker row whose
          own group is the shift's group or one of that group's participating
          siblings.
        - Garderobe: anyone with the shift's group in their area.
        - A person with both an approved row and a training row: the approved
          row wins, as the copy script assumes. Edge case, not built
          around: a skiftleder approved in a sibling bar who is also training
          as skiftleder in a bar with the switch on gets the approved path, so
          that training never counts.
        - Messages change from "Kun for skiftledere i gruppen" to "Kun for
          godkjente skiftledere", "Kun for godkjente skjenkere" and "Kun for
          godkjente kokker" (decided 2026-10-29); tests that match the old
          text must be updated.
        - Query cost: build the eligibility context once per request (area,
          qualification rows, the sibling switch). The board reads
          qualifications once per page. The app reads them once per request,
          never inside the per-slot evaluation (`/open` evaluates up to 92
          days). The app claim currently checks eligibility twice (the app
          evaluation, then `ShiftsService._check`); passing the built context
          into `_check` is optional.
        - Seeds are part of step 2: `seed_shifts.py` and `seed_demo.py` must
          write `volunteer_qualifications` rows for the fake people (including
          the demo logins listed in "Demo for a meeting"), because
          `bootstrap.py` runs migrations before seeding, so nothing is copied
          (migration A is now schema only). Without this every dev and demo
          person loses all restricted and bar places after step 2. Neither
          migration A nor the copy script is the source of demo rows.
        - Extra tests for step 2, in addition to the case table: bartender
          with an approved skjenker row; garderobe in and out of area; an
          opplaering place needs a training row; an open or "kan ikke"
          opplaering place is not open to samfunnettjeneste; a trainee-only
          person is refused crew places (step 2 rule); approved plus training
          precedence; a skiftleder row for the shift's group held by a member
          only of a sibling ("Godkjenn direkte" case); a sibling skiftleder
          row with the switch on and off; a kokk row in the kitchen held by a
          bar member; a former member with rows but no membership this
          semester; a member of a non-participating group only; a person
          registered only in the parent group (the area widening); a "kan
          ikke" opened skjenker place for a member without a row;
          `allowed_tokens` agrees with `placement_problem` across the matrix;
          the app evaluation and `ShiftsService._check` agree on the same
          inputs; query-count tests (one qualification read per board page,
          none per slot in the app's open list).
        - Tests that will break and must be updated:
          `tests/unit/domain/test_shift_planning.py` (signature and verv
          dicts), `tests/unit/domain/test_shift_service.py` (fake repository
          has verv members and no qualification methods; a test matches the
          old message), `tests/web/test_shifts_web.py` (fake
          `list_shift_members`), `tests/unit/domain/test_shift_app_service.py`
          (the test world seeds verv only), possibly
          `tests/unit/domain/test_shift_app_rules.py`, and
          `tests/unit/test_seed_demo.py` if the seeds change.

        Decided while building (2026-10-29):
        - An opplaering place accepts a training row for any qualification in
          the shift's group (until step 3).
        - A training row without membership of its group counts as no row.
        - A skiftleder trainee on a skiftleder place gets "Kun for godkjente
          skiftledere".
        - "Approved wins" over training applies only within the same group. A
          person approved in a sibling bar and training in the shift's group
          can still take the opplaering place. Accepted; step 3 replaces the
          opplaering place.
        - A skjenker trainee is still refused open or "kan ikke" places in
          their own group. Step 3 lifts this.
        - Parent-only members: in the app, a role row in a participating group
          counts as membership of its participating direct children (decided
          2026-10-29; first built as every group with templates at or below
          it); siblings are excluded, and a group above Skjenkegruppen covers
          nothing. Result: Skjenke audience, the bars below in
          their own tab, wishes, weekly rule, not counted as
          samfunnettjeneste, no barkurs. Accepted side effects: a Pensjonist
          or Opplæring verv on the parent applies to every bar below
          (Opplæring until 5d step 6).
        - Requirement lines in the app say "Godkjent skiftleder" and
          "Godkjent kokk", without a group name.
        - An unknown slot kind gives no problem inside the area (like
          garderobe). Documented in tests, not changed.
        - `ShiftsService` uses its own `now` for the semester, and
          `ShiftsAppService` passes `now` through.
        - Seeds: `seed_shifts.py` and `seed_demo.py` write qualification rows
          (Skiftleder verv: skiftleder plus skjenker approved; Kokk: kokk
          approved; Opplæring: a training row; other bar members: skjenker
          approved; skiftleder and kokk rows have `takes_trainees` on).
          `approved_by` is null; `approved_at` is the seed time on approved
          rows and null on training rows. `seed_demo.py` also writes rows for
          an existing database. Run on the demo database (Postgres) on 2026-10-29: it
        wrote 73 qualification rows.
     4d-plus (between step 2 and step 3; decided 2026-10-29, see 4d). "+" and
        "−" per role on each shift on the board. "−" only removes empty places.
        Scope-checked, CSRF, logged, with tests. Done before step 3 because
        "i tillegg" then means adding a normal place.
     3. Training simplification (depends on 4d-plus being done): remove `opplaering`, the shared room and
        `training_replaces_slot` from code, templates, generator and form; set
        `training_for` on assign, claim and move; dashed outline and counter
        on the board; the opplaering move (own revision, see Migration); migration B
        only after this is live. Tests: room, generator, template form,
        `training_for` on placement and move, and the opplaering move
        revision (its own migration, run together with this code).
     4. Count and approval: the per-bar setting, the `/shifts/settings` control
        for the sibling-skiftleder switch (with `save_group_settings`), the count,
        `/shifts/qualifications` (per group: Start opplæring for one or
        several people, Godkjenn with tar opplæring, Godkjenn direkte, Ett
        skift til, Fjern, tar opplæring switch; scope-checked, CSRF, audit
        log) and a "Klar for godkjenning" count on the board. Tests: count
        rules (future shift, "kan ikke", other qualification), approval
        clears future `training_for`, Godkjenn direkte records who and when
        and logs, bulk start skips people who already have a row, 403
        outside scope.
     5. "Tar opplæring" warning on the board and in the assign warnings.
     6. App API values and `training_text`, `openapi.json`; then the app
        (`MyShiftsList.tsx`, `shiftsView.ts`, regenerated client, tests).
   - `suggest()` is not ported yet; it takes the "tar opplæring" rule when it
     is (step 5 below).
5d2. **Hjelpsame** (design not decided). People who have been in a bar
   before do a single shift without being volunteers this semester. Today
   they get a paper "midlertidig internbevis" with tier 3 (Samfunnet) rights
   for about a week. They are not in the leader pool (Finding D) and not
   active members, so the 5d rules give them no place. Options:
   (a) the leader marks a place as filled by "Hjelpsam" with a first name
   only, no account linked; (b) time-limited access per group for a former
   member in Personal. Option (b) can reuse the existing person record:
   Personal keeps former members (`volunteer_records` and every past
   semester in `role_assignments` stay, since pingvinpoeng are summed over
   all of them, and nothing deletes old members automatically). Open
   question for Morten: do helpfuls need to see the shift in the app? If
   not, (a) is enough.
5a2. **Wish list per shift on the board** (planned after 5d step 2, not
   built). The leader page's people panel in the 2026-10-30 package replaces
   the sidebar list and its sticky fix (replaced 2026-10-30, see 'Wish
   calendar and leader page package'). Items below that the package does not
   mention are kept; **open: check against the 2026-10-30 package in the
   design pass:** the "Godkjenn direkte" link on greyed-out people, the parked
   `placement_problem` board warning, the open question about narrowing the
   list to the place's kind, "Legg til" as the keyboard alternative, and
   narrowing the count line to the shift's own group. The original text:
   Clicking a shift selects it, and the volunteer sidebar becomes that
   shift's list:
   - A search field at the top that searches the whole pool by name.
   - "Vis alle" in the sidebar returns to the full pool list.
   - Then the shift's own bar's people in groups Vil, Kan hvis nødvendig, Ikke
     satt, Kan ikke (the last folded).
   - Then a separate, folded "Andre barer" section with the same groups.
   - Each card: name, group, qualification, shifts this week, warnings, and a
     "Legg til" button that places the person in the first free place they
     can take. This also covers the keyboard alternative in step 9; drag
     still works.
   - People who cannot take any place are shown greyed out with the reason,
     with a "Godkjenn direkte" link when the viewer manages that bar (anyone
     who is Gruppeadmin of it, the Skjenke Gruppeadmin, or Admin).
   - The count line on each shift card counts only the shift's own group
     (replaces the "Later" note in 5a).
   - `suggest()` can later reorder this list.
   - Open question for the bar leaders: should clicking a free place narrow
     the list to that place's kind?
   - The volunteer list on the board sits in a short scroll box at the top and
     does not follow when scrolling down, so dragging to later days is
     awkward. Make the sidebar sticky (it follows at screen height) when 5a2
     rebuilds it.
   - **Parked:** board warning on placed people who no longer pass
     `placement_problem` (for example after a bar switch). Cheap to add using
     the pool data; parked until 5a2 (decided 2026-10-29). The existing "more
     than one shift in a week" warning only helps against double booking.
5e. **Wish calendar in the app**: now part of the wish calendar and leader
   page package, which comes first (see "Wish calendar and leader page package"
   under "Decisions already made"). The old idea of weekday rules limited to
   dagskift or kveldsskift is dropped, and weekday rules are retired
   altogether (replaced 2026-11-02, see 'Design pass results'). For reference: the existing wishes API
   has `PUT /wishes/weekdays/{weekday}` and `PUT /wishes/shifts/{shift_id}`,
   so a multi-date answer is one call per shift today; the week list only
   covers weeks that accept wishes (replaced 2026-10-30, see 'Wish calendar
   and leader page package').
5. **Wishes and weekly allocation**: the port of `suggest()` from the
   prototype's `logic.js` is replaced by the new suggestion function in the
   package (with the weekend, behind-on-requirements and "tar opplæring" rules
   from 5c and 5d). Week status and publishing (`shift_week_status`, open →
   published for leaders; `allocating` is legacy) move into the package
   (replaced 2026-10-30, see 'Wish calendar and leader page package'). Items
   the package does not mention, kept as they were; **Checked in the design
   pass (2026-11-02):** "Publiser måneden" stays (the pilot uses it) and the
   unpublished-week warning becomes the large banner (item 13).
   `fordelingWarnings()` from the prototype: open, not mentioned in the design
   pass decisions.
   Wishes are already stored by the app.
6. **Courses**: leader screens for `course_sessions` (date, place, capacity,
   cancel) and the sign-up lists. Sign-up and expiry are in the app API.
7. **Samfunnettjeneste** (design in the decisions above). Order: first 5d
   qualifications plus the training simplification, then 7a–7c (group types,
   exemptions, attribution plus no-show), then 7d–7e (rights and overview
   pages), then 7h and step 8 (email reminders). Not tied to the pilot.
   - 7a. Group type on groups (komité / BORG / DORG / other, pre-filled from
     `discount_tier` after checking real data: 1 BORG, 2 DORG, 3 komité, no
     pre-fill for other); duty settings tables: standard
     per type, override per group with reason, counting period and log.
   - 7b. "Medlemmene er fritatt" per group (on for Skjenke and subgroups) with
     reason and log; replace `is_skjenke_member` in `counts_as_samf`; exempt
     people are left out of every group's target.
   - 7c. `counted_for_group_id` on `shift_assignments` (auto, chosen in the app
     claim when in several groups, set by leader placement); multi-group
     options ("Ett skift holder" / "Velg gruppe" / "Hovedgruppe", max per
     person); no-show (`no_show_at`, `replaced_by_assignment_id`). The app
     claim change must go in the store release if it needs a new screen.
   - 7d. Samfunnettjeneste permission (MANAGE / VIEW), granted and removed by
     Admin only, logged.
   - 7e. Overview page for managers and viewers; separate rules and exceptions
     page; "Legg til arrangementsskift" for approved events; open places.
   - 7f. Group leader card ("Meld på medlem", "Når vi ikke kan"). Idea, a
     note only: the card could also show which members have a valid barkurs.
   - 7g. Editable shift descriptions (`/shifts/settings` card) and
     per-event-shift meeting point; end-of-semester report.
   - 7h. "Send påminnelse" (templates and cron route, step 8).
   - Crescat events as drafts for event shifts: depends on etjenesten's
     answers (see "Not code").
8. **Swaps and sick days** with messages to the right leaders, show
   `status_message` to them (not to skiftleder), delete it after the shift.
   Also the reminder button for volunteers who have not sent in wishes (5b),
   and the samfunnettjeneste reminders (7h): to the group's leader, and for
   per-member duties also to members who have not done a shift. The
   unpublished-week reminder goes to the bar leaders and to the skjenkeleder.
   Needs new message templates and a cron route. **Direction decided
   2026-11-02, nothing built:**
   - Slack is the main channel for leaders and volunteers (all volunteers are
     expected to be in Slack). A notification layer picks the channel per
     person, so email can be added later. This replaces "email for the pilot".
   - Slack is a nudge, not the only way to learn something: shifts and changes
     are always in the app. Messages link to the app (Mine tab).
   - Matching is by email between Personal and Slack. Leaders and the
     skjenkeleder see a list "N uten Slack-kobling" with names so they can
     follow up in person. A manual link (Slack user set by a leader or the
     person) is open and only built if many do not match.
   - Messages are short and factual (what, when, where, link). Volunteer
     names only in a person's own DM, never in a channel post. Quiet hours and
     a per-person opt-out are required.
   - **Open, from outside:** a Slack workspace admin to create or approve the
     app (post messages, look up users by email); the secret stored in the
     hosting environment by etjenesten; channel post versus DMs for leaders;
     an answer from Samfunnet's privacy person on sending name, date and shift
     through Slack with an opt-out.
9. Small: keyboard alternative to drag and drop (the "Legg til" button in
   5a2 covers it),
   semester-end data cleanup. (The per-person "tar ikke opplæring" is gone;
   see 5d.)

## Universal design: found outside Skiftlista (do nothing, list for Kvarteret)

Rule: findings in shared Kvarteret code that Skiftlista does not own are listed
here and not changed. This is an accessibility note, not a security finding.
- The global `.app-button` (red background, cream text) has 3.21 to 1
  contrast, which fails WCAG AA for normal-size text. The new week page has a
  scoped fix (`.week-page .app-button`, ink text, 5.7 to 1); the global style
  is unchanged, so the old board and other pages still have it.
- Flash notices (`flash_notice` in `ui_macros.html`) use `red-700`, `emerald-700`
  and `stone-700` text on 50-level fills; not measured.
- The app-wide `select.app-input` and `.group-combobox-input` remove the
  outline on focus and rely on a border colour change; not measured.
- The shared base layer forces `border-radius: 0 !important` on everything, so
  round shapes need a gradient and `clip-path` workaround (a general styling
  note, not an accessibility finding). In Windows forced-colours mode the
  answer dots may not show; the word beside them always does.
- Light cell dividers (stone-300, 1.49 to 1) on the week grid are decorative
  and were kept; the headers carry the table structure.

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
- **Finding E (group-scope authorization gaps in Personal's group and
  volunteer routes):** known to etjenesten and being fixed (per Morten,
  2026-11-02). Short summary until the fix is released: some management
  actions on membership, groups and verv check the Admin or Gruppeadmin role
  but not which group the actor administers. Qualifications and the pool
  depend on active membership, so this must be fixed or explicitly accepted
  before the pilot. It is Personal-wide and belongs with etjenesten, not this
  fork. The path-by-path list is not kept here (this fork is public); the
  checklist for the longer message to etjenesten is under "Notes for
  etjenesten (not sent)". See also the bar switch notes under "Not code".
- **Findings A and C stay as reported** (see above).
- **Finding B:** `?notice=` and `?error=` text in URLs. Low priority.
- Mobile login session handling was not reviewed.

### Migration A and step 2 review (2026-10-29)

A read-only Opus review of migration A and step 2. Results:

- **B1:** the copy in migration A would write nothing on real data. Fixed by
  the separate script.
- **B2:** groups above Skjenkegruppen became Skjenke members. Fixed: direct
  children only.
- **M1:** a skjenker row could land on the parent. Fixed: always on a bar.
- **M2:** parent-only members got no row. Now they get rows from their bar
  history; anyone without bar history is in the check list for "Godkjenn
  direkte" (5d step 4).
- **M3:** kitchen members without Kokk. Resolved: kitchen membership gives
  kokk, never skjenker.
- **H1:** merging skiftplan into demo conflicts in `scripts/dev/seed_demo.py`
  and `tests/unit/test_seed_demo.py`. Keep both imports, both summary lines
  and both keys in the `again == {...}` dict.
- **L1 (low):** inactive groups are ignored, but inactive templates still
  count as evidence of a bar or kitchen.
- **L2:** the old migration copy tests are replaced; the Postgres e2e tests
  for the script have not been run yet.
- **L3 (low):** wishes from parent-only members are not visible to bar
  leaders, only to the Skjenke Gruppeadmin and Admin.

## Pre-existing issues, not step 2

Found while reviewing 2026-10-29; not caused by 5d and not part of step 2.

- The "Åpen for samf." tag shows on skiftleder and kokk places that have the
  samf box ticked, although such places are not open.
- `_training_done` in `app_service.py` does not exclude "kan ikke" and only
  covers the current semester.
- The board drag check (`allowed_tokens`) ignores the Halvtimen age rule, so
  the server refuses after the drop.
- Low: the board pool reads every member of every participating group per
  page to build areas. It is one query, but its size grows with the tree; it
  could be limited to the shown people.
- Low: the `allowed_tokens` agreement test is close to circular, because
  `allowed_tokens` calls `placement_problem`.
- Low: the copy script lists ordinary verv names (Kokk, Skjenker) as
  "unrecognised", meaning no special rule. A group with only a settings row
  and no templates gives no rows.

## Not code, but on the critical path

- How OTA updates are published (who publishes, which channel and runtime
  version the store build uses, how a bad update is rolled back). The hide
  switch and the role-only display (4c) now come by OTA, so settle this
  before the store build.
- Review by etjenesten (show the prototype and this branch).
- Real data in Personal: is Stjerne etc. already a subgroup of
  Skjenkegruppen? Skiftleder, Kokk and Opplæring verv set per person for this
  and the previous semester, so the 5d copy script can read them? After the
  script has run, leaders check its lists and adjust "tar opplæring" where
  needed.
  Templates.
- Test with a few Stjerne people, then App Store / Play review.
- Ask etjenesten to check that Skiftlista fits the current plan: email volume
  for reminders, "kan ikke" and swaps; app polling and the `/open` endpoint
  (it evaluates 92 days per call); Vercel plan limits. Expected to be small.
  Samfunnet pays for hosting.
- More questions for etjenesten: the distinct verv names
  (`assignment_roles.name`) used in production, names only, so the 5d
  copy script maps them correctly (no migration or seed in this repo holds the
  real list); and the Slack questions for step 8 (the direction is Slack first, decided
  2026-11-02; see "Notes for etjenesten").
- For Samfunnet's privacy person: is it fine to keep qualifications
  (skjenker, skiftleder, kokk, who approved, when) for people who are no
  longer active? Verv history is already kept for every semester because
  pingvinpoeng are summed over all of it, and qualifications currently last
  forever, with no automatic deletion; only deleting the volunteer removes
  them. The copy script now reads all semesters, so it creates rows for many
  former members. Also for the privacy person (added 2026-11-02): (a) sending
  a volunteer's name, date and shift through Slack, with a per-person opt-out
  (step 8); (b) the personal calendar feed: Google and Microsoft fetch the
  feed, so they see shift titles, times and venues (item 14); the feed holds
  only that person's published shifts and no other names; (c) absences
  (added 2026-11-02 evening, see "Dials and absences"): the kinds are neutral
  (Ferie, Praksis, Annet), no health reasons are stored, free-text notes are a
  risk, and whether an absence changes samfunnettjeneste duty counting is open;
  (d) longer history of a person's answers (earlier semesters), which is parked
  until the semester-end data cleanup and this answer.
- Questions for etjenesten about Crescat: is there a read API for events
  (title, room, start/end, status)? How is access authenticated and who issues
  a read-only key? Rate limits? Can we match on `booking_request_id`? Goal:
  events from Crescat as drafts for samfunnettjeneste event shifts.
- Open question, not decided, nothing to build: can a barkurs holder be
  skiftleder in the smaller bars (the event bars are not planned in
  Skiftlista, see the samfunnettjeneste decision)? Barkurs expires (4
  semesters), a qualification does not, so keep them separate.
- **Bar switch** (decided 2026-10-29): nothing is built for people who switch
  bars. Since 5d step 2 an old skiftleder row keeps counting in the bar the
  person left (it is a sibling, so in the area) and does not count in the new
  bar unless the new bar's switch is on or a leader uses "Godkjenn direkte".
  The system cannot tell "moved away" from "approved directly as a visitor".
  Practice: when someone switches bars, the leader checks
  `/shifts/qualifications` and removes the old skiftleder row. Revisit after
  the pilot. Skjenker rows keep working in all sibling bars; kokk stays in
  the kitchen; an old training row becomes inert (training is valid only in
  the row's own group). Known behaviour: nothing re-checks future shift
  assignments after a membership change, and the board gives no warning (see
  the parked warning under 5a2); weekday rules carry over to the new bar (they
  have no group; moot after S1, since rules are no longer read); old per-shift wishes stay visible to the old bar's leaders
  while the person can no longer see or change them in the app. Still open:
  the cross-bar skjenker training count (Morten's leaning, see "Training and
  approval") and the question below.
- **Board placement of the Skjenkeleder** (decided 2026-10-29, nothing to
  build): under the area widening, someone registered only in Skjenkegruppen
  (for example the Skjenkeleder) can take bar places themselves in the app. A
  bar leader's pool is their own bar plus its participating sibling bars, not
  the parent group. Verified in code: a bar's Gruppeadmin cannot place a
  parent-only member (outside the pool); the Skjenke Gruppeadmin and Admin
  can. Accepted. Such a person claims places in the app, where they now get
  the Skjenke view.
- **Board members and places they have no qualification for** (open, not
  decided, nothing to build (2026-10-29)): idea from Morten: should board
  members be able to claim places they have no qualification for? Notes: the
  widening already gives parent-group members the bars in their area, and they
  still need an approved skjenker row, which "Godkjenn direkte" can grant.
  Skiftleder and kokk places need a qualification on purpose. Places open for
  samfunnettjeneste are already open to board members. A rule like "board
  members may claim anything" would bypass the qualification rule and would
  need a way to know who counts as board; verv is free text and rights are
  never derived from verv, so a named permission would fit better but is new
  building. Suggested: check first whether "Godkjenn direkte" covers it in
  practice, and decide after the pilot.
- Open question: who edits group membership when someone switches bar, and
  does removing a membership end the verv too? Morten's guess: someone with
  the admin right removes the old group and adds the new one. Not confirmed
  for the real Personal. Read-only check of this repo (2026-10-29): a
  membership is a `role_assignments` row (volunteer, group, verv, semester),
  so membership and verv are one row and removing it ends both for that
  semester. Editing membership is covered by Finding E (known to
  etjenesten and being fixed; see "Security review: open items"). Removing a row does not touch `shift_assignments`,
  so the person stays on future shifts (and, once 5d is built, a training
  row stays but is inert). Ask etjenesten or Morten whether that is the
  intended practice at Samfunnet.
- Stjerne has no weekend shifts, so check that templates and the generator
  match that when the first templates are entered. Weekend days are a per-bar
  setting (Grøndahls: Friday and Saturday).

## Notes for etjenesten (not sent)

Not sent. Morten will send a longer message later. Keep this list current.
This fork is public: nothing about an unreported security finding goes here; it
is kept in `~/kvarteret-private-notes.md`, outside the repo.

Security:

- Finding E (group-scope authorization gaps) is known to etjenesten and being
  fixed (per Morten, 2026-11-02). Until the fix is released this file keeps
  only a short summary. Checklist for the longer message, to confirm each is
  covered by the fix:
  - role assignments on the group page and on the volunteer page;
  - group history delete;
  - semester transfer;
  - volunteer delete;
  - re-parenting or deactivating groups;
  - creating, renaming and deleting verv;
  - application approval, which checks only the applicant's own group
    choices, not the approver's group.
- Why it matters for Skiftlista: qualifications and the leader pool depend on
  active membership, so this must be fixed or accepted before the pilot.
- Findings A (CSRF cookie not tied to the session) and C (CSP allows
  `'unsafe-inline'`) stay as reported.

Questions:

- OR-Tools (`ortools`) for the weekly suggestion function (Google CP-SAT), and
  whether it fits the hosting (Vercel function size limits): **later, not
  blocking (plan B, plain Python, is chosen).**
- Slack (step 8, direction decided 2026-11-02): who can create or approve a
  Slack app in the workspace (post messages, look up users by email); the app's
  secret stored in the hosting environment by etjenesten; channel post or DMs
  for leaders.
- Before any upstream PR: talk about an ADR or issue first, since the
  samfunnettjeneste plan touches groups and rights; and the docs split and
  commit series described under "Conformance pass results".
- Other questions for etjenesten are listed under "Not code, but on the
  critical path" (verv names, Slack, Crescat, OTA, hosting).

For Samfunnet's privacy person (the full list is under "Not code, but on the
critical path"): qualifications kept for former members; sending name, date and
shift through Slack with an opt-out; the personal calendar feed (Google and
Microsoft see shift titles, times and venues); absences and their free-text
notes (neutral kinds, no health reasons); longer answer history.
