---
name: verifying-web-changes
description: Check shared styling and state preservation when changing Jinja UI, HTMX swaps, or Alpine components.
---

# Verifying Web Changes

- Reuse `app/templates/components/shared/ui_macros.html` for shared page chrome,
  form fields, notices, and interactive fragments; follow existing component
  styling.
- For HTMX changes, check the swap target and whether replacing it destroys
  state that should persist.
- For Alpine changes, keep state within its owning component and check its
  behavior after HTMX swaps.
