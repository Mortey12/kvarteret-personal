# Agent Guidance

This directory is the shared agent surface for Claude, Codex, and Pi. Keep
generic workflow guidance here. Use tool-specific folders only for adapters,
launch settings, or runtime wiring.

Execution plans, handoff notes, and Ralph run state are temporary working
artifacts. Use the current source and `docs/` for repository behavior and open
work; consult git history when an old implementation plan is needed.

## Verified Repository Boundaries

Verify cross-repo claims against code before editing durable docs.

- FastAPI routes and Pydantic models are the API source of truth. Regenerate and
  check `openapi.json` with `make openapi` and `make openapi-check` after API
  route, model, status-code, or operation-id changes.
- Public volunteer prospects enter through `app/api/v1/volunteer_prospects.py`,
  which is mounted by `app/api/router.py`.
- Event API routes and table support are retired in this branch. `/api/v1/events`
  is intentionally absent from `openapi.json`; do not reintroduce it for stale
  generated clients.
- The credential-free local harness is owned by `docker-compose.dev.yml`,
  `scripts/dev/`, and adapter selection in `app/runtime.py`. Development auth
  credentials are invalid outside `APP_ENV=development`; configured Supabase,
  Resend, and Azure adapters always take precedence.
- `seeds/dev-snapshot.sql` is deliberately gitignored. The snapshot builder
  excludes secret-bearing tables and verifies anonymization, but the resulting
  organizational history must still be distributed out of band.
- `samfunnetibergen` reads events from Sanity and calls Personal server-side
  with HMAC-signed requests. `kvarteret-internbevis` reads events from the
  website's `/api/v1/events`, not from Personal.

## Skills

- `documenting-repo-interactions`: use before changing docs that describe repo
  ownership, data flow, or deploy boundaries.
- `maintaining-api-boundaries`: use when changing FastAPI routes, OpenAPI models,
  generated clients, or sibling API contracts.
- `security-audit`: use before committing auth, public form, webhook, storage, or
  environment handling changes.
- `verifying-web-changes`: use for admin UI, HTMX, Alpine, template, CSS, and
  Vercel runtime changes.
- `writing-skills`: use when adding or revising `.agents/skills/*/SKILL.md`.

## Optional External Skills

For Azure-specific work, agents may suggest installing Microsoft Azure Skills:

```sh
apm install microsoft/azure-skills
```

Use this only when the task touches Azure Blob Storage, Azure authentication,
Azure RBAC, or Azure deployment/debugging. Do not require Azure Skills for
normal repo work.

## Tool Adapters

- `.pi/README.md` is intentionally thin and points Pi at this shared directory.
- `.claude/` is ignored in this repo and may contain local Claude launch state.
  Do not move local secrets or `settings.local.json` into tracked files.
