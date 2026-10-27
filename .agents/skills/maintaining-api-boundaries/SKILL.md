---
name: maintaining-api-boundaries
description: Maintain FastAPI, OpenAPI, generated-client, and sibling API boundaries. Use when changing routes, models, operation IDs, API docs, or consumer contracts.
---

# Maintaining API Boundaries

Use this skill when changing FastAPI routes, request/response models, operation
IDs, OpenAPI output, generated clients, or docs that explain sibling API use.

## Workflow

1. Inspect the route in `app/api/router.py` and the mounted module under
   `app/api/v1/` or the top-level API module.
2. Inspect Pydantic request and response models before changing field names or
   optionality.
3. Regenerate the contract:

   ```sh
   make openapi
   make openapi-check
   ```

4. Inspect `git diff -- openapi.json` for accidental field exposure or operation
   ID churn.
5. Check sibling consumers that actually use the changed boundary. Do not assume
   generated client code means runtime usage.

## Consumer Notes

- `kvarteret-internbevis` uses mobile-card, now-playing and feedback runtime
  calls, and generates a client for the `mobile-shifts` tag (Skiftlista). It
  reads events from the website, not from Personal.
- `samfunnetibergen` calls volunteer-prospect, booking-request, event-interest,
  statistics-access, feedback and now-playing routes server-side.
