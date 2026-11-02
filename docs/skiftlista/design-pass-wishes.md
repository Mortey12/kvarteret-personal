# Design pass: wish calendar and leader page package

Read-only pass, 2026-11-02. Personal on `skiftlista/skiftplan` (clean, `80362cd`),
kvarteret-internbevis on `feat/skiftlista-mobile` (checked with
`git branch --show-current`, clean). Nothing in either repo was changed. No
database was touched. The only network calls were read-only PyPI JSON lookups
for `ortools` and `opentelemetry-proto`.

Decisions already in HANDOFF are not repeated; this file proposes what the
package section marks as open, and flags conflicts.

---

# Part 1 (A to E)

## A. What exists today for wishes

Tables (`app/domain/shifts/tables.py`):
- `shift_wishes` (PK `shift_id`, `volunteer_id`; `answer` text, no CHECK; FK
  to `shifts` with `ON DELETE CASCADE`).
- `shift_availability_rules` (PK `volunteer_id`, `semester`, `weekday`;
  `answer` text, no CHECK).
- `shift_week_status` (PK `group_id`, `week_start`; `status`, `updated_at`).
  No row: current week and earlier are `published`, later weeks `open`
  (`effective_week_status`, `app_rules.py`).

Endpoints (`app/api/v1/mobile_shifts.py`, all on the mobile-card session):
- `GET /wishes` -> `ShiftsAppService.get_wishes`: the current semester's
  weekday rules (for weekdays that have templates in the person's groups, plus
  any stored), every (group, week) with shifts from now to `wish_window_end`
  (semester end, at least end of next month), and the shifts of the weeks that
  accept wishes (`WISHES_ACCEPTED` = open, allocating), each with
  `answer = effective_answer(wish, rule)` and `from_rule`.
- `PUT /wishes/weekdays/{weekday}` -> `set_weekday_rule` (current semester).
- `PUT /wishes/shifts/{shift_id}` -> `set_shift_wish`: refuses a shift not in
  `member_enabled_ids`, a started shift, and a week that does not accept
  wishes (409 "Planen for denne uka er publisert. Be om bytte i stedet.").
- One call per shift today; delete then insert (`ShiftsAppRepository.set_wish`).

Who sees what: only the person (app) and leaders whose pool includes them
(board). `_require_wishes` refuses everyone whose audience is not `skjenke`,
so samfunnettjeneste people cannot send wishes today.

Board (`ShiftsService._attach_wishes`): one query for `list_wishes_for`, one
for `list_rules_for`, over the whole pool (`pool_group_ids`: own bars plus
participating siblings), per shift "vil / kan / nei / ikke satt" with names.
The rule used is the one for the **shift's** semester (`semester_of`).

Code inconsistency found: `get_wishes` resolves every shift against the
**current** semester's rules (`person.semester`), while the board uses the
shift's semester. In December the app shows January shifts coloured by the
autumn rules, and the leader sees them as "ikke satt". Retiring rules (B)
removes the problem; otherwise it needs a fix.

"Har svart" (5b) is not built.

## B. Existing weekday rules

Options:
1. **Keep them as a fallback** under per-date answers (today's
   `effective_answer`). Problem: in a calendar, a dot coloured by a rule
   cannot be cleared: "Fjern svar" writes `null`, which falls back to the rule
   again, so the dot stays coloured. It would need a fourth stored state
   ("cleared"), and the person cannot see where a colour comes from.
2. **Convert to per-shift answers for open weeks** in a data migration, then
   stop reading rules. Real data has no rules (the app is not in the stores,
   no real person has used it), so this only matters for the demo, where
   `seed_demo.py` (`add_availability`) writes them.
3. **Retire**: stop reading them; keep the table and the endpoint for now.

**Recommendation: 3, retire**, with the demo handled by the seed, not by a
migration.
- `effective_answer(wish, rule)` becomes the wish only (keep the function, drop
  the rule argument, so the app and the board still resolve through one
  place). `_attach_wishes` stops calling `list_rules_for`; `get_wishes`
  stops calling `list_rules`.
- `PUT /wishes/weekdays/{weekday}`: keep, marked `deprecated=True` in
  OpenAPI, answers 200 and writes nothing that is read (or 410 when the store
  build is cut). `GET /wishes` returns `rules: []` (the field stays, the
  models file says changes must be additive for installed builds).
- The "Ukedager" list in `WishesList.tsx` goes away; the calendar is the one
  place to answer. Weekday letters only select dates (already decided).
- "Har svart" = at least one `shift_wishes` row on a shift of that bar in that
  month (any answer, including "kan ikke"). A note alone does not count.
- Board counts and names come from `shift_wishes` only.
- `seed_demo.py`: replace `add_availability` with per-shift answers for the
  demo people in open weeks.
- Drop `shift_availability_rules` in a later migration, after the store build
  (it is in the volunteer delete cleanup, `VolunteersRepository.delete_volunteer`,
  which must lose it in the same change).
- Conflict to flag: the HANDOFF hard rule "never 'kan ikke' (on the shift or a
  whole-day weekday rule)" assumes rules stay. With this recommendation the
  rule reads "never 'kan ikke' on the shift".

## C. Data model

All new tables are additive; samfunnettjeneste columns (`counted_for_group_id`
etc., 7c) are untouched.

1. **Deadline per bar.** Allocation is monthly (15th to 20th of the month
   before), so the deadline is per bar **per month**, not per semester.
   - `shift_group_settings.wish_deadline_day` (SmallInteger, nullable, CHECK
     1..28): "Svarfrist: den N. i måneden før". Null = no deadline shown.
     Set on `/shifts/settings`.
   - Optional override table `shift_wish_deadlines` (`group_id`, `month` =
     first day of the month as Date, `deadline` Date, `updated_by` UUID,
     `updated_at`; PK `group_id, month`). Only needed if leaders move single
     deadlines (Easter, exams). Can be left out of the first migration.
   - The deadline is **soft**: the app keeps accepting wishes until the week
     is published (today's rule). After the deadline the app shows "Svarfrist
     gått ut" instead of days left.
2. **Note and maks per person per month.** New `shift_month_answers`
   (`volunteer_id` FK `volunteer_records`, `month` Date (first of month),
   `note` Text nullable CHECK length <= 200, `max_shifts` SmallInteger nullable
   CHECK 0..31, `updated_at`; PK `volunteer_id, month`). Per person, **not per
   bar**: maks is the person's own capacity across bars, and the suggestion
   counts shifts in every group (as `count_shifts_by_volunteer` already does).
   The note is seen by the leaders whose pool includes the person, the same
   rule as wishes. Add it to the delete cleanup in `delete_volunteer`.
3. **"Alle skift / one option per shift name"** needs no storage. The app
   builds the options from the distinct `title` values of the wishable shifts
   in the shown month (trimmed, case-insensitive), adding the group name when
   the person has two groups with the same title ("Dagskift (Stjerne)"). The
   batch request carries explicit `shift_id`s, so templates and renamed shifts
   do not matter.
4. **Fairness counters: computed on demand**, not stored. Inputs per bar and
   semester: assignments (shifts so far, weekend shifts, "kan" shifts taken,
   from `shift_assignments` joined to the shift and the person's answer) and
   answers (`shift_wishes` rows: vil and kan counted, vil weighted more, over
   the last 4 weeks and the semester). For a bar of 20 to 40 people and about
   300 shifts a semester this is two or three queries and a pure function
   (`fairness_stats` in a new `app/domain/shifts/fairness.py`). Stored counters
   would drift every time a leader moves, removes or deletes a shift and would
   need a rebuild job. Revisit only if the page gets slow.
5. **Samfunnettjeneste is not blocked**: wishes and month answers are per
   volunteer without a group, deadlines are per group (an event group could
   have one later), fairness is computed per bar and never written to
   assignments, and suggestions live in their own table (F), so samf counting
   (7c, 7e) never sees a draft.

## D. The batch endpoint

**Confirm: yes.**

- `PUT /api/v1/mobile-card/shifts/wishes/answers`, operation id
  `setShiftWishAnswers`. Body `{"answers": [{"shift_id": 1, "answer": "vil" |
  "kan" | "nei" | null}]}`, 1 to 400 items (a month with three bars and two
  shifts a day is under 200), duplicate `shift_id` refused with 422.
- **All or nothing.** The service validates every item first (exists, in
  `member_enabled_ids`, not started, week in `WISHES_ACCEPTED`), and only then
  writes: one `DELETE ... WHERE volunteer_id = :v AND shift_id IN (...)` and
  one multi-row `INSERT` for the non-null answers. Any refusal raises before
  the write, so the request-scoped unit of work rolls back (repositories never
  commit, `app/db/repository.py`). 409 with the first reason, plus the refused
  shift ids in the text count ("3 av skiftene er publisert ...").
- **Idempotent**: it sets state, so a retry gives the same result. No
  idempotency key is needed.
- Returns the month view (below) for the month of the first item, so the app
  replaces its cache with one response.
- **Month view**: new `GET /wishes/calendar?month=YYYY-MM`
  (`getShiftWishCalendar`), additive next to `GET /wishes`. It returns: the
  months the person may switch to (from the current month to the month of
  `wish_window_end`); per group the deadline and days left and `has_answered`;
  the person's note and maks for the month; every shift of the month in their
  member groups with `day` (Oslo date), times, title, `answer`,
  `accepts_wishes` (from the week status) and `past`; and their own published
  assignments in the month (`mine: [{shift_id, kind}]`). Drafts in
  unpublished weeks are never included.
- **Which weeks accept**: unchanged (`WISHES_ACCEPTED`, future shifts only).
- **Published or past weeks in the calendar**: their dots are drawn with the
  stored answer but dimmed and not selectable; tapping such a date says "Uka
  er publisert" or nothing for past days. Weekday letters skip them.
- **Own shifts**: a small filled marker under the day number (not a dot, so it
  never looks like an answer), from `mine`.
- **"Kopier fra forrige måned"**: server side, `POST
  /wishes/calendar/copy?month=YYYY-MM`. For every shift in the target month
  that accepts wishes and has **no** answer, take the person's answers from
  the month before for the same group, title and weekday; use the most common
  one, ties going to the more careful answer (nei, then kan, then vil). Never
  overwrite. Returns the month view and a count ("12 svar kopiert"). Server
  side keeps the rule in one place and changeable without an app update.
- **Note and maks**: `PUT /wishes/calendar/month` with `{month, note,
  max_shifts}` (both nullable), its own call.

## E. The migration

One revision, for example `20261104_1200_wish_calendar.py`, **additive only**:
- `shift_group_settings.wish_deadline_day` (nullable, CHECK 1..28).
- `shift_month_answers` (C2).
- `shift_suggestions` and `shift_week_activity` (F), so Neon is migrated once
  for the whole package. If the leader page design is still moving when the
  server sessions start, split these two into a second additive revision
  later; that costs one more review and Neon run.
- Optional `shift_wish_deadlines` only if Morten wants per-month overrides.
- No change to existing rows; `shift_availability_rules` stays (dropped
  later, after the store build).

Postgres specifics: `timestamptz` with `server_default now()`; CHECK
constraints named like the existing ones; FKs to `public.*`; `shift_suggestions.slot_id`
`ON DELETE CASCADE` (a deleted shift takes its suggestions); FKs to
`volunteer_records` without cascade (cleanup in `delete_volunteer`, as the
other Skiftlista tables); `UUID` for `changed_by` / `created_by` (auth user,
like `volunteer_qualifications.approved_by`); index
`shift_suggestions (group_id, week_start)`; no expression indexes. Tests build
SQLite from `tables.py` with `create_all`, so `tables.py` and the migration
must match by hand; add a Postgres e2e test like
`tests/e2e/test_shift_qualifications_migration.py` (upgrade, downgrade,
upgrade).

Downgrade: drops the new tables and the column. Lossy for notes, maks and
pending suggestions; nothing else. Use the Neon branch for rollback, as the
runbook says, not `alembic downgrade`.

Order against the demo ("Start here"): (1) checks pass locally; (2) read-only
Opus review of the migration; (3) Neon branch as rollback; (4) `alembic
upgrade head` on Neon, the old demo code keeps working because it is
additive; (5) merge into `skiftlista/demo`; (6) `seed_demo.py` (now writing
per-shift answers and a few notes); (7) push; (8) the runbook checks.

What the Opus review must check:
- Additive only; `down_revision` is `20261101_1200`; no data written.
- `tables.py` matches the migration column by column (names, types,
  nullability, defaults, CHECKs, FK `ondelete`), since tests never run the
  migration.
- Constraint names unique and within 63 characters.
- New volunteer FKs are in `delete_volunteer` (or deleting a volunteer fails
  with an FK error).
- `server_default` on every NOT NULL column added to an existing table, so the
  old demo code can still insert into `shift_group_settings`.
- Downgrade drops in dependency order and leaves `20261101_1200` intact.
- The e2e upgrade/downgrade test exists and was run on Postgres.
- No real names, and the seed refuses non-demo hosts as before.

---

# Part 2 (F to J)

## F. Leader page

What `/shifts` has today (`pages.py` `shifts_week`, `shift_plan.html`,
`shift-board.js`, `ShiftsService.get_week_plan`): one week, all groups in
scope or a group filter; days stacked vertically with venue cards; per shift a
collapsed "Ønsker: N vil · N kan · N nei · N ikke satt" with names; a pool
list in a short scroll box with `allowed_tokens` per person; SortableJS drag
and drop (`/shifts/assignments`, `/{id}/move`, `/{id}/delete`), each followed
by a fetch of the whole page and a swap of `#shift-board-root`; status panel
("Åpne for ønsker", "Publiser uka", "Publiser måneden") and the unpublished
banner; close, reopen and delete a shift; warnings "more than one shift in a
week" and Halvtimen birth date. Scope via `get_shift_scope`, CSRF, and
`log_admin_activity` on every action.

What the one-screen page needs that is missing: bar tabs (instead of the
filter), "Har svart N av M" with "Vis hvem", deadline, a days-as-columns grid,
selection colouring by one person's answers, the place popover with Vil / Kan
/ "Vis andre", swap by drag, problems and "N ledige" / "N uten skift" chips,
notes and maks in the people list, suggestions, "Angre", "Sist endret av" and
the two-leader warning, the month strip, print CSS.

Recommendations:
- **Rendering**: keep server-rendered Jinja for the page and the grid, and
  embed the week's data the JavaScript needs as one
  `<script type="application/json" id="week-data">` block (per shift and
  person: answer; per person: shifts this week and month, note, maks, SL,
  allowed tokens, busy days). Data blocks are not executed, so this stays
  compatible if Finding C later removes `'unsafe-inline'` from the CSP. One
  new external file, `app/static/js/shift-week.js`, plain JavaScript like
  `shift-board.js` (estimate 600 to 900 lines), reusing SortableJS and the
  fetch-and-swap refresh. No framework, no build step beyond what exists.
  Selection colouring, the people list sorting and the place popover run in
  the browser from the JSON; every change still goes to the server, which
  checks every rule.
- **Build it as a new route** (`GET /shifts/week`, or behind `?view=uke`)
  next to the old board until it works, then switch `/shifts` to it and delete
  the old template. Less risk during the demo.
- **Suggestions are stored server side**, in `shift_suggestions` (`id`,
  `slot_id` FK cascade, `volunteer_id`, `group_id`, `week_start`, `reason`
  Text, `created_by` UUID, `created_at`; unique `slot_id, volunteer_id`). Not
  in `shift_assignments`: assignments are read by the app, the counts, the
  claim capacity, the samf counting and the training count, and a flag there
  would have to be filtered in every one of them. A suggestion survives a
  reload, the second leader sees it, and the server can refuse publishing
  while suggestions are left. "Godta forslag" re-checks each one through the
  same path as `ShiftsService.assign` (pool, lock, `_check`), creates the
  assignments it can, deletes all suggestions of the week, and reports the
  ones it skipped and why (not all or nothing here: the week may have changed,
  and the leader wants the rest). "Forkast" deletes them. Publishing ("Publiser
  uka" and "Publiser måneden") is refused while the week has suggestions.
- **"Angre" is client side**, in `sessionStorage` per bar and week, up to 20
  entries. Each server action answers with its inverse (assign -> remove that
  assignment id; move -> move back; remove -> assign the person to the slot;
  bulk actions -> a list), and "Angre" sends the inverse as a normal action,
  checked like any other. If the inverse is refused (someone took the place
  meanwhile) the toast says so and the entry is dropped. Publishing is not
  undoable (it reaches the app at once). Server-side history would need a new
  log table and replay rules for little gain.
- **"Sist endret av" and the two-leaders warning**: a small table
  `shift_week_activity` (`group_id`, `week_start`, `version` BigInteger,
  `changed_by` UUID, `changed_at`; PK `group_id, week_start`), bumped in the
  same transaction by every leader action on that bar and week (assign, move,
  remove, suggestions, accept, discard, status, close, delete). Not on
  `shift_week_status`: a row there changes the week's status rules (explicit
  rows win and never auto-publish), so writing one only to record an edit
  would silently stop the week from publishing itself. The page embeds the
  version; each action sends `expected_version`; on a mismatch the action
  still runs (the server checks the rules anyway) and the answer adds "Leder A
  endret uka for 2 minutter siden" and the page refreshes. A light poll
  (`GET /shifts/week/activity?group=..&week=..` every 30 s while the tab is
  visible) shows the warning before the second leader acts. "Endret etter
  publisering" = status published by a row and `changed_at` later than that
  row's `updated_at`.
- **"Vis andre"**: everyone in the leader's pool (`pool_group_ids`: own bar
  plus participating siblings) who passes `placement_problem` for that place,
  grouped: other bars with the right qualification (marked with their bar),
  own bar's "ikke svart", then "kan ikke" last and greyed. It needs no new
  rights: the pool already allows these people (Finding D). Samf people are
  not in it (see I).
- **Month strip**: the Mondays of the shown month with their effective status
  and a dot for suggestions pending; one query for statuses
  (`get_week_statuses`) and one for suggestion counts.
- **Printing**: a print stylesheet that hides everything but the week grid
  (the mockup already has `.noprint`); names and kind per place, no colours
  needed.
- **Permissions**: unchanged model. Every read and action takes `scope=` and
  calls `scope.require(group_id)`; only Admin and Gruppeadmin of the bar (or
  an ancestor) see the page, edit, suggest and publish. "Only the bars' admins
  publish" is already true (`set_week_status`). New actions need CSRF and
  `log_admin_activity` like the existing ones.
- 5a2 items the package does not mention: keep the "Godkjenn direkte" link on
  greyed people for 5d step 4, show the parked `placement_problem` warning on
  placed people (cheap now that the page has the eligibility data), keep the
  keyboard alternative (the place popover's list is clickable, which covers
  it). Narrowing the count line to the shift's own group: yes ("N vil · N
  kan" counts only members of the shift's group; `get_wishes` already lets a
  person answer only for their member groups, so other bars could only add
  "ikke satt").
- From step 5: keep "Publiser måneden" (blocked while any week of the month
  has suggestions) and the unpublished-week banner; `fordelingWarnings()`
  from the prototype becomes the problems chip.

Conflict to flag (needs Morten, question 1): HANDOFF says "Nothing reaches
the app before the week is published", but a future week **without** a
`shift_week_status` row publishes itself on its Monday
(`effective_week_status`; the banner already says "Den blir åpen for alle
mandag ..."). A half-finished draft would then show in the app.

## G. The suggestion function

Shape (pure, in a new `app/domain/shifts/suggest.py`, no I/O):
- Input `SuggestInput`: the week, the bar, its shifts with places in order
  (slot id, kind, `open_for_samf`, start and end, already placed people with a
  `locked` flag: everything already assigned is locked), the people (pool
  members of the bar plus siblings who answered for this bar's shifts; per
  person: answers per shift, the slot ids they may take, precomputed with
  `placement_problem` and the age rule so the function stays pure, shifts and
  busy intervals this week and this month in **all** groups, maks for the
  month, quota from `quota_for`, trainee rows and `takes_trainees`, fairness
  stats from `fairness_stats`), and the bar's weekend days and needs.
- Output `SuggestOutput`: list of `(slot_id, volunteer_id, reasons:
  tuple[str, ...])`, plus `unfilled: [slot_id]` and `without_shift:
  [volunteer_id]`.
- Deterministic: no randomness; every sort ends on `volunteer_id` and
  `slot_id`; no reliance on set order. A test runs it twice and on shuffled
  input and expects the same result.

Algorithm (plan B, plain Python):
1. Candidate places: empty places of the bar's shifts in the week, **skipping
   places marked `open_for_samf`** (left for samfunnettjeneste unless a leader
   fills them) and closed shifts.
2. Hard rules for a (person, place): may take it; answered vil or kan (never
   nei, never ikke svart); no overlap with any of their shifts in any group
   (real start and end); not already on that shift; below maks for the month;
   below the quota (1 a week, Pensjonist 1 a month); a trainee only where the
   shift's skiftleder (placed or suggested) `takes_trainees`.
3. Skiftleder places first, scarcest first (fewest candidates), so trainee
   pairing can see who leads.
4. Coverage pass ("everyone who answered gets one shift"): people with no
   shift this week, fewest options first, each to their best place by score.
5. Fill pass: remaining places, scarcest first, best candidate by score.
6. Improvement pass: pairwise swaps between suggested people (never locked
   ones) that lower the total cost; bounded (for example 200 rounds), fixed
   order.
Score (lower is better), in this order of weight: vil before kan; fewest
shifts this semester relative to the bar's average; behind on weekend need;
for "kan" places, the person who offered least lately (vil and kan counted
over 4 weeks, vil double) goes first; for "vil" places, the most helpful
first; extra credit for each "kan" shift taken earlier. Nobody above their
share: a person already at the bar's average plus one this month is used
only if no one else can.

"Valgt fordi" is built from the score terms that decided the pick, at most
three, in plain words: "vil", "0 skift denne uka", "2 i desember, færrest i
baren", "har tilbudt 9 skift siste 4 uker", "trenger helgeskift (0 av 4)",
"godkjent skiftleder", "opplæring sammen med <fornavn>, som tar opplæring".
Stored in `shift_suggestions.reason`.

"N uten skift": answered (at least one vil or kan this week for this bar) and
no shift this week in any group, after suggestions are counted.

"Kopier forrige uke": same template one week earlier, same people, through
the same hard rules (so "kan ikke" is skipped), stored as suggestions.
"Fyll" on one shift and "Lag forslag på nytt" call the same function with the
already placed and accepted people locked.

Dependencies on work that is not built (conflicts to flag):
- Opplæring: trainees in normal places with `training_for` is 5d step 3,
  which HANDOFF schedules **after** this package. Until then the suggestion
  can only pair a trainee on an `opplaering` place with a skiftleder who
  `takes_trainees`, and the page shows the opplæring place as today.
- "0 helg", weekend needs and the per-bar quota are 5c (per-bar requirements),
  not built. Until then: quota from `quota_for`, no weekend term, no "0 helg".

**OR-Tools check**
- Wheel: `ortools` 9.15.6755 (latest on PyPI) has `cp313` manylinux x86_64
  (29.8 MB) and aarch64 wheels; Personal uses Python 3.13 (`.python-version`).
- It pulls `numpy>=2.0.2`, `pandas>=2.0.0`, `absl-py`, `immutabledict` and
  `protobuf>=6.33.1,<6.34`. Personal's lock has `protobuf 7.35.1` (through
  `opentelemetry-proto`, which allows `>=5.0,<8.0`), so adding ortools would
  downgrade protobuf to 6.33.x. Resolvable, but a change to the telemetry
  stack's dependency, and `pip-audit` in CI then checks the older version.
- Size: Personal's production dependencies take about 105 MB installed
  today (`.venv`, measured locally). ortools, numpy and pandas add very roughly
  200 MB or more installed (estimate, not measured: I did not install them).
  `vercel.json` sets one function (`api/index.py`, `includeFiles: app/**`,
  `maxDuration: 300`, region `arn1`) and says nothing about memory or size.
  Vercel's Python bundle limit is not in the repo; I recall 250 MB, raised to
  500 MB for Python in 2025, but I could not verify which applies to
  Samfunnet's plan.
- Cold start: `cp_model` imports numpy and pandas; importing it at module
  level would slow every cold start of the whole app. It would have to be
  imported inside the function.
- import-linter: passes. `.importlinter` checks only `app` packages, and
  third-party imports are not part of any contract. Put it in
  `app/domain/shifts/suggest.py` (pure) or behind a protocol.
- CI: `uv sync --locked` needs the lock updated; `pip-audit` runs on the new
  packages; no other step changes.
- **Recommendation: plan B.** The problem is tiny (a bar week is 5 to 15
  shifts, 2 to 4 places each, 15 to 40 people); a greedy fill with swaps
  finds good answers in milliseconds, "Valgt fordi" follows directly from the
  score, there is no new dependency, no protobuf downgrade and no wait for
  etjenesten. Keep the input and output shapes solver-neutral, so CP-SAT can
  replace the inside later if the leaders find the suggestions poor.
- Not verified: the installed size, the Vercel limit on Samfunnet's plan, and
  cold-start time. etjenesten must answer: do they accept `ortools` (and the
  protobuf change), what plan and limits the Vercel project has, and whether
  they would rather run a solver elsewhere. With plan B none of this blocks
  the build.

## H. The app calendar

Today (`src/features/shifts/`): `WishesList.tsx` renders "Ukedager" (one
`AnswerChips` per weekday) and then months, weeks, days and one card per
shift; `useWishes` (React Query, key `shiftsKeys.wishes`), `useShiftActions`
`perform({run, refresh})`; `shiftsRepository.ts` wraps the generated client
(`src/core/api/kvarteret-personal`, `npm run api:generate:personal`, only the
`mobile-shifts` tag). Installed: `date-fns` 4, `expo-linking`,
`expo-localization`, `expo-updates`, `react-native-paper`; no calendar or
date-picker library, no `expo-calendar`, no `expo-file-system`, no
`expo-sharing`, no `expo-web-browser`.

Components (all plain React Native, no new package):
- `domain/wishCalendar.ts` (pure, tested): build the month grid from the
  month view; place each dot by the shift's `day` (the Oslo date from the
  server, never the device time zone); dots left to right by `starts_at`;
  selection, weekday letters (only selectable dates), the "Svaret gjelder"
  options from titles, which dots an answer will change, the batch body.
- `ui/components/WishCalendar.tsx` (grid with `Pressable` days, 44 pt
  targets, dots, own-shift marker, ring on dots that will change),
  `WeekdayLetters.tsx`, `AnswerScope.tsx` ("Alle skift" plus names, hidden
  with one name), `AnswerBar.tsx` (Vil gjerne, Kan hvis nødvendig, Kan ikke,
  Fjern svar, count of selected dates), `WishLegend.tsx`, `MonthSwitcher.tsx`,
  `DeadlineBanner.tsx` (deadline, days left, not answered), `MonthNoteSheet.tsx`
  (note and maks), a "Kopier fra forrige måned" button.
- `vm/useWishCalendar(month)` and a mutation that writes the batch response
  into the cache (no optimistic update needed; one round trip).
- `WishesList.tsx` is replaced; the weekday API calls go.
- Data flow: `GET /wishes/calendar?month=` -> grid; tap, letters, scope ->
  local state; answer button -> `PUT /wishes/answers` -> response replaces the
  cache; note -> `PUT /wishes/calendar/month`; copy -> `POST
  /wishes/calendar/copy`.

OTA versus store: all of this is JavaScript plus new server endpoints, so it
can ship by OTA (`runtimeVersion` policy `appVersion`, version 2026.2.0,
channels `production` and `preview` in `eas.json`) once OTA publishing is
settled with etjenesten. Since the store build is not made yet, the simplest
is to have it in the store build. Server first, always additive, so installed
dev builds keep working.

"Legg til i kalender" without new native packages:
- iOS: the app asks Personal for a short-lived signed link (`POST
  /mine/{assignment_id}/calendar-link` returns a URL with an `itsdangerous`
  token valid for 10 minutes, since `Linking.openURL` cannot send the bearer
  header), and opens it with `Linking.openURL`. Personal answers `text/calendar`
  (one VEVENT, DTSTART and DTEND in UTC from `starts_at` and `ends_at`, so a
  shift ending after midnight ends on the right date). Safari offers "Legg
  til".
- Android: opening an `.ics` link downloads a file, and whether a calendar app
  picks it up depends on the phone. Use a Google Calendar template link instead
  (`https://calendar.google.com/calendar/render?action=TEMPLATE&text=..&dates=<UTC start>/<UTC end>&location=..`),
  built in the app from the shift, opened with `Linking.openURL`; it opens the
  Calendar app or the web. It sends the shift title, time and venue to Google
  at the person's own tap; no names.
- With a store build, `expo-calendar` (native, needs calendar permission and
  Morten's approval) would add events directly on both platforms. Not
  recommended for this package.
- Not verified on devices: Safari's handling of the signed `.ics` URL and the
  Google link on Android without the Calendar app.

After midnight: the dot sits on the start date (decided); time label from
the server (`time_label`); hours and calendar entries use the real
`ends_at`.

Language: the app uses Bokmål (`lng: "no"`, `translations.ts`) and English,
chosen from the device language (`LanguageProvider.tsx`), and every server
text is Bokmål. There is no Nynorsk. The app mockup is Nynorsk, which
conflicts. **Recommendation: Bokmål plus the English keys, like the rest of
the app** (question 7).

## I. Samfunnettjeneste check

- 7a (group types) and 7b (exemptions): untouched; wishes still require the
  `skjenke` audience, and nothing new reads `is_skjenke_member`.
- 7c (`counted_for_group_id`, no-show): suggestions are not assignments, so
  no draft is ever counted; accepted suggestions go through the
  `ShiftsService.assign` path, which is where 7c will set
  `counted_for_group_id`. No new column touches assignments.
- 7d and 7e (rights, overview): the new leader endpoints use `GroupScope`
  only; the samf MANAGE and VIEW rights give nothing here, as for
  qualifications.
- 7f ("Meld på medlem", "Når vi ikke kan"): independent; busy periods could
  later be shown on the leader page as information.
- 7g (descriptions) and 7h (reminders): the "har ikke svart" list is the data
  a reminder needs later (step 8).
- The suggestion skips `open_for_samf` places, as the package asks.
- Deadlines, notes, maks and fairness are per bar or per person and never
  feed samf counting.

Open points:
- **Can samf people send wishes? Recommendation: no**, as today
  (`_require_wishes`). Their duty is 1 to 2 shifts a semester, taken first
  come, first served in open places; a wish calendar for them would mean
  leaders plan them, which Finding D already refuses (they are outside the
  pool). Consequence of yes: a samf audience in the calendar, a pool widening
  for leaders, and a privacy question about who sees their wishes.
- **Do they appear under "Vis andre"? Recommendation: no**, for the same
  reason (outside the pool). Revisit with 7f, when group leaders can sign up
  their own members.

## J. The build split

Order: Personal server first against the dev seed, then the app against the
local Personal, with the leader page after; each part works on fake data.
"Opus review" means read-only, before Neon or the demo.

| # | Scope | Size | Model | Opus review | Files it may touch | Tests | Blocks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | Retire weekday rules in reads; month view `GET /wishes/calendar`; batch `PUT /wishes/answers`; weekday PUT deprecated; "Har svart N av M" with "Vis hvem" on today's board | M | Sonnet | No (no migration) | `app_rules.py`, `app_service.py`, `app_repository.py`, `app_models.py`, `service.py`, `models.py`, `repository.py`, `api/v1/mobile_shifts.py`, `shift_plan.html`, `openapi.json`, `scripts/dev/seed_demo.py`, `docs/reference/api-boundaries.md`, tests | A1 |
| A1 | **First slice, app**: the calendar with batch save, month switch, published and past dimmed, own shifts; remove "Ukedager" | L | Sonnet | No | `src/features/shifts/**`, `src/app/localization/translations.ts`, regenerated `src/core/api/kvarteret-personal` | `wishCalendar.test.ts`, component tests like `WishesList.test.tsx`, repository tests | A2 |
| S2 | The migration (E) and `tables.py`; deadline day on `/shifts/settings`; note and maks; copy from previous month; delete cleanup | M | Sonnet | **Yes**, the migration before Neon | `tables.py`, `app/db/tables.py`, new migration, `app_*`, `service.py`, `setup_actions.py` or `actions.py`, `shift_settings.html`, `volunteers/repository.py`, `openapi.json`, seed, tests incl. a Postgres e2e migration test | A2, L1 |
| A2 | App: deadline and days left, not-answered banner, note and maks sheet, "Kopier fra forrige måned" | S | Sonnet | No | `src/features/shifts/**`, translations, client | unit and component tests | |
| L1 | Leader page layout: new route, bar tabs, top line, week bar, days-as-columns grid, people panel with search and filters, note and maks shown, chips (problems, ledige, uten skift), existing place/move/remove, print CSS, month strip | L | Sonnet | No | `app/web/routes/shifts/pages.py`, `actions.py`, new template `shift_week.html`, new `app/static/js/shift-week.js`, `service.py`, `models.py`, `repository.py`, CSS source, tests | L2, L3 |
| L2 | Selection colouring, day header sorting, place popover with Vil / Kan / "Vis andre", swap by drag, refusal texts, `placement_problem` warning on placed people | M | Sonnet | No | same as L1 | web tests for "Vis andre" scope (no one outside the pool), swap | |
| G1 | `suggest.py` and `fairness.py`, pure, plan B, with "Valgt fordi" | M | Sonnet (Opus only if Morten wants the fairness rules checked) | No (no data) | new `app/domain/shifts/suggest.py`, `fairness.py`, tests | L3 |
| L3 | Suggestions on the page: "Lag forslag", "Fyll", "Lag forslag på nytt", "Kopier forrige uke", "Godta" and "Forkast", publish refused while suggestions exist | M | Sonnet | No (tables come with S2) | `service.py`, `repository.py`, `actions.py`, template, JS, tests | |
| L4 | "Angre" (client stack, inverse actions), `shift_week_activity` bumps, "Sist endret av", two-leaders warning and poll, "Endret etter publisering" | M | Sonnet | No | `service.py`, `repository.py`, `actions.py`, `pages.py`, JS, tests | |
| A3 | "Legg til i kalender": signed `.ics` endpoint (Personal) and the iOS / Android buttons in Mine (app) | S + S | Sonnet | Short Opus look at the signed link (security) | `mobile_shifts.py`, `app_service.py`, new pure `ics.py`, `openapi.json`; app `MyShiftsList.tsx`, `shiftsView.ts` | `.ics` text, token expiry and person check; app link builder | |

Then the demo update per the runbook (after S2's review), and later 4d-plus
and 5d step 3.

**Smallest first slice for the bar leaders: S1 + A1.** Volunteers answer in
the calendar on the demo, and the leaders see the answers and "Har svart" on
today's board, with no migration and no Neon run.

---

## Open questions for Morten

1. **Auto-publish of weeks without a row.** Today a future week without a
   `shift_week_status` row publishes itself on its Monday, so a leader's
   half-done draft shows in the app. Default: keep it, and make the leader
   page warn louder from 10 days ahead (today's banner). Alternative: the
   first leader edit or suggestion in a week writes an explicit `open` row, so
   nothing ever reaches the app before "Publiser uka". Consequence: with the
   default a forgotten week still opens for claiming; with the alternative a
   forgotten week stays hidden and nobody can claim its places.
2. **Weekday rules: retire (recommended)?** Yes: one place to answer,
   "Fjern svar" works, the semester mismatch goes away, the demo seed changes.
   No (keep as fallback): needs a "cleared" state and a way to show where a
   colour comes from.
3. **Deadline: soft (recommended) or hard?** Soft: wishes accepted until the
   week is published, only the text changes. Hard: the batch refuses after
   the deadline; leaders must then change it for latecomers.
4. **Per-month deadline overrides now?** Default no: only a default day per
   bar. Yes adds the override table and a small form.
5. **Plan B for the suggestion (recommended)?** Yes: build now, no etjenesten
   wait. Plan A: wait for etjenesten's answer, a protobuf downgrade, and an
   unverified size budget.
6. **Samf people: no wishes and not in "Vis andre" (recommended)?** Yes keeps
   Finding D and the pool rules. No widens the pool and needs the privacy
   person.
7. **App language: Bokmål with English (recommended) or Nynorsk?** Bokmål
   matches the app and the server texts. Nynorsk would make Ønsker the only
   Nynorsk screen.
8. **Opplæring on the leader page before 5d step 3.** Default: show today's
   opplæring places and pair trainees only there, and leave the "normal place
   marked opplæring" for step 3. Alternative: move 5d step 3 (with 4d-plus)
   before L3, which delays the leader page.

## What I could not verify

- The installed size of `ortools`, `numpy` and `pandas` on Linux, and
  Vercel's Python bundle limit and memory for Samfunnet's project (not in the
  repo; `vercel.json` sets only `maxDuration` and the region).
- Cold-start cost of importing `cp_model`.
- Whether `uv lock` would resolve cleanly with `protobuf` 6.33 (checked the
  version ranges only).
- How iOS Safari and Android handle the signed `.ics` link and the Google
  Calendar link on real phones.
- How OTA updates are published (parked with etjenesten), so whether A1 to A3
  ship by OTA or in the store build.
- The leader's display name for "Sist endret av": I did not check which
  account field holds it.
- That the demo database has no `shift_week_status` rows that would change
  the month strip; I did not connect to Neon, by the rules of this pass.
- Nothing was run: no tests, no lint, no server.
