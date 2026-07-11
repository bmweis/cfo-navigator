---
name: verify
description: How to launch and drive this app end-to-end for runtime verification of a change.
---

# Verifying changes by running the app

## Launch

The app is one FastAPI process; point it at a throwaway DB and it seeds its
own schema on boot:

```bash
export SCRATCH=$(mktemp -d)
LINKLIB_DB="$SCRATCH/verify.db" LINKLIB_PASSWORD=adminpass \
  LINKLIB_SAVE_TOKEN=tok-secret LINKLIB_SECRET_KEY=k \
  python -m uvicorn webapp.app:app --port 8600 &
curl -s http://localhost:8600/health   # {"ok":true}
```

Leave `ANTHROPIC_API_KEY` unset: `POST /ask` then takes a deterministic
no-key fallback path that still records turns — every flow is drivable
without spending API money.

## Seed data

Create users and Ask turns directly through `linklib.db.Library` against the
same DB file before (or while) the server runs — e.g. `create_user(...)`,
`record_ask_question(...)` (pass `conversation_id=""` on turn one, then the
returned `str(id)`), `record_ask_feedback(...)`.

## Drive

- HTTP: log in with `curl -c jar -d "username=...&password=..."
  /login`, then send cookie-jar requests. Token auth: `X-Save-Token` header.
- Browser: Python Playwright works with the pre-installed Chromium via
  `p.chromium.launch(executable_path="/opt/pw-browsers/chromium")` — do NOT
  run `playwright install`. Log in by filling `input[name=username]` /
  `input[name=password]` on `/login`.
- Mobile checks: new context with `viewport={"width": 390, "height": 844}`
  (portrait) / `844×390` (landscape); assert
  `document.documentElement.scrollWidth <= clientWidth`.

## Gotchas

- All HTML/JS is inline in `webapp/app.py` f-strings — JS braces are doubled
  (`{{`), so a page that 500s on render is usually a brace-escaping slip.
- The app reads env at import time; tests reload `webapp.app` after setting
  env, and a running server needs a restart to pick up env changes.
