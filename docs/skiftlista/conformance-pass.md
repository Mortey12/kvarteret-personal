# Conformance pass: Skiftlista against Kvarteret's own projects

Read-only pass, 2026-11-02. Nothing in either repo was changed (see "Changes
made" at the end of part 2). Only this file was written.

Part 1 (this part): kvarteret-personal, sections A to C.
Part 2: kvarteret-internbevis, sections A to C, then D and E for both repos,
then the fix list, risks, questions and what I could not verify.

Short names: "ours" is `skiftlista/skiftplan` at `80362cd` (Personal) and
`feat/skiftlista-mobile` (app). "Upstream" is the `upstream` remote
(`kvarteret/...`), fetched read-only on 2026-11-02. Upstream's default branch
is `develop` in Personal and `development` in the app.

One thing to say first: HANDOFF.md has no section named "Design pass
results". The package section ("Wish calendar and leader page package")
exists, and `design-pass-wishes.md` holds the design pass, but the results are
not yet written into HANDOFF. This pass does not depend on it.

---

# PART 1: kvarteret-personal

## A. Guideline inventory (Personal)

Kvarteret has no CONTRIBUTING file, no PR template, no CODEOWNERS and no
commit message rule in Personal (checked with `git ls-tree` on
`upstream/develop`). Migration, constraint-name, accessibility and i18n
conventions are not written down either; they can only be read from existing
code (marked "inferred" below). Everything else is listed here.

| # | Path | One-line rule | Applies to Skiftlista |
| --- | --- | --- | --- |
| A1 | `AGENTS.md` | Read `.agents/README.md` first; Vercel defaults: stateless functions, secrets only in env, `waitUntil` for post-response work, cron in UTC, no new work on Vercel KV/Postgres | Yes. We use no background work and no new storage |
| A2 | `.agents/README.md` | FastAPI routes and Pydantic models are the API source of truth; regenerate and check `openapi.json`; mobile-card changes additive; verify cross-repo claims against code; "handoff notes ... are temporary working artifacts" | Yes (see C13, a conflict with HANDOFF) |
| A3 | `.agents/security.md` | Validate at the FastAPI boundary; authz close to route or service; no SQL by interpolation; user-safe errors, server-side context; mobile-card shapes are client contracts, keep additive; review `openapi.json` for exposed internal fields | Yes |
| A4 | `.agents/skills/maintaining-api-boundaries` | After route or model change: regenerate, `git diff openapi.json` for churn, check consumers that really call it | Yes |
| A5 | `.agents/skills/security-audit` | Checklist before committing auth, forms, tokens, storage, admin routes | Yes (leader routes, mobile session) |
| A6 | `.agents/skills/verifying-web-changes` | Reuse `ui_macros.html`; check HTMX swap targets; keep Alpine state inside its component | Yes (leader pages) |
| A7 | `.agents/skills/documenting-repo-interactions`, `writing-skills` | Cite source files in durable docs; separate "schema exists / generated / called / deployed" | Yes for docs; `writing-skills` no |
| A8 | `.agents/interface-design/system.md` | Square corners, `.app-panel`, `.app-input`, `.app-button`, shared macros; dense admin layouts; keep Norwegian copy; check mobile and desktop widths | Yes (leader pages) |
| A9 | `.importlinter` | Four contracts: layers (web > api > domain > db > infrastructure > shared), domain modules independent except `tables.py`, domain and web must not import infrastructure except declared seams | Yes |
| A10 | `pyproject.toml` | `ruff` with default rules (no `[tool.ruff]` section, so no formatter config), pytest `asyncio_mode = auto`, `ty` as dev dependency | Yes. No format rule exists, which matches "never ruff format" |
| A11 | `.github/workflows/ci.yml` | Jobs: `checks` (uv sync --locked, bun build:assets, ruff, lint-imports, pytest on SQLite, openapi --check, pip-audit, bun audit) and `migrations` (Postgres 17, alembic upgrade from empty, `check_schema_drift.py`, `tests/e2e`) | Yes. The `migrations` job runs our e2e tests and all six of our migrations |
| A12 | `.github/dependabot.yml` | Weekly updates for uv, npm and actions | Not touched by us |
| A13 | `docs/README.md` | Diataxis layout: tutorials, how-to, reference, explanation; this folder is the canonical documentation home | Yes (we added `docs/skiftlista/`, outside that layout) |
| A14 | `docs/reference/api-boundaries.md` | Consumer table, auth per consumer, additive rule for installed apps | Yes (we edited it) |
| A15 | `docs/reference/configuration.md` | Environment variables | Applies if we add settings; we added none |
| A16 | `docs/how-to/run-tests.md` | Four test layers (fakes, ASGI app, static gates, e2e on migrated Postgres); e2e skips without `E2E_DATABASE_URL` | Yes |
| A17 | `docs/how-to/update-openapi-clients.md` | Regenerate `openapi.json`, then the app client, then the website; commit `openapi.json` with the backend change | Yes (we edited it) |
| A18 | `docs/explanation/kvarteret-personal-architecture.md` | One request, one transaction; repositories never commit; `queries.py` / `service.py` / `repository.py` / `models.py` split; tables in `app/domain/{module}/tables.py` on the shared metadata; "~800-line cap" for files in `app/domain`; commit-before-effect for email | Yes |
| A19 | `docs/adr/001` to `003` | 001: workflow coordinator for stateful processes, no event bus for core rules; 002: auth consolidation; 003: append-only `domain_events` for lifecycle audit | 001 partly; 002 and 003 not directly |
| A20 | `scripts/check_schema_drift.py` (run by CI) | Compares table and column names between the migrated database and `public_metadata`. It does not compare types, constraints or indexes | Yes |
| A21 | `sonar-project.properties` | SonarQube sources `app`, tests `tests` | Context only |
| A22 | Inferred from `migrations/versions/` | Revision id `YYYYMMDD_HHMM` equal to the file prefix; constraint names `<table>_<what>`; indexes `<table>_<cols>_idx`; new tables enable RLS and the tables are not granted to `anon` or `authenticated`; `schema="public"` on every op | Yes |
| A23 | Inferred from `app/observability.py` and web actions | Every admin web mutation calls `log_admin_activity(...)`; the `app.audit` logger is for audit events | Yes |

## B. Our changes (Personal)

Definition: `git diff upstream/develop...HEAD`. The merge base is
`23077b6` (the current tip of `upstream/develop`), so 33 commits ahead, 0
behind.

- 77 files changed, 23,688 insertions, 1,446 deletions.
- 59 added, 18 modified, 0 deleted.
- Of the 1,446 deletions, 1,436 are in `openapi.json`. They are diff noise
  from alphabetical re-ordering of schemas. A JSON comparison shows: 0 removed
  paths, 0 changed operations, 0 removed schemas, 0 changed schemas, 28 added
  paths (12 under `/api/v1/mobile-card/shifts`, 16 web routes under
  `/shifts`; upstream already lists its web routes in the file), 39 added
  schemas, no duplicate operation ids.

Skiftlista-owned (59 files, all new): `app/domain/shifts/` (15 files),
`app/api/v1/mobile_shifts.py`, `app/web/routes/shifts/` (4), 4 shift
templates, `app/static/js/shift-board.js`, 6 migrations, 3 scripts
(`seed_shifts.py`, `seed_demo.py`, `scripts/shifts/copy_verv_to_qualifications.py`),
`docs/skiftlista/` (HANDOFF and 2 mockups; the design-pass file is untracked
and not in the diff), and 22 test files (including `tests/support/shift_groups.py`
and `tests/unit/domain/conftest.py`).

Shared (18 files, all modified): 17 hand-edited plus the generated
`openapi.json`. Lines and reasons are in section E.

| Group | Files |
| --- | --- |
| Skiftlista-owned | 59 |
| Shared, hand-edited | 17 |
| Shared, generated | 1 (`openapi.json`) |

(59 + 17 + 1 = 77.)

## C. Compliance (Personal)

Verdicts: pass, fail or unclear. Checks I ran, with the HANDOFF test
environment: `ruff check .` pass; `lint-imports` 4 contracts kept, 0 broken;
`pytest --ignore=tests/e2e` 1080 passed, 2 skipped; `export_openapi.py --check`
exit 0. `git status` was unchanged afterwards (only the untracked
`design-pass-wishes.md` that existed before).

### C1. Layering and import contracts (A9, A18). PASS

- All four contracts kept. Our change to `.importlinter` is one line (adds
  `app.domain.shifts` to the independence list), the intended extension.
- `app/domain/shifts/*` imports no other domain module's service or
  repository. Cross-module reads go through `tables.py` only (volunteers,
  groups, courses, role assignments), which is the declared seam.
- Reverse edge: `app/domain/volunteers/repository.py` now imports
  `app.domain.shifts.tables` (to delete shift rows with the volunteer). That
  is a tables-only import, so the contract allows it, but it is the one place
  where an existing module learns about Skiftlista (see E).
- Wiring is in `app/runtime.py` and `app/dependencies.py` in the same style as
  other modules. `ShiftsRepository()` is constructed twice in the container
  (three services, three repository instances). Harmless.

### C2. File size and module shape (A18, A19). FAIL (soft)

- Architecture doc: "No file in `app/domain` exceeds the ~800-line cap".
  `app/domain/shifts/app_service.py` is 1,276 lines with 64 functions;
  `repository.py` is 779 and `service.py` 647. Upstream is itself over the cap
  in `volunteer_applications/repository.py` (1,265) and `service.py` (1,047),
  so the sentence is already stale, but ours would be reviewed against it.
- Module shape: upstream splits reads and writes (`queries.py`,
  `service.py`, `repository.py`, `models.py`). We use `app_*` and plain files
  and put errors in `errors.py` rather than `models.py`. Reasonable, but a
  deviation worth a line in the architecture doc.
- ADR-001 asks for a workflow coordinator for stateful processes. Claim, swap,
  "kan ikke" and week publishing are stateful, and they sit in one 1,276-line
  service. Unclear whether maintainers would ask for a `workflow.py` split.

### C3. Naming (A8, A18, A22). UNCLEAR

- Tables, columns, files and classes are English snake_case or CamelCase and
  follow the `shift_*` prefix. Pass.
- Architecture doc: "The schema speaks English". Stored values are Norwegian:
  `answer IN ('vil','kan','nei')`, slot kinds `skiftleder`, `skjenker`,
  `kokk`, `opplaering`, `garderobe`, qualification values. Names are English;
  values are Norwegian domain words. Not covered by any written rule.
  I mark it unclear and suggest writing it down as a deliberate choice.
- `opplaering` (ASCII for "opplæring") is used in code and data; fine, but
  HANDOFF already plans to remove it (5d step 3).

### C4. `tables.py` against migrations (A20, A22). FAIL (partial)

Method: I generated SQL offline with `alembic upgrade 20261025_1200:head
--sql` (no database) and compared column names with `public_metadata`. It
stopped at `20261029_1200` because that migration runs a SELECT (offline mode
returns `None`), so I read the last three migrations by hand.

- Column names: all shift tables and `course_sessions`,
  `course_session_signups`, `volunteer_qualifications`, `shift_closed_periods`
  match their migrations. The only differences were columns added by the later
  migrations I read by hand (`training_for`, `courses_enabled`,
  `training_shifts_needed`, `sibling_skiftleder_allowed`), which are present
  in both. PASS for what `check_schema_drift.py` checks.
- Constraints and indexes: FAIL. `tables.py` declares 6 constraints (3 unique, 3 CHECK: on
  `shift_assignments`, `shift_closed_periods`, `volunteer_qualifications` and
  `course_session_signups`) and no indexes. The migrations create 10 more CHECK
  constraints (for example `shift_templates_weekday_range`,
  `shifts_ends_after_start`, `shift_slots_count_nonnegative`,
  `shift_wishes_answer_values`, `shift_week_status_is_monday`,
  `course_sessions_capacity_nonnegative`) and 9 indexes
  (`shifts_starts_at_idx`, `shift_wishes_volunteer_idx`, ...). Upstream's
  `tables.py` does declare both (`event_interest.py`: `Index(...)` and
  `CheckConstraint(...)`). Consequence: the SQLite unit tests (which use
  `create_all` from `tables.py`) never exercise those 10 CHECK constraints;
  only Postgres e2e would, and our e2e tests cover just the qualifications
  migration and the copy script.
- Server defaults and types are not compared by CI, so I did not check them
  one by one beyond the 1101 migration (consistent).

### C5. Constraint names and revision ids (A22). FAIL (one item)

- Revision ids: `20261027_1200` to `20261101_1200`, chain unbroken, first
  `down_revision` is `20261025_1200` (upstream's current head). Format and
  file prefix match. PASS.
- Constraint names follow `<table>_<what>` (`..._range`, `..._values`,
  `..._key`, `..._valid`, `..._nonnegative`). PASS.
- `20261030_1200_shift_closed_periods.py` names its index
  `ix_shift_closed_periods_group_id`, the auto-style name. Every other index in
  our chain and upstream is `<table>_<cols>_idx`. FAIL (S). It is already
  applied on Neon (demo), so renaming needs a new migration or a deliberate
  exception; see the fix list.
- Row level security: `20261027`, `20261028` and `20261101` enable RLS and
  revoke from `PUBLIC, anon, authenticated`. `20261029_1200` (creates
  `shift_week_status`) and `20261030_1200` (creates `shift_closed_periods`) do
  not. Upstream enables RLS on every new table (5 migrations, plus the
  `public_grants_hardening` default privileges). The default-privilege
  hardening probably keeps these two tables unreachable from `anon`, but it is
  not what the other tables do. FAIL (S), security-flavoured.
- `20261029_1200` contains data logic (reads and writes rows). Upstream
  migrations also do data work in places (for example
  `20260810_..._preserve_public_choice_labels`), but offline `--sql` mode is
  broken by it. Low.
- Downgrades exist and are lossy where HANDOFF says so. Unclear whether
  upstream wants downgrades at all; theirs exist.

### C6. Additive API and the OpenAPI and client flow (A2, A3, A14, A17). PASS

- JSON comparison (B) shows only additions. No existing path, operation or
  schema changed. Mobile endpoints are new, under a new tag
  (`mobile-shifts`), so installed app builds are not affected.
- `GET /me` and the mobile-card session are untouched; we only added
  `MobileCardService.authenticate_session` and a dataclass (E).
- `export_openapi.py --check` passes, so `openapi.json` matches the code.
- Docs updated in the same change (`api-boundaries.md`,
  `update-openapi-clients.md`, the skill file). PASS. One thing to verify in
  part 2: our doc edit replaces `npm run api:generate` with
  `npm run api:generate:personal`. Upstream docs name `kv openapi` and
  `make openapi` interchangeably; `make openapi` does not exist (there is no
  Makefile), which is an upstream error, not ours.

### C7. Tests (A16). UNCLEAR

- Layout follows upstream: `tests/unit/domain`, `tests/unit/shared`,
  `tests/web`, `tests/api/mobile_card`, `tests/e2e`, `tests/support`. PASS.
- Style matches (fakes for repositories; real SQLite for the eligibility and
  repository tests). PASS.
- Postgres e2e: two files (`test_shift_qualifications_migration.py`,
  `test_copy_verv_to_qualifications.py`). HANDOFF says neither has been run
  since the schema-only change. CI's `migrations` job runs `tests/e2e`, so they
  would run in upstream CI for the first time. UNCLEAR: I cannot run them
  (no Postgres allowed). Risk: a red CI on the first PR.
- No Postgres test applies migrations `20261027` to `20261031` and then
  checks the schema; the CI `check_schema_drift.py` would, on column names only.
- Real names: test and seed names are invented (`Mats Solberg`, `Hedda Dahl`,
  `Ylva Vik` ...) and all emails end in `@example.dev`, as in upstream's seed.
  I cannot prove none of them match a real person. The names are common
  Norwegian names. UNCLEAR, low.
- Test files over 800 lines are not capped by any rule; the largest is
  `test_shift_app_service.py`. No finding.

### C8. Security (A3, A5, A23). PASS with two notes

- CSRF: every `<form method="post">` in the four shift templates carries
  `csrf_token` (5 POST forms in `shift_plan.html` carry it; the sixth form is a GET filter); `shift-board.js` sends `X-CSRF-Token` from the page meta tag.
  The CSRF middleware is global, so the POST routes are covered.
- Authorization: all 18 leader routes (14 POST, 4 GET) use `require_management_user`
  and `get_shift_scope`; services refuse outside the scope (403) in tests.
- Admin activity log: every leader mutation calls `log_admin_activity` (14
  call sites across `actions.py` and `setup_actions.py`), the same helper
  upstream uses (64 uses). PASS.
- App-side audit: only `shift.app.claim` and one more event use
  `get_logger("app.audit")`. Wishes, swap requests and "kan ikke" do not log.
  Upstream logs mobile-card events through the same logger. UNCLEAR, low.
- CSP: upstream CSP allows `'unsafe-inline'` (our Finding C), so the inline
  `onclick="return confirm(...)"` in `shift_plan.html:154` works. But upstream
  uses `hx-confirm` for delete confirmations (`group_history_members.html`,
  `volunteer_course_completions_panel.html`). Ours is a style deviation that
  becomes a real break the day the CSP is tightened. FAIL (S).
- Errors: user-safe Norwegian text in `detail`; no stack traces. PASS.
- No secrets added; no new environment variables; `requirements.txt`,
  `uv.lock`, `bun.lock` and `package.json` are unchanged by us.
- Row level security: see C5 (two tables without it).
- Not run: `pip-audit` and `bun audit` need the network; we added no
  dependencies, so no change in result is expected.

### C9. Texts and languages (A8). PASS with a question

- Leader pages: Norwegian Bokmal, matching the surrounding admin UI, as
  `system.md` asks. API `detail` texts are Norwegian, meant to be shown as is.
- No em dashes in anything we added (checked added lines in the whole diff).
  The em dashes in `app/db/tables.py` and `scripts/dev/bootstrap.py` are
  upstream's own text.
- Upstream has `app/web/i18n.py`, used for the public volunteer application
  pages only; admin pages hard-code Norwegian. Ours does the same. PASS.
- The app mockup is Nynorsk while the server texts are Bokmal. That is an open
  question in HANDOFF, not a conformance finding. Covered in part 2.

### C10. Accessibility (A8; no written rule). UNCLEAR

- No accessibility rule is written down in Personal. `system.md` only asks to
  check mobile and desktop widths.
- Ours: `aria-`, `<label>` or `sr-only` appear 28 times across the four shift
  templates, comparable to other pages (1 to 11 per page upstream).
- The board is drag and drop (SortableJS) with no keyboard alternative;
  HANDOFF step 9 and 5a2 plan a "Legg til" button. Until then the board cannot
  be used without a pointer. That is a gap against general practice, not
  against a Kvarteret rule.
- Visual system: we use the shared classes (`app-panel`, `app-button`, ...,
  8 to 16 uses per template) and no `rounded-` classes in our templates.
  `system.md` says square corners. PASS. (The nav link we added in
  `base.html` copies the neighbours' `rounded-full`, which is how the
  existing nav looks.)

### C11. Docs (A13, A14, A17, A18, A2). FAIL (two items)

- API docs and the skill file were updated in the same change. PASS.
- `docs/explanation/kvarteret-personal-architecture.md` was not updated: the
  module list, the ownership diagram and "Route Surfaces" do not mention
  shifts or `/api/v1/mobile-card/shifts`. `docs/README.md` "Canonical
  Ownership" does not list Skiftlista. FAIL (S), "before upstream PR".
- `docs/reference/configuration.md` needs nothing (no new settings). PASS.
- Our docs sit in a new folder `docs/skiftlista/` (HANDOFF is 118 KB, 1,822
  lines; the design-pass file adds 40 KB; the mockups add 55 KB of HTML).
  Upstream's structure is Diataxis; no folder for per-feature notes. See C13.

### C12. Commit style (A22). UNCLEAR

- No written rule. Upstream history is plain imperative sentences with a PR
  number (squash merges), a few `fix(scope):` and `refactor(scope):`, merge
  commits for branches.
- Ours: three styles mixed (`Skiftlista: ...` 17, `docs(skiftlista): ...` 10,
  `feat(...)` 4, `Shifts: ...` 2). 33 commits, all by one author.
  30 have `Co-Authored-By: Claude`, 27 have `Claude-Session`. Upstream has no
  such trailers (checked the last 25 commits). The count missing the trailers
  is HANDOFF's rule, not Kvarteret's.
- Three subjects are over 72 characters. If upstream squash-merges, none of
  this matters.

### C13. Conflicts between HANDOFF and upstream guidelines

1. `.agents/README.md` says handoff notes and execution plans are temporary
   working artifacts and that the source and `docs/` are the record.
   `docs/skiftlista/HANDOFF.md` is a 1,800-line permanent file, with demo
   hosting, runbooks, Neon branch names and a security-findings list for
   etjenesten. It also holds "Notes for etjenesten (not sent)". Merging this
   file upstream conflicts with the rule and exposes unsent security notes.
   Recommend: keep it on the fork only, or split it into durable docs
   (reference and explanation) plus a private working file. Decision for
   Morten.
2. HANDOFF says pushes and PRs to Kvarteret are blocked and the demo branch
   carries demo login code; nothing in `skiftlista/skiftplan` contains it
   (checked: no `demo_login.py`, no `render.yaml`). Consistent.
3. HANDOFF says "web routes are in openapi.json too" and tells the reader to
   run `export_openapi.py --check`. Upstream docs say `kv openapi` and
   `make openapi`. Equivalent; no conflict.
4. HANDOFF asks for commit trailers (`Claude-Session`) that upstream does not
   use. Harmless, but squashing would remove them anyway.
5. HANDOFF plans samfunnettjeneste group types, a "fritatt" flag on every
   group and a new permissions table. Those touch `groups`, a shared module,
   and `Gruppeadmin` handling. Nothing in the upstream guidelines forbids it;
   the architecture doc's module independence means it must go through
   `tables.py` or an injected protocol. Flagging so it is planned that way.
6. HANDOFF Finding E (any Gruppeadmin can edit any group's membership) is an
   upstream issue; HANDOFF says it is not yet reported. Not a conformance
   item, but our qualification rules depend on it.

---

# PART 2: kvarteret-internbevis (the app)

Branch `feat/skiftlista-mobile` at `92248e9`, clean. Upstream default branch
is `development` (tip `74eb289`, 2026-10-25). `upstream/main` is older
(2026-05-10) and is not the integration branch: the app's own release
workflow builds store releases from GitHub Releases. Our branch is 6 commits
ahead of `upstream/development`, 0 behind.

## A. Guideline inventory (app)

No CONTRIBUTING, no PR template, no AGENTS.md or CLAUDE.md, no commit message
rule in the repo (checked with `git ls-tree` on `upstream/development`).

| # | Path | One-line rule | Applies to Skiftlista |
| --- | --- | --- | --- |
| B1 | `docs/architecture/FEATURE_FIRST_MVVM_LITE.md` | Feature-first folders (`data/`, `domain/`, `ui/`, `vm/`); no feature imports another feature; composed hooks of at most about 8 members; discriminated unions, not parallel booleans; stable context values; every network payload crosses exactly one Zod parse at the repository layer; screens over about 400 lines get split; Uniwind `className` first; `// Style escape hatch: <reason>` on every `style`; alias imports `@/*`; tests for new logic | Yes, all of it |
| B2 | `.dependency-cruiser.cjs` | Mechanical version of the four boundary rules plus two grandfathered exceptions with reasons; "do not add a new one without writing the reason there and in the ExecPlan Decision Log" | Yes |
| B3 | `biome.json` | Biome lint and format: 4 spaces, width 100, double quotes, no semicolons, trailing commas; generated clients excluded | Yes (we added our generated folder to the exclusion) |
| B4 | `scripts/check-style-exceptions.sh` | Fails on `StyleSheet.create` and `themeColors` in `src` (uses `rg`) | Yes |
| B5 | `.github/workflows/code-quality.yml` | CI on every push and PR: `npm run api:check`, `biome ci`, `tsc --noEmit`, `jest --ci`, style check, `lint:architecture` | Yes |
| B6 | `.github/workflows/` (OTA, release, preview) | OTA publish is a manual dispatch to the production channel; store builds come from GitHub Releases; every PR gets an EAS Update preview | Yes (this is how Skiftlista reaches phones) |
| B7 | `docs/architecture/ADR-0001-lenient-backend-response-schemas.md` | Backend response schemas must tolerate unknown additive fields at every level; strict only for app-owned payloads | Yes |
| B8 | `docs/architecture/ADR-0002`, `ADR-0003` | Event occurrence projections; progressive benefit tiers | No |
| B9 | `docs/skills/new-screen/SKILL.md` and templates | New screen: route type, screen in feature `ui/`, VM only if complex, repository, i18n keys in `translations.ts`, root navigator, tests | Yes |
| B10 | `README.md` | Install and run, `cn` helper, architecture pointers, PII storage decision (cached card in AsyncStorage, session token in SecureStore), API client generation, preview and release workflows | Yes (we added a section) |
| B11 | `PLANS.md` | Execution plans (ExecPlans) as self-contained living documents; Decision Log | Convention for plans; we have none in `plans/` |
| B12 | `DEPLOYMENT_MANUAL_STEPS.txt`, `eas.json`, `app.json` | Date-driven versioning, manual store promotion | Not touched |
| B13 | `package.json` scripts | `api:generate`/`api:check` are wired to the events client only; `lint:ci`, `lint:architecture` | Yes |
| B14 | Inferred from code and tests | i18n: flat camelCase keys in `src/app/localization/translations.ts`, two locales (`no` and `en`, no Nynorsk); a11y: `accessibilityRole` and labels on interactive elements (used in about 20 files); tests live in `__tests__` next to the code (`jest-expo`) | Yes |
| B15 | Inferred from `git log upstream/development` | Conventional-commit style prefixes (`feat:`, `fix(scope):`, `ci:`), PR numbers on squash merges, merge commits for feature branches | Yes |

## B. Our changes (app)

`git diff upstream/development...HEAD`: 67 files, +9,372 and -1 lines
(58 added, 9 modified, 0 deleted). Merge base is the upstream tip.

| Group | Files | Lines added |
| --- | --- | --- |
| Skiftlista-owned: `src/features/shifts/` (23 source, 15 test) | 38 | about 3,470 |
| Skiftlista-owned: two route files (`skift.tsx`, `shift/[slotId].tsx`) | 2 | 56 |
| Skiftlista-owned: generator script `scripts/generate-kvarteret-personal-client.mjs` | 1 | 121 |
| Skiftlista-owned: generated client `src/core/api/kvarteret-personal/` | 16 | 5,388 |
| Shared, hand-edited (8 source, doc and config files plus 2 test files) | 10 | 337 (1 line removed) |
| Total | 67 | 9,372 |

Shared files: `README.md`, `biome.json`, `package.json`,
`src/app/localization/translations.ts`, `src/app/providers/SessionProvider.tsx`,
`src/features/auth/data/authRepository.ts`, `src/routes/(tabs)/_layout.tsx`,
`src/routes/_layout.tsx`, plus their tests
(`SessionProvider.test.tsx`, `authRepository.test.ts`). No change to
`.github`, `package-lock.json`, `app.json`, `eas.json`, `jest.config.js`,
`tsconfig.json` or `.dependency-cruiser.cjs`.

## C. Compliance (app)

Checks I ran (Node 24.16.0): `biome ci .` exit 0 (10 warnings and 3 infos, all
in files we did not touch: dashboard, games, shared interop); `tsc --noEmit`
clean; `jest --ci` 46 suites, 338 tests passed; `lint:architecture` no
violations (223 modules); `check:style-exceptions` printed OK but `rg` is not
installed here, so I grepped by hand (no `StyleSheet.create` or `themeColors`
in `src`); `api:check:personal` FAILED (see C6). `git status` stayed clean.

### C1. Feature boundaries and layering (B1, B2). PASS

- No cross-feature import, no new depcruise exception (the file is unchanged).
- `shifts` borrows tokens through `useSession()` (`SessionProvider`), not
  through `auth/data`, so the grandfathered auth exception is not used.
- `domain/` files import no `ui/`, `data/` or `react-native`. Routes are thin
  (`skift.tsx` and `shift/[slotId].tsx` import only feature `ui` and `app`).
- Our change to shared auth code is small (`renewStoredSessionToken` in
  `authRepository.ts`, two functions on the session context). It adds public
  surface to the most sensitive provider in the app.

### C2. Hooks, state and size (B1). PASS

- Hooks return 4 members (`useShiftActions`) or are plain query hooks; far
  under the 8-member limit. Feedback is one discriminated union
  (`{ kind: "success" | "error" }`), not parallel booleans.
- Largest file `MyShiftsList.tsx` is 285 lines (limit about 400). Tests exist
  for domain, data, vm, components and screens (15 test files, about 1,560
  lines).

### C3. Network payloads and Zod (B1 rule 4, B7). UNCLEAR

- Rule: every payload crosses exactly one Zod parse in `data/`. Our
  `shiftsRepository.ts` calls the generated client and trusts the types; there
  is no Zod. HANDOFF says "zod is not used", which conflicts with the rule as
  written.
- Mitigating: upstream's own `dashboard/data/eventsRepository.ts` uses a
  generated client (`samfunnet-events`) in the same way, and the lenient-schema
  ADR (B7) is satisfied by not validating (unknown fields are ignored, nothing
  is rejected). Auth, benefits and feedback do use Zod.
- Risk: a reviewer may still cite rule 4. A runtime schema would also protect
  against the server sending a value the generated union does not know (the
  last commit already adds "fallbacks for new server values" by hand).

### C4. Styling (B1 rules 6 and 7, B4). FAIL (one item)

- Uniwind `className` throughout, no `StyleSheet.create`. PASS.
- `src/features/shifts/ui/components/ProgressBar.tsx:27` uses a `style` object
  for a computed width and has no `// Style escape hatch: <reason>` comment.
  Rule 7 requires it. The CI script does not check for it, so CI is green.
  FAIL (S).
- The style script needs `rg`; on a machine without it the script prints errors
  but still exits 0, so it checks nothing there. CI installs `ripgrep`, so CI
  is fine. Low.

### C5. Imports, formatting, naming (B1 rule 8, B3). PASS

- Alias imports; no deep relative imports in production code; Biome clean for
  our files; names follow the feature-first pattern (`useXxx`, `xxxRepository`,
  `xxxView.ts`). The route folder name `shift/[slotId]` and the tab `skift`
  mix English and Norwegian; matches nothing written, harmless.

### C6. Generated client and the OpenAPI flow (B5, B10, B13). FAIL

- `npm run api:check:personal` reports `index.ts` and `types.gen.ts` as stale.
  I regenerated into a scratch folder (outside the repo) and compared: the
  current `openapi.json` in Personal contains eight `Body_shift_...` form
  schemas (the leader pages' form bodies) that the checked-in client does not
  have. The client was generated before the leader web routes (4b) existed in
  `openapi.json`. Nothing in the shifts API itself changed.
- Side effect to decide on: the generator keeps only operations tagged
  `mobile-shifts` but not the unused component schemas, so leader-form body
  types would ship in the app bundle (types only, no runtime cost). Pruning
  unused schemas would keep the "no code for other consumers" promise in the
  README.
- `api:check` in CI runs only the events client. Ours is documented as not in
  CI ("CI has no Personal checkout"), so a stale client cannot turn CI red,
  which is why this was not caught. Fix: regenerate (S) and, optionally, check
  against the `openapi.json` of upstream `develop` over HTTPS (M).
- Backend responses are consumed additively (no strict parsing), in line with
  the additive rule in Personal.

### C7. Tests (B1 acceptance list). PASS

- Tests sit in `__tests__` next to the code, cover pure rules, the requester
  (token renewal, header persistence), repository calls, screens and the claim
  flow. 338 tests pass.
- Fake data: names in tests are invented first names (`Kari`) and group names
  from the domain (`Stjerne`, `Kjøkken`). No real people that I can tell.
  Unclear only in the sense that I cannot prove it.

### C8. Texts, languages and i18n (B9, B14). PASS with a question

- All user-visible text goes through `t(...)`, with keys added for both `no`
  and `en` (42 keys each). Server messages (`detail`) are shown as is, in
  Bokmal, as in the API doc.
- The app has no Nynorsk locale. The design mockup is Nynorsk and HANDOFF lists
  the language as open: the answer from the code is that the app speaks
  Bokmal (`no`) and English today, so Nynorsk would be a new locale (not part
  of this change). The `en` strings were written by us and are not reviewed by
  a native speaker.
- English fallback messages exist in `shiftsError.ts` and `shiftsRequester.ts`
  (internal error texts); they are mapped to translated texts in the UI.
- No em dashes in anything we added to the app (checked the whole diff).

### C9. Accessibility (B14). UNCLEAR

- No written rule. Our UI uses `accessibilityRole` and labels in 13 places
  across the shifts components (the progress bar, buttons, chips). Comparable
  to other features (1 to 4 per file).
- Not checked with a screen reader or on a device. The wish calendar will need
  its own pass: dots coloured by answer must also be readable in text (HANDOFF
  already says "in words too" for the leader page; the app mockup shows only
  colour).

### C10. Security and privacy (B10). PASS with notes

- Tokens: shifts never stores credentials itself; the renewed token is written
  to the same SecureStore slot the card refresh uses
  (`SESSION_STORAGE_KEYS.accessToken`). A failure to persist it emits a
  diagnostic and keeps the session usable.
- Shift data is not cached to disk by us (only TanStack Query memory). Names
  of crew members are first name and place only (server decides).
- The new `getSessionToken` on the context hands the bearer token to any
  component that calls `useSession()`. Only `useShiftsRepository` uses it.
  Acceptable, but it widens what a future component could do.
- Operational diagnostics use the existing `emitOperationalDiagnostic`.

### C11. Navigation and store review (B6, B9). UNCLEAR

- `src/routes/(tabs)/_layout.tsx` shows the "Skift" tab for every signed-in
  user. When the server says Skiftlista is off for the person (including App
  Store review sessions, which get `enabled: false`), the tab opens to "Skiftlista
  er ikke tilgjengelig for deg ennå." HANDOFF describes the feature as "behind
  a server-side per-group switch", and the tab itself is not. A reviewer
  seeing an empty tab may flag it. Hiding the tab needs the config to be known
  before the tab renders.
- OTA and release (B6): anything we add that needs a native module goes in a
  store build, everything else by OTA. We added no dependency (`package.json`
  gained only two scripts, `package-lock.json` is unchanged), so the whole
  change is JS and fits OTA.

### C12. Docs and plans (B10, B11). PASS with a note

- README section added; Personal's docs updated in the other repo. PASS.
- PLANS.md asks for ExecPlans for larger work. None exists for Skiftlista in
  `plans/`; HANDOFF in Personal plays that role. Upstream has three plans in
  `plans/`, all small. UNCLEAR whether they would ask for one. Low.

### C13. Commit style (B15). FAIL (S)

- Upstream uses conventional prefixes (`feat:`, `fix(games):`, `ci:`) and PR
  numbers. Ours: six commits all starting `Skiftlista: ...` (one in
  Norwegian: `generert klient for Personal sitt skift-API`). All six carry
  `Co-Authored-By: Claude`; upstream commits do not. If upstream squashes,
  only the PR title matters.

### C14. Conflicts between HANDOFF and the app's guidelines

1. HANDOFF: "zod is not used" against B1 rule 4 (see C3).
2. HANDOFF: "behind a server-side per-group switch" against the tab always
   shown (see C11).
3. HANDOFF says the mockup is Nynorsk and the language is open; the app has no
   Nynorsk locale (see C8).
4. Safety, not conformance: in the app repo the `upstream` remote has a real
   push URL (`git@github.com:kvarteret/kvarteret-internbevis.git`), while in
   Personal it is `no_push`. HANDOFF says "pushes to upstream are blocked",
   which is true only for Personal. A mistyped `git push upstream` here would
   reach Kvarteret. Suggest `git remote set-url --push upstream no_push` (I did
   not run it; no changes allowed).

---

# D. Drift (both repos)

## D1. Position against the original's main

| Repo | Original's branch | Ahead | Behind | Merge base |
| --- | --- | --- | --- | --- |
| Personal | `upstream/develop` (tip `23077b6`, 2026-10-25) | 33 | 0 | `23077b6` (the tip) |
| App | `upstream/development` (tip `74eb289`, 2026-10-25) | 6 | 0 | `74eb289` (the tip) |
| App (for reference) | `upstream/main` (2026-05-10) | 54 | 1 | `c6371c9` |

Both repos were fetched read-only on 2026-11-02. Nothing new has landed
upstream since each merge base.

## D2. Upstream changes since the merge base that touch our files

- None: 0 commits behind in both repos, so no textual conflicts today.
- Upstream remote branches that are newer than the tips are only Dependabot
  branches in Personal (ruff 0.17.2, packaging 26.3, annotated-doc, pydantic
  2.14.1, markupsafe 3.0.4, last push 2026-10-28). They touch `uv.lock` and
  `pyproject.toml`, which we do not change, so they merge cleanly with us, but
  see "Risks": ruff 0.17 may flag our code (we lock 0.15.7).
- All other upstream branches are older than the tips and appear "ahead" only
  because of squash merges; I did not treat them as in-flight work.
- Where future upstream work is likely to collide with us (these are the shared
  files we touched where other features also add lines): `openapi.json`,
  `app/runtime.py`, `app/dependencies.py`, `app/db/tables.py`,
  `.importlinter`, `app/web/routes/__init__.py`, `app/templates/layouts/base.html`
  (all append-style edits, so conflicts are likely trivial but frequent);
  `app/domain/mobile_card/service.py`, `volunteers/repository.py`; in the app
  `translations.ts` and `SessionProvider.tsx`.

## D3. Alembic head against our chain

- Upstream `develop` has one head: `20261025_1200` (checked from the file
  contents with `git show`, not by running Alembic).
- Our chain starts with `down_revision = "20261025_1200"` and ends at
  `20261101_1200`, one head, 6 new revisions. No collision today.
- Collision rule: if upstream merges any new revision before us, there are two
  heads on `20261025_1200`, and `alembic upgrade head` fails until one side
  re-parents (our `20261027_1200` `down_revision` changes). Our ids are dated
  after upstream's head, so ordering by date still works, but the re-parent is
  needed. The Neon demo database is already at `20261101_1200`, so re-parenting
  must not change any revision id, only `down_revision` of the first file.

## D4. Lock files, generated OpenAPI and client, CI

- Locks: `uv.lock`, `bun.lock`, `requirements.txt`, `pyproject.toml`,
  `package.json` (Personal) and `package-lock.json` (app) are unchanged by us.
  No new dependencies. OR-Tools would be the first (HANDOFF open item); it would
  change `uv.lock` and `requirements.txt` and trigger the Vercel size question.
- `openapi.json` (Personal): additive only (D, section C6 in part 1);
  `--check` passes. The raw diff looks like 1,436 deleted lines because of the
  order of schemas.
- App client: stale by 8 form body types (C6 above).
- CI: workflows are identical to upstream in both repos (no change under
  `.github`). Differences in what CI will do with our code: Personal's
  `migrations` job runs our six migrations from an empty database, our two e2e
  files and `check_schema_drift.py`, none of which has run on Postgres since
  the schema-only change; the app's `api:check` does not cover our client.
- Environment differences: Personal's CI also runs `pip-audit` and `bun audit`
  (network); not run here.

## D5. The earlier merge conflict (H1)

Checked with the read-only form of `git merge-tree` (it prints the result and
writes nothing) between `skiftlista/skiftplan` (`80362cd`) and
`skiftlista/demo` (`4513caa`), merge base `cbaba3a`:

- The only file that conflicts now is `docs/skiftlista/HANDOFF.md` (changed on
  both sides). It is resolved by taking the skiftplan side, as the runbook says.
- H1 itself (`scripts/dev/seed_demo.py`, `tests/unit/test_seed_demo.py`) no
  longer conflicts: the demo branch already contains the resolution (`4513caa`
  is that merge). So H1 is not the only conflict: it is gone, and the only
  conflict left is the HANDOFF.md one that HANDOFF already mentions.
- `skiftplan` is 2 commits ahead of `demo` (docs only: `b10233b`, `80362cd` and
  the mockups); `demo` is 5 ahead (demo login, Render blueprint, PostHog switch,
  app code, two merges). The demo branch adds `app/auth/demo_*.py`,
  `render.yaml` and touches 15 shared files (`config.py`, `main.py`,
  `observability.py`, `runtime.py`, `feature_flags.py`, `login.html`, ...). None
  of that is in `skiftplan`, which is the intended separation.

---

# E. Footprint in shared code (what maintainers see first)

## E1. Personal: 17 hand-edited shared files, +247 / -10 lines, plus generated `openapi.json`

| File | Lines | What and why | Risk for maintainers |
| --- | --- | --- | --- |
| `.importlinter` | +1 | Adds `app.domain.shifts` to the independence list | None |
| `app/api/router.py` | +4 | Mounts `mobile_shifts` under `/api/v1/mobile-card/shifts`, tag `mobile-shifts` | New public API under their mobile-card prefix |
| `app/db/tables.py` | +28 | Registers 11 shift tables and 2 course tables in the aggregator | Alembic and drift check now see them |
| `app/dependencies.py` | +24 | Providers for the three services, plus `get_shift_scope` | None |
| `app/runtime.py` | +11 | Wires `ShiftsService`, `ShiftsSetupService`, `ShiftsAppService` | None |
| `app/web/routes/__init__.py` | +6 | Includes the three shift routers | None |
| `app/templates/layouts/base.html` | +4 | `shift-board.js` script tag; nav link "Skiftplan" for management users | Visible in every admin page |
| `app/domain/courses/tables.py` | +42 / -1 | Adds `course_sessions` and `course_session_signups` to the courses module | Courses module owns tables with no service code yet; a new table family in a module someone else owns |
| `app/domain/mobile_card/models.py` | +10 | `MobileCardSessionIdentity` dataclass | Small |
| `app/domain/mobile_card/service.py` | +16 | `authenticate_session()`: who a session token belongs to, for the new API | Touches the auth path the mobile app depends on (read-only, no behaviour change) |
| `app/domain/volunteers/repository.py` | +17 / -1 | `delete_volunteer` also deletes shift assignments, wishes, availability rules, course sign-ups, qualifications | Behaviour change in an existing destructive path; needed because of the new foreign keys |
| `app/shared/semester.py` | +22 / -1 | `get_previous_semester_code`, `get_semester_end_date` | Pure helpers |
| `scripts/dev/bootstrap.py` | +6 | Runs `seed_shifts` after the synthetic seed unless a real snapshot is used | Changes `kv start` output for all developers |
| `scripts/dev/seed_synthetic.py` | +5 / -2 | Semester list now uses `get_previous_semester_code` (fixes the `20253` code) | Bug fix in shared seed |
| `docs/reference/api-boundaries.md` | +47 / -2 | Skiftlista API section; consumer row; corrects the client generator sentence | Docs |
| `docs/how-to/update-openapi-clients.md` | +2 / -2 | Names `api:generate:personal` and `api:check:personal` | Docs |
| `.agents/skills/maintaining-api-boundaries/SKILL.md` | +2 / -1 | Notes the new generated client | Docs |
| `openapi.json` (generated) | +5,434 / -1,436 | Regenerated; additive only | Big diff, small change |

Not shared but visible: 59 new files (about 23,400 lines including 6 migrations,
about 7,000 lines of source and 6,000 lines of tests), `docs/skiftlista/` with
HANDOFF (118 KB), two mockups (55 KB) and this pass.

## E2. App: 8 hand-edited shared files plus 2 shared tests, +337 / -1 lines

| File | Lines | What and why | Risk for maintainers |
| --- | --- | --- | --- |
| `src/app/providers/SessionProvider.tsx` | +36 | `getSessionToken` and `renewSessionToken` on the context, with a shared in-flight renewal | The auth provider; adds public surface |
| `src/features/auth/data/authRepository.ts` | +10 | `renewStoredSessionToken()` reusing the refresh-token path | Same |
| `src/features/auth/data/__tests__/authRepository.test.ts` | +65 | Tests for it | None |
| `src/app/providers/__tests__/SessionProvider.test.tsx` | +116 (new) | Tests for the new context functions | None |
| `src/routes/(tabs)/_layout.tsx` | +6 | New "Skift" tab for signed-in users | Visible to all users (C11) |
| `src/routes/_layout.tsx` | +1 | Registers `shift/[slotId]` | None |
| `src/app/localization/translations.ts` | +84 | 42 keys in `no` and `en` | Merge conflict magnet |
| `package.json` | +2 | `api:generate:personal` and `api:check:personal` | None |
| `biome.json` | +1 / -1 | Excludes the generated Personal client from lint and format | None |
| `README.md` | +16 | Documents the Personal client | Docs |

Not shared: `src/features/shifts/` (38 files), two routes, the generator script
(121 lines) and 16 generated files (5,388 lines).

---

# Fix list

Size: S under half a day, M about a day, L several sessions.
"S1" in this list means the first server session of the wish calendar and
leader page package (HANDOFF "New order of work", item 2). Where I used
"before upstream PR" it means before anything is proposed to Kvarteret; the
pilot is Stjerne in the app.

| # | Repo | Item | Size | When |
| --- | --- | --- | --- | --- |
| 1 | Personal | `shift_week_status` and `shift_closed_periods` have no RLS and no REVOKE (C5). One new migration, same as `20261027`: enable RLS, revoke from `PUBLIC, anon, authenticated` | S | Before S1: put it in the package's first migration, so Neon runs it once before the demo merge |
| 2 | Personal | Index `ix_shift_closed_periods_group_id` breaks the `<table>_<cols>_idx` name (C5). `ALTER INDEX ... RENAME` in the same migration as item 1 | S | Before S1 (same migration), or never if you prefer not to touch Neon: a deliberate deviation worth one line in HANDOFF |
| 3 | Personal | `tables.py` lacks 10 CHECK constraints and 9 indexes that the migrations create (C4). Declare them in the metadata; declare new ones in the package from the start | M | New tables: before S1. Old ones: before upstream PR |
| 4 | Personal | `app/domain/shifts/app_service.py` is 1,276 lines, over the ~800-line cap (C2). Split by area (claim, mine, wishes, courses) and keep `ShiftsAppService` as a thin facade | M | Before S1: the package adds endpoints to this file |
| 5 | Personal | Postgres e2e tests (`tests/e2e/test_shift_qualifications_migration.py`, `test_copy_verv_to_qualifications.py`) have not been run (C7). Needs Postgres and the Supabase stubs from `run-tests.md`; Morten to run | S | Before S1, and before the Opus migration review |
| 6 | Personal | Inline `onclick="return confirm(...)"` at `app/templates/pages/shifts/shift_plan.html:154`; use `hx-confirm` like the other admin pages (C8) | S | Before the pilot |
| 7 | Personal | App-side events `wishes`, `swap request`, `kan ikke` are not audited with `app.audit` (C8). Add the same `logger.info("shift.app.<name>")` as `claim` | S | Before the pilot |
| 8 | Personal | Update `docs/explanation/kvarteret-personal-architecture.md` and `docs/README.md` (module list, route surfaces, ownership) (C11) | S | Before upstream PR |
| 9 | Personal | Decide what HANDOFF is (C13): `.agents/README.md` calls handoff notes temporary. Move durable parts to `docs/reference` and `docs/explanation`, keep the working log and "Notes for etjenesten (not sent)" on the fork only | M | Before upstream PR |
| 10 | Personal | No keyboard alternative to drag and drop on the board (C10); HANDOFF step 9 and the "Legg til" button in 5a2 | M | Before the pilot |
| 11 | Personal | Migration `20261029_1200` reads rows, so `alembic --sql` offline mode fails (C5) | S | Never: deliberate, note it in the migration docstring |
| 12 | Personal | Stored values in Norwegian (`vil/kan/nei`, slot kinds, qualifications) while "the schema speaks English" (C3) | S | Never: deliberate deviation; add one sentence to the architecture doc |
| 13 | Personal | Commit subjects mix four styles and add `Claude-Session` trailers (C12) | S | Before upstream PR: reword in the PR title if upstream squashes; otherwise never |
| 14 | Personal | Test and seed names: confirm none matches a real person (C7) | S | Before the pilot |
| 15 | App | Regenerate the Personal client: `npm run api:generate:personal` (C6). Optionally prune unused schemas so leader-form types do not ship | S | Before S1 (the package changes the API again) |
| 16 | App | Add `// Style escape hatch: computed width` above the `style` in `ProgressBar.tsx:27` (C4) | S | Before upstream PR |
| 17 | App | Zod at the repository layer, or a written deviation (C3) | M to add; S to document | Before the store build: decision for Morten (question 2) |
| 18 | App | Skift tab shows for everyone, including reviewers with `enabled: false` (C11) | M | Before the store build |
| 19 | App | `api:check:personal` is not in CI, so drift is invisible (C6) | S (check against the `develop` file over HTTPS) | Before upstream PR |
| 20 | App | Commit subjects (`Skiftlista: ...`, one Norwegian) against conventional prefixes (C13) | S | Before upstream PR (PR title if squashed) |
| 21 | App | `upstream` has a real push URL here (C14, item 4) | S | Now, by Morten: `git remote set-url --push upstream no_push` |
| 22 | App | English `en` strings not reviewed by a native speaker; no Nynorsk locale (C8) | S | Before the store build |
| 23 | App | The wish calendar will need text equivalents to colour dots (C9) | S | During the app calendar session |
| 24 | Both | Re-parent rule for Alembic if upstream adds a revision (D3) | S | When it happens |

# Risks for upstream

1. **HANDOFF.md as a file in their repo.** 118 KB, a runbook for a hosted demo
   on Neon and Render, unsent security notes about their own code (Finding E
   lists weaknesses in who can edit membership), and a conflict with
   `.agents/README.md`. This is the item most likely to draw push-back, and the
   one with the most downside. Keep it on the fork or split it.
2. **Volume.** About 23,700 new lines in Personal and 9,400 in the app, in one
   proposal. A maintainer who joined recently would not review that in one go.
   The seams are natural: tables and migrations, leader pages, app API, app UI.
3. **Shared-code touches that change behaviour** (E): `delete_volunteer` now
   deletes shift data, `seed_synthetic` semester fix, `bootstrap.py` runs the
   shift seed for all developers, a new admin nav item, new tables in the
   courses module, `authenticate_session` in the mobile-card service, extra
   public surface on the app's `SessionProvider`.
4. **Migrations.** Six new revisions with data logic in one of them, applied on
   Neon already. Two tables lack RLS, one index name is off-pattern, and the
   constraint declarations in `tables.py` are incomplete. A DB-minded reviewer
   will find all three quickly.
5. **Alembic collision** if upstream ships any migration before us (D3).
6. **CI first run.** Their Postgres job will run our e2e tests and migrations
   for the first time; the app's CI will not catch a stale generated client.
7. **Ruff 0.17.2** (Dependabot branch) might report new findings in our code;
   we lock 0.15.7 and I could not test 0.17.
8. **Size cap and module shape** (C2): a 1,276-line service against a written
   800-line cap; no workflow split under ADR-001.
9. **Rule 4 (Zod)** in the app (C3), the style comment (C4) and commit style
   (C13) are the three things the app's reviewer, who wrote those rules for
   junior contributors, will most likely cite.
10. **Product scope.** Skiftlista adds a leader surface and a volunteer tab to
    a card app and an admin tool; upstream may want an ADR or an issue first.
    HANDOFF's samfunnettjeneste plan (group types, exemptions, a new permission
    table) touches `groups` and rights, which belong to etjenesten.
11. **Hosting.** OR-Tools (plan A) would be a first new dependency and may hit
    Vercel size limits.

# Open questions for Morten

1. **What goes upstream?** (a) code, tests and reference docs only, with
   HANDOFF, mockups, demo and this pass staying on the fork; (b) everything.
   Recommended default: (a). Consequence of (a): a docs split (fix 9, M) and a
   clean story for maintainers; (b) is faster now but exposes unsent security
   notes and a Neon runbook, and conflicts with `.agents/README.md`.
2. **Zod in the app: add schemas or write a deviation?** Recommended default:
   write the deviation (precedent: the events client) and add a Zod parse only
   for the three most variable responses (config, open, mine) if reviewers ask.
   Consequence of adding Zod everywhere (M): rule-compliant and robust to new
   server values, but duplicate types next to the generated client. Consequence
   of deviation: S, but expect the question in review.
3. **Hide the Skift tab when Skiftlista is off?** Recommended default: yes,
   before the store build, using the config query (the tab appears only when
   `enabled`). Consequence of yes: the tab is absent for reviewers and for
   groups not switched on, but the tab layout waits for one request. Consequence
   of no: reviewers see an empty tab with "ikke tilgjengelig", a plausible
   rejection reason under placeholder-content guidelines.
4. **Split `app_service.py` before S1?** Recommended default: yes (fix 4).
   Consequence of yes: one extra session now, easier reviews and tests for the
   package. Consequence of no: the package adds roughly 300 more lines and the
   split becomes a larger, riskier change later.
5. **Bundle RLS, the index rename and the missing constraints into the package's
   first migration?** Recommended default: yes (fixes 1 to 3). Consequence of
   yes: one Neon run covers it all; the rename needs `ALTER INDEX` on a table
   with demo data only. Consequence of no: three small migrations later, or an
   exception for each written into HANDOFF.
6. **App language:** keep Bokmal plus English (what the app has) or add Nynorsk
   because the mockup uses it? Recommended default: Bokmal and English, and
   change the mockup text, since a new locale touches every screen and the
   store texts. Consequence of adding Nynorsk: a new `nn` catalog for the whole
   app, not only Skiftlista, and a language switch in settings.

# What I could not verify

- Postgres behaviour: the Postgres e2e tests, `scripts/check_schema_drift.py`
  and `alembic upgrade` on a real database were not run (no Postgres allowed).
  The column comparison in part 1 used offline SQL up to `20261028_1200` and a
  hand read of the last three migrations; types, defaults and nullability were
  not compared one by one.
- `pip-audit` and `bun audit` (need the network) and the app's `api:check`
  for the events client (needs the Samfunnet OpenAPI URL).
- Upstream CI itself, and whether it runs on a pull request from a fork;
  whether upstream squashes or merges; which branch an app PR should target
  (`development` looks right, `main` is older).
- Visual and behavioural checks: nothing was run in a browser or a device;
  accessibility with a screen reader; the Skift tab on a real build.
- Whether any name in seeds or tests belongs to a real person.
- `ty` (a Personal dev dependency) was not run; CI does not run it.
- Ruff 0.17.x against our code.
- The style script in the app depends on `rg`, which is not installed here; I
  grepped by hand for the two patterns in `src`.
- Upstream maintainers' preferences (ADR for a new module, ExecPlan, size
  limits). My reading of "~800-line cap" and "Zod rule" is from their docs, not
  from them.
- `docs/skiftlista/design-pass-wishes.md` was only skimmed (first 80 lines), as
  asked; HANDOFF was read in full, and it contains no section called "Design
  pass results".

# Changes made

None in either repository. `git status --short` at the end: Personal shows only
`?? docs/skiftlista/design-pass-wishes.md`, which was already untracked at the
start; the app repo is clean. Fetches updated only remote-tracking refs of
`upstream`. Temporary files (generated SQL, a regenerated client, a filtered
spec) are in the session scratchpad, outside both repos. The only file written
for the report is `~/conformance-pass.md`.

Claude-Session: e370ff55-10d4-44e4-83fc-15b8657597a2
