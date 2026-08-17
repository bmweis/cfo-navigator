# Runbook

Operational procedures for bmweis.com — written to be followed under stress,
by Brian or by an agent acting for him. Every command here was verified
against the code as of July 2026; the restore path was rehearsed end-to-end
(see the last section for the transcript).

Related reading: `ARCHITECTURE.md` (deployment map, auth model),
`CLAUDE.md` (env-var table, deploy flow).

---

## 1. Restore `library.db` from a Google Drive snapshot

**When:** the production database on the Railway volume is corrupt, was
accidentally bulk-deleted from, or the volume was lost/recreated.

**What you're restoring from:** the weekly off-site snapshots in Google
Drive, named `library-YYYYMMDD-HHMMSS.db`. They're produced by
`linklib/backup.py::maybe_backup` (debounced to once per 168 h, tracked by a
`.last_backup` marker beside the DB) using SQLite's online backup API, so
every snapshot is a consistent, self-contained file — no WAL sidecar needed.
They land in a Drive folder named **"CFO Navigator — Library Backups"**,
owned by the Workspace account behind `GOOGLE_OAUTH_REFRESH_TOKEN`. The app
creates this folder itself on the first successful backup and remembers its
id in the `settings` table (`backup_drive_folder_id`) — it deliberately
doesn't target a folder made by hand in the Drive UI, since the refresh
token is minted with the narrow `drive.file` scope, which can only see
files/folders the app created via the API (a hand-made folder 404s no
matter how correct its id is — see `linklib/backup.py`'s module docstring
for the full story). `/admin/library/backup` shows a live link to the
current folder. `GOOGLE_DRIVE_FOLDER_ID`, if set, overrides this and takes
priority — normally left unset. The override is read fresh on every backup
attempt (not cached at startup), so setting or clearing it in Railway takes
effect on the very next attempt — no redeploy needed.

### Path A — the app is up (normal case)

The app has a built-in restore endpoint: `POST /admin/library/backup/upload-db` validates
the upload is a real library DB, then swaps it onto the volume atomically
and clears stale WAL/SHM sidecars. **No restart or redeploy is needed** —
the app opens a fresh DB connection per request, so the very next request
reads the restored file.

1. **Snapshot the current state first**, even if it's damaged — it may hold
   saves newer than the Drive snapshot that you'll want to merge back later:

   - Browser: log in as admin → `/admin/library/backup` → **Download library.db**
     (or hit `/admin/library/backup/download-db` directly).

2. **Download the snapshot from Google Drive** you want to restore
   (normally the newest `library-*.db`).

3. **Upload it.** Either use the upload form on `/admin/library/backup` (admin
   login), or from a terminal:

   ```bash
   curl -si -X POST "https://bmweis.com/admin/library/backup/upload-db" \
        -H "X-Save-Token: $LINKLIB_SAVE_TOKEN" \
        -F "file=@library-YYYYMMDD-HHMMSS.db"
   ```

   Expect `HTTP/1.1 303 See Other` with
   `location: /admin/library/backup?uploaded=<N>` — **N is the article count the
   server found in the uploaded file** (it runs
   `SELECT COUNT(*) FROM articles` before swapping anything). Sanity-check
   it: production should be ~1,500+. A `400` means the file didn't parse as
   a library DB and **nothing was touched** — the live DB is only replaced
   after validation, via an atomic `os.replace`.

4. **Validate** (see the checklist below).

### Path B — the app is down or won't boot

If the DB is so broken the app crashes on boot (rare — the schema script is
additive and re-runnable), Path A's endpoint isn't reachable and you need a
shell on the volume:

1. In the Railway dashboard, confirm the crash is DB-related from the deploy
   logs before touching anything.
2. Use the Railway CLI to get a shell in the service container
   (`railway ssh`, after `railway link` to the project — check
   `railway --help` for the current incantation; the CLI changes more often
   than this file).
3. On the volume (the directory `LINKLIB_DB` points at): move the broken
   file aside (`mv library.db library.db.broken`), don't delete it. Also
   move aside `library.db-wal` / `library.db-shm` if present.
4. Get the snapshot onto the volume. Easiest: restart the service so the app
   boots against the now-empty path (it creates a fresh schema), then run
   Path A's upload. Transferring the file directly over the shell works too
   but depends on what the CLI supports that month.

### Post-restore validation checklist

- [ ] `https://bmweis.com/health` returns `{"ok": true}`
- [ ] Log in as admin, open `/read?view=saved` — article count and recent items look right
- [ ] FTS search works (search something specific on `/read?view=saved`, or
      `GET /api/search?q=netsuite` with the token) — the FTS index travels
      inside the DB file, so if the file is good, search is good
- [ ] `/admin/contacts`, `/admin/library/queue` load (spot-check non-article tables)
- [ ] If you restored an older snapshot: diff against the step-1 download
      for member saves / contacts / ask history created since the snapshot,
      and re-add anything worth keeping (article re-saves are idempotent —
      URL is the natural key)
- [ ] Trigger a fresh off-site snapshot: `POST /admin/backup-now`
      (admin cookie or `?token=`), so Drive holds a copy of the restored
      state. Note the weekly auto-backup won't fire on its own right away if
      the `.last_backup` marker on the volume is recent — but the weekly
      GitHub Action (`.github/workflows/backup.yml`, Phase O) bypasses that
      debounce, so it isn't the only path back to a fresh snapshot.
- [ ] Confirm that snapshot on `/admin/library/backup` — the status banner
      should read green with this restore's timestamp, and the history table's
      top row should show `status=success` with a row count matching what you
      just validated above (not just that the request returned 200).

---

## 2. Rotate `LINKLIB_SAVE_TOKEN` (and re-grab the bookmarklet)

**When:** the token leaked (it's embedded in plaintext in the bookmarklet
JS, so treat any exposure of the bookmarklet snippet as a leak), or on
general hygiene grounds.

**Know the blast radius before you rotate.** The token is not just the
bookmarklet's secret; two other env vars *fall back to it* when unset:

| If this is unset in Railway… | …then rotating the save token also |
|---|---|
| `LINKLIB_PASSWORD` | **changes the break-glass admin login password** (it *is* the save token) |
| `LINKLIB_SECRET_KEY` (when `LINKLIB_PASSWORD` is also unset) | **invalidates every member session** (cookie-signing key falls back to the password) — everyone just logs in again, no data impact |

Member accounts in the `users` table are unaffected either way — their
passwords are their own (scrypt, in the DB).

### Steps

1. Generate a new token:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. In the Railway dashboard → service → **Variables**, set
   `LINKLIB_SAVE_TOKEN` to the new value. Saving variables triggers a
   redeploy; the old token stops working the moment the new deploy is live.
   (If you want the login password decoupled from the token going forward,
   this is the moment to set `LINKLIB_PASSWORD` and `LINKLIB_SECRET_KEY`
   explicitly too.)

3. **Re-grab the bookmarklet** — the old one embeds the old token and is now
   dead. Log in → `/bookmarklet` → copy the snippet → replace the browser
   bookmark on every machine that has one. (Same drill if
   `LINKLIB_PUBLIC_BASE` ever changes — the base URL is baked in too.)

4. **Update every other place the token lives:**
   - The MCP server config for Claude Desktop / Claude Code
     (`scripts/mcp_server.py` reads `LINKLIB_SAVE_TOKEN` from its env —
     it's set in the client's MCP config JSON).
   - The GitHub repo secret backing the weekly backup Action
     (`.github/workflows/backup.yml`, Phase O) — Settings → Secrets and
     variables → Actions → `LINKLIB_SAVE_TOKEN`. Miss this and the Action
     starts failing with `401` on the next scheduled run, silently, until
     someone checks the Actions tab or `/admin/library/backup`'s status
     banner shows a stale "last successful backup."
   - Any personal shell exports / scripts that call `/save`, `/api/search`,
     or `/ask` with `X-Save-Token` or `?token=`.

5. Verify: old token gets `401` on `POST /save`; new token saves fine
   (click the new bookmarklet on any article page).

---

## 3. Railway is down — triage

The site is one FastAPI process on Railway behind Cloudflare. Cloudflare
does not cache HTML (stock cache config), so if the origin is down, the
site is down — there is no "stale but serving" mode. The good news: the
data is two-way safe (Railway volume + weekly Drive snapshots), so an
outage is availability, not data loss.

Work down this list; each step splits the problem in half.

1. **Is it the edge or the origin?** The legacy Railway hostname bypasses
   Cloudflare entirely:

   ```bash
   curl -s https://bmweis.com/health          # through Cloudflare
   curl -s https://<service>.up.railway.app/health   # straight to Railway
   ```

   (The exact `*.up.railway.app` hostname is on the service's Settings →
   Networking page.)

   - **Railway URL works, bmweis.com doesn't** → it's Cloudflare or DNS:
     check <https://www.cloudflarestatus.com>, then the Cloudflare
     dashboard (SSL/TLS mode should be **Full** — Full (strict) is known to
     break during Railway cert-renewal windows; that's why it's Full).
     A 52x error page *from Cloudflare* also means the edge is fine and the
     origin isn't — go to step 2.
   - **Both fail** → it's Railway or the app: continue.

2. **Is it Railway itself?** Check <https://status.railway.com>. If there's
   a platform incident, wait it out — don't redeploy into an incident.

3. **Is it the deploy?** Railway dashboard → service → **Deployments**:
   - A deploy failed or is crash-looping → open its logs. The healthcheck
     is `GET /health` (wired in `railway.toml`) — a deploy that never goes
     healthy usually means the app is crashing on boot, and the log will
     say why (import error, missing module after a requirements change,
     bad env var).
   - Last deploy was fine but a new one just went out and broke → use
     Railway's **rollback** to the previous deployment. Then fix on a
     branch and ship a PR like normal.

4. **Is it the environment?** Variables page: was anything changed right
   before the outage? (A variable save triggers a redeploy — a typo'd
   `LINKLIB_DB` pointing away from the volume makes the app boot against an
   empty DB, which looks like "all my data is gone" but isn't. Point it
   back at the volume path.)

5. **Is it the volume?** Service settings → volume attached and mounted at
   the path `LINKLIB_DB` expects? A full volume makes SQLite writes fail
   (reads may still work — symptom: saves error, browsing works).

6. **Still stuck?** Redeploy `main` from the dashboard (it's the source of
   truth — everything live shipped through it). If the DB itself is the
   problem, go to section 1. If Railway is hard-down for an extended
   period, the recovery story is: repo (GitHub) + latest Drive snapshot +
   env vars (re-enter from `.env.example`'s list) on any host that runs a
   Dockerfile — nothing about the app is Railway-specific.

**After any recovery:** hit `/admin/checks` (mirrors CI) and click through
`/read`, `/tools/fpa-buddy` once. Then check `/admin` for
email-failure badges — outbound email during the outage will have landed in
`email_failures` rather than vanishing.

---

## 4. Restore rehearsal — procedure and July 2026 record

Rehearse the restore roughly yearly (or after any change to
`linklib/backup.py` / `/admin/library/backup/upload-db`) so section 1 stays a checklist,
not a theory. The rehearsal never touches production — it's the same code
paths against scratch files.

### Procedure (local, ~10 minutes)

1. Build two scratch DBs with `linklib.db.Library`: a "good" one (a handful
   of articles — this stands in for the healthy DB that was backed up) and
   a "live" one (fewer/different rows — stands in for the damaged volume
   DB).
2. Produce the "Drive snapshot" from the good DB with
   `linklib.backup.snapshot_to_file()` — the exact function the weekly
   upload uses, so the artifact is byte-for-byte what Drive would hold.
3. Run the app against the live DB:
   `LINKLIB_DB=.../live.db LINKLIB_SAVE_TOKEN=<anything> uvicorn webapp.app:app --port 8123`
4. Confirm the pre-restore state through the API
   (`GET /api/search?q=&limit=50&token=…` shows only the live DB's rows).
5. Restore with section 1's exact curl (`POST /admin/library/backup/upload-db`).
6. Validate: the 303 redirect's `uploaded=<N>` matches the good DB's
   article count; `/api/search` now returns the snapshot's rows (including
   an FTS query that missed before); the stale row is gone; on the file
   itself, `PRAGMA integrity_check` says `ok` and
   `INSERT INTO articles_fts(articles_fts) VALUES('integrity-check')`
   doesn't raise; `/health` still 200s — all **without restarting the app**.

### Record: rehearsal run 2026-07-11 ✅

- Good DB: 5 articles; live DB: 2 articles (1 shared, 1 stale placeholder).
- Snapshot via `snapshot_to_file()` → 233,472-byte self-contained file, no
  WAL sidecar.
- Pre-restore: API listed 2 rows; FTS query `netsuite` → 0 hits.
- `POST /admin/library/backup/upload-db` with `X-Save-Token` → `303`,
  `location: /admin/library/backup?uploaded=5` (count matched the snapshot).
- Post-restore, **no restart**: API listed the snapshot's 5 rows; `netsuite`
  → 1 hit; stale row unfindable; `PRAGMA integrity_check` = `ok`; FTS
  self-check passed; `/health` → `{"ok": true}`.
- Conclusion: the in-app restore path works end-to-end exactly as section 1
  documents it.

---

## 5. Refresh an expired paywall cookie

**When:** a paid source stops returning full text. The archive keeps saving
those articles, but with a preview instead of the body, so enrichment and
FP&A Buddy quietly get less to work with. Nothing breaks loudly—that's why
the check below exists.

**Cadence:** every few months, whenever a cookie ages out.

### 5.1 Notice it

Two places surface it, both fed by the same stored record
(`authcheck.check_auth_cookies` → `settings.auth_cookie_status`):

- **`/admin/library/feeds`**—the primary surface. With everything healthy
  you see only a compact **Re-check subscriber access** button, nothing else.
  When a cookie has actually gone stale, that button is absorbed into a coral
  **"Subscriber cookie expired"** panel carrying a status line per domain and
  an abbreviated version of 5.2–5.3 inline.
- **The Reader**—its own banner, posting to the same
  `POST /admin/auth/recheck`.

The page kicks a background re-check when the stored status is missing or
older than 12 hours, so just loading it is usually enough. Press the button
to force one.

Each domain reports one of three states:

<!-- The `detail` strings below are copied verbatim from authcheck.check_auth_cookies
     so they match what the panel actually prints. The spaced em dash in the
     "expired" row is the code's own; don't normalize it. -->

| State | `detail` reads | Meaning |
|---|---|---|
| **working** | `full text fetched (N chars)` | Cookie is good. |
| **expired** | `got a preview/paywall — cookie missing or expired` | **This runbook.** |
| **untested** | `no recent post found to test` | The probe found no post to check. Not a cookie failure—see 5.5. |

> **Only domains listed in `LINKLIB_AUTH_COOKIES` are probed at all.** The
> probe list comes from that variable's keys, not from
> `feed.PAYWALLED_DOMAINS`. A paywalled feed with no cookie configured is
> silently never checked—it just never returns full text. If a source you
> expect to see is absent from the panel entirely, that's the reason, and the
> fix is to add it in 5.3 rather than to refresh anything.

### 5.2 Get a fresh cookie value

Do this in a normal browser profile where you're a paying subscriber.

1. Log into the site normally and open a post you can read in full.
2. Open DevTools → **Network**, then reload the page.
3. Click the top-level document request to that domain.
4. Under **Request Headers**, find `Cookie:` and copy **the entire value**—everything
   after `Cookie: `, semicolons and all.

**Copy the whole header rather than hunting for the one session cookie.**
`extract._cookie_for` sends the stored string as the `Cookie` header
verbatim, so a full copy is both correct and the reason you don't need to
know which individual cookie carries the session. This is also exactly what
the expired banner tells you on screen—the two are deliberately the same
procedure.

> **On the specific cookie name—unconfirmed.** `extract._auth_cookies`'s
> docstring shows `{"mostlymetrics.com": "substack.sid=..."}`, but treat that
> as an illustrative example, not a verified fact: Mostly Metrics is a
> **beehiiv** publication, not Substack, so `substack.sid` is unlikely to be
> its real cookie name. A search of git history turns up only the commit that
> introduced the feature (`43cd00b`), with no record of how the value was
> originally obtained. Copying the full header (step 4) sidesteps the question
> entirely. **If you do identify the real per-domain cookie names the next
> time you run this, write them down here**—that's the gap this note marks.
> DevTools → **Application** → **Cookies** → the domain lists them
> individually if you want to look.

### 5.3 Update `LINKLIB_AUTH_COOKIES`

One JSON object, domain → full `Cookie` header value:

```json
{"mostlymetrics.com":"cookie1=abc; cookie2=def","stratechery.com":"...","blog.publiccomps.com":"..."}
```

Key rules, from `_auth_cookies` and `_cookie_for`:

- **Bare registrable domain.** No scheme, no path, no `www.`—the lookup
  strips `www.` from the URL's host before matching.
- Keys are lowercased and a leading `.` is stripped, so `.Example.com` and
  `example.com` are equivalent.
- Subdomains match automatically (`host == dom or host.endswith("." + dom)`),
  so `mostlymetrics.com` also covers `www.mostlymetrics.com`. But
  `blog.publiccomps.com` is keyed as-is, because that's the host the feed
  actually uses.
- **The whole variable is one JSON object.** Editing one domain means editing
  the object, not appending. Malformed JSON fails *silently*—`_auth_cookies`
  catches the parse error and returns `{}`, which reads downstream as "no
  cookies configured at all," and the panel disappears rather than turning
  red. If the banner vanishes after an edit, suspect a JSON typo first.

In Railway: the `cfo-navigator` service → **Variables** → edit
`LINKLIB_AUTH_COOKIES` → save.

**Restart:** not something you have to think about. `_auth_cookies()` reads
`os.environ` on every call with no caching, so a new value is picked up by the
next fetch; and Railway redeploys the service on a variable change anyway.
Wait for that deploy to finish before verifying, or you'll be testing the old
container.

### 5.4 Verify

1. Open `/admin/library/feeds`.
2. Press **Re-check subscriber access** (don't rely on the 12-hour
   auto-refresh—you want a probe against the new value, now).
3. The domain should flip to **working**, with `full text fetched (N chars)`.
   The coral panel disappears once no domain is left in the expired state.

If it still reads expired: the cookie was probably copied from a logged-out
session or a different browser profile. Redo 5.2 after confirming you can
actually read a full post in that same profile.

### 5.5 If it reports "untested"

`no recent post found to test` means the probe couldn't find a post URL to
check—not that the cookie failed. `authcheck._recent_post_url` tries the
OPML feed first, then falls back to the site's sitemap. Both coming up empty
usually means the feed URL is wrong or the source was down at probe time.
Check the feed in `/admin/library/feeds`, then re-check. The cookie may well
be fine.
