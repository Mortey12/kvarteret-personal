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
- Who may take what **today** comes from existing Personal data: membership and
  verv (`role_assignments` + `assignment_roles`) this semester. Verv names
  used: `Skiftleder`, `Opplæring`, `Kokk`. **This changes before the pilot:**
  qualifications decide who may take which places, not verv (see the
  "Qualifications" decision and step 5d; `volunteer_qualifications` exists
  since 5d step 1 but nothing reads it yet). Subgroups use
  `groups.parent_group_id`. Course requirements per group already exist in
  `group_course_requirements`, but that table is display-only (courses
  module) and Skiftlista does not use it: the course checks use `COURSE_RULES`
  in `app_rules.py` and the per-group `courses_enabled` switch.
  Course rights are global on purpose, since brann- and barkurs are for all of
  Kvarteret: any Admin or Gruppeadmin can manage courses, sessions and
  completions for everyone, not only for their own groups.

### Rules implemented (server is the source of truth)

- **Current code, to be replaced** (see "Qualifications"): Skiftleder place:
  verv Skiftleder in the shift's group. Kokk: verv Kokk.
  Opplæring place: verv Opplæring in the group. Trainees may not take crew
  places in their own group. Crew places (`placement_problem` in
  `planning.py`): a person with any verv other than Opplæring in a group that
  has shifts, or a member without verv in such a group, or anyone if the place
  is open for samfunnettjeneste. Otherwise refused with "Kun for medlemmer av
  gruppa". It does not look at the Skjenke group.
- Opplæring **takes a regular crew place** by default
  (`training_replaces_slot`); per shift it can be "i tillegg". (To be removed:
  see "Training simplification".)
- Closed shift, same person twice on a shift, full place: refused.
- More than one shift in a week: allowed with a warning.
- Halvtimen: under 20 is refused, also on the leader board (unknown birth
  date: a warning for leaders, refused in the app).
- "Kan ikke i dag": the person stays on, marked on the board; the place
  counts as open and, unless it needs a qualification (today: a verv), is open
  to everyone. Whoever
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
email delivery (Resend) or a demo-login flag, which does not exist yet.
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

Leader side: log in to Personal as the dev admin and open `/shifts`. Halvtimen
is switched on for the demo (it is not in the request list, but the "20+" block
only shows when Halvtimen is visible in the app).

## Decisions already made (do not re-ask)

- Quota and requirements (from the demo meeting with the bar leaders, incl.
  Grøndahls) replace the old "1 shift a week" idea as the general model.
  Requirements are **per bar and warnings only, never blocking**. Max per
  week and the pensioner rule "1 a month" (verv `Pensjonist`) become rule
  types in the same per-bar settings. Pensjonist stays a verv, not a
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
- Skiftleder training: the trainee is the skiftleder on the shift, with an
  experienced skiftleder who "tar opplæring" present. The kitchen has no
  skiftledere; a new chef works with a kokk who "tar opplæring".
- **Qualifications, not verv, decide who may take which places** (decided,
  **not built yet**). Each person has qualifications (`skjenker`,
  `skiftleder`, `kokk`), each with "tar opplæring" yes/no, who approved it
  and when. For now they last forever. A place is open to someone who has the
  needed qualification valid for the shift's group (see "Where
  qualifications are valid"), whatever their verv (Skjenker, Styremedlem,
  Skjenkeleder, etc.); board members usually take shifts. Verv only says what
  job someone has this semester. Approval in 5d creates the qualification.
  Being in training is a training row a leader starts (not the Opplæring
  verv, which the migration reads once). The rule "Skiftleder place: verv
  Skiftleder" in `planning.py` must change to this, and existing Skiftleder,
  Kokk and Opplæring verv (this and the previous semester) need a one-time
  migration into qualifications **before the pilot**.
  - **Where qualifications are valid** (decided 2026-10-29; replaces "active
    member of the shift's group" for crew places). The person must be an
    active member this semester of a participating group. A skjenker
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
  - **Migrated skiftledere and kokker** get "tar opplæring" on (decided);
    leaders untick the exceptions.
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
  weekday letter (M–S) to select all those days, picks which shifts the
  answer applies to (all, dagskift, kveldsskift), then vil / kan hvis
  nødvendig / kan ikke. Weekday taps stay standing rules for the semester, as
  today, with a line saying so. The three answers stay. App change only (JS,
  can ship by OTA) on top of the existing wishes API (step 5e).
- Halvtimen requires age 20+ (show only "20+" in the app, never birth date).
- Kjøkken is part of Skjenke; kitchen places are for chefs only; kitchen
  shifts are shown in the Stjernesalen card ("Stjernesalen og kjøkken").
- Becoming skiftleder: skiftleder training shifts (default three, per bar),
  not a course. See the training and approval decision below.
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
  board; editing a whole series is dropped), then 5 onward.
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
     training flag.
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
   and shift is `effective_answer` in `app_rules.py` (per-shift wish, else the
   weekday rule of the shift's semester and Oslo weekday, else "ikke satt");
   the app's `get_wishes` uses the same function. `ShiftsService._attach_wishes`
   reads the pool's wishes and rules with one query per table for the whole
   displayed week (`list_wishes_for`, `list_rules_for`). Only people in the
   leader's pool are counted or named; an empty scope reads nothing. No reasons
   exist, and the app API shows wishes only to the person themselves.
   Later: narrow the counts to people whose own groups include the shift's
   group (today it is the whole pool); see 5a2.
5b. **Who has answered** (demo feedback): per bar and period, "x av y har
   svart" for leaders, with the list of who has not answered. Someone "har
   svart" for a period if they have weekday rules for the semester or at least
   one answer on a shift in that period. The reminder button comes with email
   (step 8).
5c. **Per-bar requirements** (see decisions): a per-bar settings table with
   rule types: weekend shifts per semester (weekend days per bar), max per
   week, pensioner 1 a month. Warnings only, never blocking. Pace warning to
   the leader halfway through the semester; closed periods never count. The
   app shows progress in Mine ("Helgeskift 1 av 4") and `suggest()` favours
   people who are behind. Option B (two halves per semester) later, per bar.
5d. **Qualifications, training and approval** (see decisions; design done
   in a read-only pass on 2026-10-29, not built). Do the training
   simplification in the same step.
   - **"Leader" in 5d** always means anyone who is Gruppeadmin of that bar
     (the leader, the personalansvarlig, or anyone an Admin assigns), plus the
     Skjenke Gruppeadmin and Admin, as in the rights decision. It is never
     derived from verv. This applies to approving, starting training,
     "Godkjenn direkte", flipping the sibling-skiftleder switch and placing
     people.
   - **Where verv and `opplaering` live today.** Verv names `Skiftleder`,
     `Opplæring`, `Kokk` are constants in `planning.py` (`placement_problem`,
     `allowed_tokens`). Verv are read by `ShiftsRepository.list_shift_members`
     (board pool and `_check` in `service.py`) and
     `ShiftsAppRepository.list_person_roles` (`_person_or_none` in
     `app_service.py`), and shown as `ShiftAppGroup.role` / `in_training`
     (`_role_label`). `opplaering` is plain `Text` in `shift_slots.kind` and
     `shift_template_slots.kind` (no enum). It is used in `planning.py`
     (shared room, tags, `blocked_by_training`), `service.py` (full message),
     `app_service.py` (`_training_done`, training text, Mine tag),
     `app_rules.py` (`SLOT_DESCRIPTIONS`, `TRAINING_SHIFTS_NEEDED`),
     `shift_plan.html` ("Brukt til opplæring"), `shift_templates.html`
     (checkbox), `generator.py`, `template_input.py`, `repository.py`,
     `models.py`, seeds and tests. The app reads `MyShift.kind ===
     "opplaering"` (`MyShiftsList.tsx`) and `groups[].in_training`,
     `training_done`, `training_needed` (`getTrainingProgress` in
     `shiftsView.ts`). It does not read `groups[].role` or `slot_kinds`.
   - **Data model.** One new table `volunteer_qualifications`: `volunteer_id`,
     `qualification` (`skjenker` / `skiftleder` / `kokk`), `status`
     (`training` / `approved`), `group_id` (the bar where they train or were
     approved; decides who manages the row, and where a skiftleder or kokk
     row is valid), `training_target` (copied from the bar's setting when
     training starts; null for "Godkjenn direkte" and migrated rows),
     `takes_trainees` ("tar opplæring", used for skiftleder and kokk; on for
     migrated rows, chosen by the leader otherwise), `approved_by` (auth
     user, null for migrated rows), `approved_at`, `created_at`; unique
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
     `area` (those groups plus their participating siblings, the same
     sibling rule as `pool_group_ids`), and their qualification rows as
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
     ```
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
     whose shift has ended, without "kan ikke". At `training_target` the
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
     (the 4d "+").
   - **Migration** (three revisions, Postgres only, in this order).
     **A, additive (step 1):** create the table and columns
     (`volunteer_qualifications`, `shift_assignments.training_for`,
     `shift_group_settings.training_shifts_needed` and
     `sibling_skiftleder_allowed`); give both `training_replaces_slot` columns
     a server default so the new code can insert before B; copy verv from the
     current and previous semester only (decided; returning people from older
     semesters get "Godkjenn direkte" from a leader) in groups with templates
     or a settings row:
     Skiftleder to skiftleder (in that group) and skjenker approved, Kokk to
     kokk approved (in that group), both with "tar opplæring" on so leaders
     untick the exceptions; Opplæring to a training row in that group (kokk
     in a group whose templates have kokk and no bar places, else skjenker);
     any other verv or none to skjenker approved. A skjenker row is kept once
     per person (first group wins); skiftleder and kokk rows are kept per
     group. The verv names the migration reads (Skiftleder, Kokk, Opplæring)
     live in one constant, and the migration prints every verv name it did
     not recognise, so a run on real data shows misses instead of silently
     giving people no qualification. A prints a check list (every Opplæring
     person, Kjøkken members without Kokk, people in several groups).
     **The opplaering move (step 3), its own revision** that runs together
     with the code that stops using `opplaering`: move each assignment in an
     `opplaering` slot to the shift's crew place (kokk in the kitchen; create
     a skjenker place if none) with `training_for` set, raising that place's
     count by the number moved where `training_replaces_slot` was off; add
     template `opplaering` counts to the crew slot only where the template had
     it off; delete all `opplaering` slots. **B (after step 3 is live):**
     drop both `training_replaces_slot` columns. Run on Neon through the direct
     endpoint, after taking a Neon branch as the rollback. Tests build SQLite
     from `tables.py` with `create_all`, never from migrations, so keep the
     verv mapping a small pure function in the migration file with a unit
     test, and test the upgrade on Postgres e2e. Downgrade is schema only and
     lossy (no `opplaering` slots come back).
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
     1. **Done (2026-10-29).** Tables, migration A (additive changes and the
        verv copy only), repository reads, and the volunteer delete cleanup.
        Built in `app/domain/shifts/tables.py` (`volunteer_qualifications`,
        `shift_assignments.training_for`, `training_shifts_needed`,
        `sibling_skiftleder_allowed`, server defaults on both
        `training_replaces_slot` columns), `app/db/tables.py`,
        `migrations/versions/20261101_1200_shift_qualifications.py`
        (`map_verv`, `RECOGNISED_VERV`, the printout),
        `app/domain/shifts/models.py` (`QualificationRecord`,
        `QualificationSettings`), `app/domain/shifts/repository.py`
        (`list_qualifications`, `list_group_qualifications`,
        `get_qualification_settings`) and
        `app/domain/volunteers/repository.py` (delete cleanup). Tests:
        `tests/unit/test_shift_qualifications_migration.py`, the extended
        delete test and two repository tests in
        `tests/unit/domain/test_shift_app_service.py`, and
        `tests/e2e/test_shift_qualifications_migration.py` (written, not yet
        run). Decided while building: migration matching ignores case and
        surrounding spaces; every verv name that is not Skiftleder, Kokk or
        Opplæring is printed as unrecognised with a count (so ordinary names
        like Skjenker show up too); a training row gets target 3 (the column
        default); a training row is dropped when the person is already
        approved for that qualification (skjenker: anywhere, kokk: in that
        group); approved rows get `approved_at` = migration time and no
        `approved_by`; the check list prints volunteer ids only, no names;
        nothing reads the new rows or columns yet, so eligibility is still
        from verv. Still to do for the switch: the `/shifts/settings` control
        and `save_group_settings` (step 2 and 4). Original plan: Tests: the mapping function (verv older than the previous
        semester is ignored), Postgres e2e upgrade on a seeded database, and
        extend `test_deleting_a_volunteer_removes_their_shift_rows`
        (`tests/unit/domain/test_shift_app_service.py`) so it also checks
        that the person's qualification rows are gone.
     2. Eligibility from qualifications in `planning.py`, `service.py`,
        `app_service.py` and `allowed_tokens`; `opplaering` still works but
        needs a training row. Tests: a case table for `placement_problem`
        (own group, participating sibling, Halvtimen with the 20+ rule,
        non-participating group, skiftleder in another bar refused, kokk only
        in its own group, training only in its own group, open for samf),
        board assign by a sibling bar's leader and app claim with and without
        the qualification.
     3. Training simplification: remove `opplaering`, the shared room and
        `training_replaces_slot` from code, templates, generator and form; set
        `training_for` on assign, claim and move; dashed outline and counter
        on the board; the opplaering move (own revision, see Migration); migration B
        only after this is live. Tests: room, generator, template form,
        `training_for` on placement and move, and the opplaering move
        revision (its own migration, run together with this code).
     4. Count and approval: the per-bar setting, the count,
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
   built). Clicking a shift selects it, and the volunteer sidebar becomes that
   shift's list:
   - A search field at the top that searches the whole pool by name.
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
5e. **Wish calendar in the app** (JS only, can ship by OTA): month calendar
   with one coloured dot per shift, select dates or weekday letters, pick all
   / dagskift / kveldsskift, then vil / kan hvis nødvendig / kan ikke. Check
   the existing wishes API first. It has `PUT /wishes/weekdays/{weekday}` and
   `PUT /wishes/shifts/{shift_id}`, so a multi-date answer is one call per
   shift. Likely gaps: a batch endpoint, and weekday rules limited to dagskift
   or kveldsskift (a rule today covers the whole weekday). Decide when
   building; the week list only covers weeks that accept wishes.
5. **Wishes and weekly allocation**: port `suggest()` and
   `fordelingWarnings()` from the prototype's `logic.js` (with the weekend,
   behind-on-requirements and "tar opplæring" rules from 5c and 5d); set
   `shift_week_status` (open → published for leaders; `allocating` is legacy) per week, with a
   "Publiser måneden" shortcut that publishes all weeks of a month, and the
   unpublished-week warning described under known gaps. Wishes are already
   stored by the app.
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
   - 7f. Group leader card ("Meld på medlem", "Når vi ikke kan").
   - 7g. Editable shift descriptions (`/shifts/settings` card) and
     per-event-shift meeting point; end-of-semester report.
   - 7h. "Send påminnelse" (templates and cron route, step 8).
   - Crescat events as drafts for event shifts: depends on etjenesten's
     answers (see "Not code").
8. **Swaps and sick days** with email to the right leaders, show
   `status_message` to them (not to skiftleder), delete it after the shift.
   Also the reminder button for volunteers who have not sent in wishes (5b),
   and the samfunnettjeneste reminders (7h): to the group's leader, and for
   per-member duties also to members who have not done a shift. Needs new
   email templates and a cron route.
9. Small: keyboard alternative to drag and drop (the "Legg til" button in
   5a2 covers it),
   semester-end data cleanup. (The per-person "tar ikke opplæring" is gone;
   see 5d.)

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

- How OTA updates are published (who publishes, which channel and runtime
  version the store build uses, how a bad update is rolled back). The hide
  switch and the role-only display (4c) now come by OTA, so settle this
  before the store build.
- Review by etjenesten (show the prototype and this branch).
- Real data in Personal: is Stjerne etc. already a subgroup of
  Skjenkegruppen? Skiftleder, Kokk and Opplæring verv set per person for this
  and the previous semester, so the 5d migration can read them? After the
  migration, leaders check its list and untick "tar opplæring" where needed.
  Templates.
- Test with a few Stjerne people, then App Store / Play review.
- Ask etjenesten to check that Skiftlista fits the current plan: email volume
  for reminders, "kan ikke" and swaps; app polling and the `/open` endpoint
  (it evaluates 92 days per call); Vercel plan limits. Expected to be small.
  Samfunnet pays for hosting.
- More questions for etjenesten: the distinct verv names
  (`assignment_roles.name`) used in production, names only, so the 5d
  migration maps them correctly (no migration or seed in this repo holds the
  real list); and whether notifications should go to Slack instead of, or
  as well as, email, since leaders mostly use Slack.
- For Samfunnet's privacy person: is it fine to keep qualifications
  (skjenker, skiftleder, kokk, who approved, when) for people who are no
  longer active? Verv history is already kept for every semester because
  pingvinpoeng are summed over all of it, and qualifications currently last
  forever, with no automatic deletion; only deleting the volunteer removes
  them.
- Questions for etjenesten about Crescat: is there a read API for events
  (title, room, start/end, status)? How is access authenticated and who issues
  a read-only key? Rate limits? Can we match on `booking_request_id`? Goal:
  events from Crescat as drafts for samfunnettjeneste event shifts.
- Stjerne has no weekend shifts, so check that templates and the generator
  match that when the first templates are entered. Weekend days are a per-bar
  setting (Grøndahls: Friday and Saturday).
