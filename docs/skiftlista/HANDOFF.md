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
| Test data | `scripts/dev/seed_shifts.py` (run by `scripts/dev/bootstrap.py`; skipped when a real snapshot is used) |
| Tests | `tests/unit/domain/test_shift_planning.py`, `tests/unit/domain/test_shift_service.py`, `tests/web/test_shifts_web.py`, `tests/unit/shared/test_semester.py` |
| App API (step 1) | `app/api/v1/mobile_shifts.py` (mounted at `/api/v1/mobile-card/shifts`), `app/domain/shifts/app_service.py`, `app_repository.py`, `app_rules.py`, `app_models.py`, migration `20261028_1200_shift_app_api.py` |
| App API tests | `tests/unit/domain/test_shift_app_service.py` (real SQLite DB), `tests/unit/domain/test_shift_app_rules.py`, `tests/api/mobile_card/test_mobile_shifts_api.py` |
| App client | `kvarteret-internbevis` branch `skiftlista/api-client`: `npm run api:generate:personal` writes `src/core/api/kvarteret-personal` (only the `mobile-shifts` tag) |

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
  `samf_enabled`, `meeting_point`), `shift_week_status` (per group and
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
  allocation). Explicit rows (`open`, `allocating`, `published`) always win.
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
- Ownership is role based (Skjenkeleder, HS leder/driftsansvarlig); role
  admins can grant access; a maintenance role for etjenesten.
- Notifications: email for the pilot. Push to be built together with
  etjenesten's planned shared notifications.

## Step 1 (API for the app): done

Contract and rules are described in `docs/reference/api-boundaries.md`
(section "Skiftlista API"). Smoke-tested on Postgres against the dev seed,
where Stjerne is switched on (also for samfunnettjeneste) with course dates.

Known gaps, deliberately left to later steps:

- **Publishing a week** has no screen yet (step 5). Until then, SQL, where
  `week_start` must be a Monday (one row per week; this publishes November
  2026 for one group):
  `INSERT INTO shift_week_status (group_id, week_start, status)
  SELECT <group id>, d::date, 'published'
  FROM generate_series('2026-11-02'::date, '2026-11-30'::date, interval '7 days') d
  ON CONFLICT (group_id, week_start) DO UPDATE SET status = 'published', updated_at = now();`
  Shifts leaders put people on in an unpublished (future) week are drafts and
  do not show in the app's Mine until the week is published.
- **Warn leaders about unpublished weeks:** an unpublished week becomes
  published by default on its Monday, whether or not it is allocated. From
  about 10 days before, the leader board should show "Uke 49 er ikke
  publisert. Den blir åpen for alle mandag 30. november." with a "Publiser"
  button and a "Publiser måneden" button (publishes all its weeks). Email
  reminders come with step 8.
- **Pilot step when switching a group on:** mark the weeks already planned in
  the old sheet as published (SQL above), or their shifts stay hidden from
  Ledige and Mine and open for wishes.
- **Switching on** a group, setting a week to `allocating` and adding
  course dates has no screen in Personal yet (steps 4–6). Until then, SQL:
  `INSERT INTO shift_group_settings (group_id, app_enabled, samf_enabled)
  VALUES (<stjerne id>, true, false);`
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
2. **App screens in kvarteret-internbevis** (`src/features/shifts/`,
   feature-first MVVM-lite, zod schemas): tabs Ledige / Mine / Ønsker,
   detail with requirements, course sign-up + claim in one step, behind a
   server-side per-group switch. Copy the flows from the prototype. Use the
   generated client (branch `skiftlista/api-client`); `GET /config` decides
   tabs and labels.
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
4. **Templates and series editing** in Personal: create/edit templates,
   generate a semester, edit one shift or the whole weekday series (time,
   staffing, note, closed, opplæring mode). Also the per-group app switches
   and meeting point (`shift_group_settings`). The semester generator needs
   closed periods (from date, to date, per group) so holidays get no shifts.
5. **Wishes and weekly allocation**: port `suggest()` and
   `fordelingWarnings()` from the prototype's `logic.js`; set
   `shift_week_status` (open → allocating → published) per week, with a
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

## Not code, but on the critical path

- Review by etjenesten (show the prototype and this branch).
- Real data in Personal: is Stjerne etc. already a subgroup of
  Skjenkegruppen? Verv Skiftleder/Opplæring/Kokk set per person? Templates.
- Test with a few Stjerne people, then App Store / Play review.
