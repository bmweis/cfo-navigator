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

**What you're restoring from:** the daily (bumped from weekly, 2026-08) off-site
snapshots in Google Drive, named `library-YYYYMMDD-HHMMSS.db`, retained per
`linklib.backup.prune_old_backups()`'s policy (most recent 14 unconditionally,
plus one per week for 8 further weeks, everything older deleted). The daily
cadence comes from a Railway Cron Service in the same project (§7 below),
the primary trigger — the in-app `linklib/backup.py::maybe_backup` bonus trigger
is still separately debounced to once per 168 h, checked against the most
recent success recorded in `backup_log` (2026-09: a standalone `.last_backup`
marker file used to track this instead — retired because the file only
updated on this debounce's own successful runs, not on the daily cron's, so
it silently went stale relative to what was actually happening; `backup_log`
is already the real record of every attempt, so the debounce now reads that
directly). Every snapshot is produced via SQLite's
online backup API, so it's a consistent, self-contained file — no WAL sidecar
needed.
They land in a Drive folder named **"CFO Navigator — Library Backups"**,
owned by the Workspace account behind `GOOGLE_OAUTH_REFRESH_TOKEN`. The app
creates this folder itself on the first successful backup and remembers its
id in the `settings` table (`backup_drive_folder_id`) — it deliberately
doesn't target a folder made by hand in the Drive UI, since the refresh
token is minted with the narrow `drive.file` scope, which can only see
files/folders the app created via the API (a hand-made folder 404s no
matter how correct its id is — see `linklib/backup.py`'s module docstring
for the full story). `/admin/library-backup` shows a live link to the
current folder. `GOOGLE_DRIVE_FOLDER_ID`, if set, overrides this and takes
priority — normally left unset. The override is read fresh on every backup
attempt (not cached at startup), so setting or clearing it in Railway takes
effect on the very next attempt — no redeploy needed.

### Path A — the app is up (normal case)

> **Path A cannot restore the production database any more.** bmweis.com is
> behind Cloudflare, which rejects request bodies over 100 MB on the Free plan,
> and `library.db` is about 254 MB. The 2026-10-06 rehearsal ran for about 55
> minutes and the app never logged the POST (§4). Path A still works for a
> database under 100 MB (a scratch rehearsal, a fresh start). For production,
> use **Path C**.

The app has a built-in restore endpoint: `POST /admin/library-backup/upload-db` validates
the upload is a real library DB, then swaps it onto the volume atomically
and clears stale WAL/SHM sidecars. **No restart or redeploy is needed** —
the app opens a fresh DB connection per request, so the very next request
reads the restored file.

1. **Snapshot the current state first**, even if it's damaged — it may hold
   saves newer than the Drive snapshot that you'll want to merge back later:

   - Browser: log in as admin → `/admin/library-backup` → **Download library.db**
     (or hit `/admin/library-backup/download-db` directly).

2. **Download the snapshot from Google Drive** you want to restore
   (normally the newest `library-*.db`).

3. **Upload it.** The route accepts an **admin session only**: the save token
   (`X-Save-Token` or `?token=`) is refused with `401`, as is a non-admin
   login. Use the upload form on `/admin/library-backup` (admin login), or from
   a terminal reuse an admin session cookie (the `cfo_session` value from your
   browser after logging in):

   ```bash
   curl -si -X POST "https://bmweis.com/admin/library-backup/upload-db" \
        -H "Cookie: cfo_session=<your admin session cookie>" \
        -F "file=@library-YYYYMMDD-HHMMSS.db"
   ```

   Expect `HTTP/1.1 303 See Other` with
   `location: /admin/library-backup?uploaded=<N>` — **N is the article count the
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
4. Get the snapshot onto the volume with Path C's script (it works with the
   destination missing, so no restart or empty schema is needed first). Path A's
   upload does not work at production size.

### Path C — restore from Drive on the container (works at any size)

`scripts/restore_from_drive.py` runs on the Railway container, pulls the
snapshot from Drive with the same OAuth variables the daily backup uses, checks
it, and swaps it onto the volume. Nothing passes through Cloudflare or your
home connection. It works whether the app is up or down.

```bash
railway ssh                      # then, inside the container:
cd /app
python -m scripts.restore_from_drive --db /data/library.db --list
python -m scripts.restore_from_drive --db /data/library.db --dry-run
python -m scripts.restore_from_drive --db /data/library.db --yes-replace-live
```

- `--list` prints each snapshot's name, size, date and, when the live database
  still opens, its article count. Without `--snapshot NAME_OR_ID` the script
  uses the newest.
- `--dry-run` downloads and validates (size and md5 against Drive,
  `PRAGMA integrity_check`, the FTS5 self-check, article count) and writes
  nothing to the destination. Run it first.
- Without `--yes-replace-live` it refuses to replace a destination that exists.
  A destination that does not exist (a scratch path, or a lost volume) needs no
  flag.
- Before swapping it keeps the current file as
  `/data/library.db.pre-restore-<timestamp>` (a hard link, so no extra space; a
  full copy if the volume cannot link) and never deletes it. Delete it by hand
  once the restore is confirmed.
- **Free space needed:** the snapshot size plus 16 MB (about 270 MB today), in
  the same directory as the destination. If links are unsupported, add the size
  of the current database. The script refuses before downloading if it is short.
- It validates before swapping, uses `os.replace`, and removes `-wal` and `-shm`,
  the same as the old upload route. No restart is needed.
- The script needs `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and
  `GOOGLE_OAUTH_REFRESH_TOKEN` in the container's environment. Check with
  `railway ssh` then `env | grep -c GOOGLE_OAUTH` (expect 3).
- The Drive folder id normally lives in the database's `settings` table. If
  the database is gone, the script finds the folder by name instead. If it
  reports more than one match, pass `--folder-id`.
- It does not run the post-restore checklist below. Do that by hand.

### Post-restore validation checklist

- [ ] `https://bmweis.com/health` returns `{"ok": true}`
- [ ] Log in as admin, open `/read?view=saved` — article count and recent items look right
- [ ] FTS search works (search something specific on `/read?view=saved`, or
      `GET /api/search?q=netsuite` with the token) — the FTS index travels
      inside the DB file, so if the file is good, search is good
- [ ] `/admin/inbox/contact-submissions` loads (spot-check non-article tables) — the
      Archive Queue itself (`/admin/library/queue`) was retired in 2026-09 (PR 3), so
      there's no longer a queue page to check here
- [ ] If you restored an older snapshot: diff against the step-1 download
      for member saves / contacts / ask history created since the snapshot,
      and re-add anything worth keeping (article re-saves are idempotent —
      URL is the natural key)
- [ ] Trigger a fresh off-site snapshot: `POST /admin/backup-now`
      (admin cookie or `?token=`), so Drive holds a copy of the restored
      state. Note the in-app `maybe_backup` bonus trigger won't fire on its
      own right away if `backup_log`'s most recent success is still within
      its 168h debounce window — but the daily Railway Cron Service (§7,
      Phase O) bypasses that debounce, so it isn't the only path back to a
      fresh snapshot.
- [ ] Confirm that snapshot on `/admin/library-backup` — the status banner
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
| `LINKLIB_PASSWORD` | nothing about login (the shared-secret login was retired, issue #627); it still turns auth on and is the fallback cookie-signing key |
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
   - The stdio MCP server config for Claude Desktop / Claude Code
     (`scripts/mcp_server.py` reads `LINKLIB_SAVE_TOKEN` from its env —
     it's set in the client's MCP config JSON).
   - **The remote `/mcp` server's bearer tokens are a SEPARATE credential
     family—rotating `LINKLIB_SAVE_TOKEN` does not touch them.** They
     live in the `api_tokens` table (sha256-hashed, one row per minted
     token, each bound to a real user), not this env var. If you're
     rotating because the save token leaked, that leak says nothing about
     whether an `api_tokens` row also leaked—check separately:
     ```bash
     railway ssh
     python -m scripts.mint_api_token --db /data/library.db --list
     python -m scripts.mint_api_token --db /data/library.db --revoke <TOKEN_ID>
     ```
     Revoke and re-mint any token you don't have a specific reason to
     trust; a signed-in `/mcp` client just needs its config updated with
     the new value, same as any other credential rotation.
   - The daily backup Railway Cron Service's `LINKLIB_SAVE_TOKEN` (§7,
     Phase O) — if it's set up as a direct Railway variable reference to
     the main web service's own `LINKLIB_SAVE_TOKEN` (as §7.1 recommends),
     this updates automatically with step 2 above and needs no separate
     action; only check this if it was ever set as a standalone copy
     instead. Miss it and the cron service starts failing with `401` on
     the next scheduled run, silently, until someone checks that service's
     Railway run history or `/admin/library-backup`'s status banner shows
     a stale "last successful backup."
   - Any personal shell exports / scripts that call `/save`, `/api/search`,
     or `/ask` with `X-Save-Token` or `?token=`.

5. Verify: old token gets `401` on `POST /save`; new token saves fine
   (click the new bookmarklet on any article page).

---

## 3. Railway is down — triage

The site is one FastAPI process on Railway behind Cloudflare. Cloudflare
does not cache HTML (stock cache config), so if the origin is down, the
site is down — there is no "stale but serving" mode. The good news: the
data is two-way safe (Railway volume + daily Drive snapshots), so an
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
`linklib/backup.py` / `scripts/restore_from_drive.py`), at production size, so section 1 stays a checklist,
not a theory. The rehearsal never touches production — it's the same code
paths against scratch files.

### Procedure (local, ~10 minutes)

1. Build two scratch DBs with `linklib.db.Library`: a "good" one (a handful
   of articles — this stands in for the healthy DB that was backed up) and
   a "live" one (fewer/different rows — stands in for the damaged volume
   DB).
2. Produce the "Drive snapshot" from the good DB with
   `linklib.backup.snapshot_to_file()` — the exact function the daily
   upload uses, so the artifact is byte-for-byte what Drive would hold.
3. Run the app against the live DB:
   `LINKLIB_DB=.../live.db LINKLIB_SAVE_TOKEN=<anything> uvicorn webapp.app:app --port 8123`
4. Confirm the pre-restore state through the API
   (`GET /api/search?q=&limit=50&token=…` shows only the live DB's rows).
5. Restore with section 1's curl (`POST /admin/library-backup/upload-db`). It
   needs an admin session, so log in to this local instance as admin first
   (`LINKLIB_PASSWORD`) and send that cookie.
6. Validate: the 303 redirect's `uploaded=<N>` matches the good DB's
   article count; `/api/search` now returns the snapshot's rows (including
   an FTS query that missed before); the stale row is gone; on the file
   itself, `PRAGMA integrity_check` says `ok` and
   `INSERT INTO articles_fts(articles_fts) VALUES('integrity-check')`
   doesn't raise; `/health` still 200s — all **without restarting the app**.

### Record: rehearsal run 2026-10-06 ❌ (production size)

- Restored through `/admin/library-backup` (Path A) with the real 253.8 MB
  `library.db`. The spinner ran about 55 minutes. Railway logs for the whole
  window showed the `GET /admin/library-backup/download-db` (200) and no
  `POST /admin/library-backup/upload-db` line, no errors and no restarts. The
  handler logs on completion, and the body is read in full before it runs, so
  the upload never completed.
- Cause: Cloudflare caps request bodies at 100 MB on Free and Pro (larger
  requests return 413), so a 253.8 MB upload could not work however long it
  ran. The July run below used a 5-article scratch database, so this was never
  exercised at real size.
- Fix: `scripts/restore_from_drive.py` (§1, Path C). Path A now states its
  limit.
- **Rehearsal procedure at production size** (the script's first run against
  Drive is not yet recorded here; add the result when it has run):
  1. `railway ssh`, `cd /app`, `env | grep -c GOOGLE_OAUTH` should print 3.
  2. `python -m scripts.restore_from_drive --db /data/library.db --list`
  3. `python -m scripts.restore_from_drive --db /data/restore-test.db`
     restores the newest snapshot to a scratch path (no flag needed, the file
     does not exist). Expect the article count it prints to match the `--list`
     line. Check `df -h /data` first: it needs about 270 MB free.
  4. Delete `/data/restore-test.db` when done. The live database is untouched.
  5. Only then, for a real restore, step 3 with `--db /data/library.db
     --yes-replace-live`.

### Record: rehearsal run 2026-07-11 ✅

- Good DB: 5 articles; live DB: 2 articles (1 shared, 1 stale placeholder).
- Snapshot via `snapshot_to_file()` → 233,472-byte self-contained file, no
  WAL sidecar.
- Pre-restore: API listed 2 rows; FTS query `netsuite` → 0 hits.
- `POST /admin/library-backup/upload-db` with `X-Save-Token` → `303` (the route
  has been admin-session-only since 2026-10; the token is now refused),
  `location: /admin/library-backup?uploaded=5` (count matched the snapshot).
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

- **`/admin/reader/feeds`**—the primary surface. With everything healthy
  you see only a compact **Re-check subscriber access** button, nothing else.
  When a cookie has actually gone stale, that button is absorbed into a coral
  **"Subscriber cookie expired"** panel carrying an abbreviated version of
  5.2–5.3 inline. (Before 2026-09 this panel also carried a status line per
  domain; that per-domain detail now lives in each feed's own **Cookie**
  column instead—see below—so the panel keeps only the part a per-row dot
  can't carry: which env var to update.)
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

The feed table's **Cookie** column is a computed, read-only indicator—it
drives nothing, does not gate fetching, and is not what the probe reads.
(Before 2026-08 this was a manually-ticked checkbox that recorded intent,
not fact, and could silently drift from reality—see CLAUDE.md's Feeds-page
Cookie-indicator note. It's gone now; there's nothing to tick.) It shows two
different facts per row (2026-09): a dash when no `LINKLIB_COOKIE_<DOMAIN>`
variable is set for that feed's domain, checked live against the host
environment; and, once a variable is set, either "configured, not yet
checked" or one of this table's three colored states plus how long ago it
was checked—the same persisted `auth_cookie_status` record this section
already describes, rendered per feed instead of in a separate summary.

> **Every feed's domain is checked automatically (2026-09) — no code change
> needed for a new subscription.** Before 2026-09, only domains in a short
> hand-maintained tuple (`linklib.extract._COOKIE_DOMAINS`) were ever
> probed — this silently broke Cautious Optimism's cookie, which was set in
> Railway and simply never read, since the tuple was never extended for it.
> `_opml_feed_domains()` now derives the candidate set live from
> `preferred_sites.opml` (every feed's domain), so any feed on
> `/admin/reader/feeds` is checked the moment its `LINKLIB_COOKIE_<DOMAIN>`
> variable is set in 5.3 — no code change, no deploy.

### 5.2 Get a fresh cookie value

Do this in a normal browser profile where you're a paying subscriber.

1. Log into the site normally and open a post you can read in full.
2. Open DevTools → **Network**, then reload the page.
3. Click the top-level document request to that domain.
4. Under **Request Headers**, find `Cookie:` and copy **the entire value**—everything
   after `Cookie: `, semicolons and all.

**Copy the whole header rather than hunting for the one session cookie, if
you're at all unsure which one carries the session.**
`extract._cookie_for` sends the stored string as the `Cookie` header
verbatim, so a full copy is both correct and the reason you don't need to
know which individual cookie carries the session. This is also exactly what
the expired banner tells you on screen—the two are deliberately the same
procedure. If you'd rather copy the minimum cookie instead of the whole jar,
DevTools → **Application** → **Cookies** → the domain lists them
individually—a rough per-platform starting point: a **Substack** site's
session cookie is typically `connect.sid`; a **beehiiv** site uses a signed
JWT, usually under a name containing `token` or `session`. Treat these as a
place to start looking, not a guarantee—fall back to the full-header copy
above if the site doesn't work after copying just one cookie.

> **On Mostly Metrics' specific cookie name—still unconfirmed.** A search of
> git history turns up only the commit that introduced the feature
> (`43cd00b`), with no record of how the value was originally obtained.
> Copying the full header (step 4) sidesteps the question entirely. **If you
> do identify the real cookie name the next time you run this, write it down
> here**—that's the gap this note marks.

**Lifetime varies enormously by platform.** A beehiiv token typically expires
in about 48 hours; a Substack `connect.sid` lasts months. A beehiiv cookie
needs re-grabbing regularly—and a stale one fails *silently*: no error
anywhere, it just quietly stops returning full text. **A cookie visible in
DevTools may already be expired**—the browser keeps showing it either way, so
force a fresh one by logging out, logging back in, and copying it
immediately. **Checking a JWT's validity directly:** a beehiiv cookie's
payload carries an `exp` timestamp—paste the token into any JWT decoder
(e.g. jwt.io) to see whether it's still valid before assuming the fetch
failure is something else.

### 5.3 Update the domain's `LINKLIB_COOKIE_<DOMAIN>` variable

**One variable per domain, each holding a raw `Cookie` header value—no JSON,
nothing to parse.** (Before 2026-08 every domain's cookie lived in one
`LINKLIB_AUTH_COOKIES` JSON blob; a single hand-edited-JSON typo anywhere in
it silently broke every domain's cookie at once, since a parse failure reads
downstream as "nothing configured." Splitting it removes that failure mode
entirely: there's nothing to parse, so a typo in one domain's value can't
touch another's.)

The variable name is the domain, normalized to `[A-Z0-9_]` and uppercased
(`linklib.extract._cookie_env_var`)—dots and hyphens become underscores:

| Domain | Variable |
|---|---|
| `mostlymetrics.com` | `LINKLIB_COOKIE_MOSTLYMETRICS_COM` |
| `onlycfo.io` | `LINKLIB_COOKIE_ONLYCFO_IO` |

See §6 below for the naming convention this follows, and for how to add a
future domain.

Key rules, from `_auth_cookies` and `_cookie_for`:

- **Bare registrable domain drives the variable name and the match.** No
  scheme, no path, no `www.`—the lookup strips `www.` from the URL's host
  before matching.
- Subdomains match automatically (`host == dom or host.endswith("." + dom)`),
  so `LINKLIB_COOKIE_MOSTLYMETRICS_COM` also covers `www.mostlymetrics.com`.
- **Every feed's domain is checked automatically (2026-09)** — derived live
  from `preferred_sites.opml` (`linklib.extract._opml_feed_domains`), not a
  hardcoded list. Setting `LINKLIB_COOKIE_<DOMAIN>` for a domain that's
  already a feed on `/admin/reader/feeds` is the whole fix; no code change,
  no deploy—see §6.

In Railway: the `cfo-navigator` service → **Variables** → add/edit the
domain's `LINKLIB_COOKIE_<DOMAIN>` variable → save.

**Rollback:** the old `LINKLIB_AUTH_COOKIES` variable is left in place,
untouched, until the new per-domain variables are confirmed working (5.4).
If something goes wrong, the fastest revert is a code rollback to the commit
before this migration—`_auth_cookies()` reading the old blob again makes the
old variable live immediately, no Railway variable edits needed either way.
Once the new variables are confirmed working, `LINKLIB_AUTH_COOKIES` can be
deleted from Railway; it's no longer read by anything.

**Restart:** not something you have to think about. `_auth_cookies()` reads
`os.environ` on every call with no caching, so a new value is picked up by the
next fetch; and Railway redeploys the service on a variable change anyway.
Wait for that deploy to finish before verifying, or you'll be testing the old
container.

### 5.4 Verify

1. Open `/admin/reader/feeds`.
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
Check the feed in `/admin/reader/feeds`, then re-check. The cookie may well
be fine.

---

## 6. `LINKLIB_`-prefixed environment variable naming convention

**When:** adding a new `LINKLIB_`-prefixed Railway variable, or renaming an
existing one as part of the sequenced rename backlog opened by the 2026-08
`LINKLIB_AUTH_COOKIES` split (each remaining rename is a separate,
Brian-executed change—coordinated Railway edits plus a deploy each, not
batched).

**The convention** (derived from an audit of the ~18 `LINKLIB_`-prefixed
variables actually read by the code as of 2026-08—see the git history for
that investigation if you need the full inventory):

```
LINKLIB_<AREA>[_<SUBAREA>]_<SETTING-OR-TYPE>
```

General to specific, left to right, with a trailing suffix that names the
value's role when there is an obvious one: `_MODEL` (`LINKLIB_ENRICH_MODEL`,
`LINKLIB_CHAT_MODEL`, `LINKLIB_EMBED_MODEL`), `_TOKEN`/`_KEY`
(`LINKLIB_SAVE_TOKEN`, `LINKLIB_SECRET_KEY`), `_EMAIL`
(`LINKLIB_CONTACT_EMAIL`, `LINKLIB_FROM_EMAIL`), `_BASE`
(`LINKLIB_PUBLIC_BASE`), `_OPML` (`LINKLIB_SITES_OPML`), or a unit
(`LINKLIB_CONTACT_RATE_LIMIT_PER_HOUR`, `LINKLIB_CONTACT_TIME_TRAP_SECONDS`).
Almost every existing variable already fits this—it's a description of what
was already the dominant pattern, not a new one invented from scratch.

**For a *family* of per-instance variables** (one value per domain, per
feed, per anything else with an unbounded set of instances), use
`LINKLIB_<AREA>_<INSTANCE>`, where `<INSTANCE>` is the qualifier normalized
to `[A-Z0-9_]` and uppercased (dots and hyphens become underscores). This is
the pattern `LINKLIB_COOKIE_<DOMAIN>` follows (§5.3 above)—it's the
reference case for this half of the convention. Pair it with an explicit
list of the valid instances that's still not a wildcard `os.environ` scan
for the prefix—a domain containing both dots and hyphens isn't unambiguously
reversible from its normalized variable name, so *something* has to name
which instances exist before the variable is read. Originally a small
hand-maintained tuple in code (`_COOKIE_DOMAINS`, mirroring `feed.
PAYWALLED_DOMAINS`); as of 2026-09 that list is instead derived live from
`preferred_sites.opml` (`linklib.extract._opml_feed_domains`, every real
feed's domain) after the hardcoded version silently missed a newly added
paid subscription (Cautious Optimism)—the instance names now come from the
same data an admin already maintains on `/admin/reader/feeds`, not a second,
easy-to-forget copy in source.

**No other variable needed a rename to fit this convention** as of the
2026-08 audit—`LINKLIB_AUTH_COOKIES` was the only one that broke the
pattern (a single variable standing in for an unbounded family, rather than
one variable per instance or a scalar setting), and it's fixed as of §5.3.
Two dead variables were also found and are **not** part of any active
convention: `LINKLIB_AUTHOR_TITLE` (`.env.example` only, leftover from the
removed LinkedIn-drafting feature, never read by any code—safe to delete
from `.env.example` whenever someone's next in that file) and
`LINKLIB_QUEUE_EXCLUDE_CATEGORIES` (retired outright in PR 3, 2026-09,
with the Archive Queue itself; `feeds.exclude_from_queue` is frozen too,
so there's nothing left to point at).

---

## 7. Railway Cron Service — daily backup trigger

**Background:** the daily off-site Drive backup used to be triggered by a
scheduled GitHub Action (`.github/workflows/backup.yml`). That Action's
schedule silently stopped firing for 9 straight days (2026-08) when the
GitHub account's Actions spending limit blocked every workflow run — an
outage with nothing to do with Railway or this app, and one that wouldn't
self-resolve until the next billing cycle. The trigger was moved onto a
native **Railway Cron Service** in the same project instead, removing the
GitHub Actions dependency entirely. See CLAUDE.md's "Backup trigger moved
from GitHub Actions to a Railway Cron Service" note and ARCHITECTURE.md's
matching bullet for the full write-up — this section is the exact
one-time setup procedure.

### 7.1 One-time setup (Railway dashboard)

1. Open the `cfo-navigator` project in the Railway dashboard (the same
   project the main web service already lives in — this does **not** need
   its own project).
2. **+ New** → **Empty Service** (not "Deploy from GitHub repo" — this
   service runs no application code, just one `curl` call, so it doesn't
   need a source repo or a build).
3. Name it something identifiable, e.g. `backup-cron`.
4. On the new service's **Settings** tab:
   - **Cron Schedule**: `0 9 * * *` (daily, 09:00 UTC — the same slot the
     GitHub Action used).
   - **Deploy → Custom Start Command** (this is the only thing the service
     ever runs, since it has no build/source):
     ```
     response=$(curl -sS -w '\n%{http_code}' -X POST "https://cfo-navigator-production.up.railway.app/admin/backup-now" -H "X-Save-Token: $LINKLIB_SAVE_TOKEN"); status="${response##*$'\n'}"; echo "${response%$'\n'*}"; echo "HTTP status: $status"; [ "$status" -ge 200 ] && [ "$status" -lt 300 ]
     ```
     A non-2xx status makes the command's own exit code non-zero (the final
     `[ ... ] && [ ... ]` expression *is* the command's exit status), so a
     failed backup shows up as a failed run in this service's Railway run
     history — the same signal the old Action's `curl -f` gave in the
     GitHub Actions tab.
   - Confirm the **Restart Policy** is `Never` (or leave it at the Railway
     default for a cron service) — this service should run once per
     schedule tick and exit, not stay resident or auto-restart after a
     normal exit.
5. On the **Variables** tab, add a **Shared Variable reference** to the
   main web service's `LINKLIB_SAVE_TOKEN` (Railway's "reference a variable
   from another service" mechanism), rather than pasting a second copy of
   the token — one source of truth for the secret, and a future token
   rotation only has to happen in one place.
6. **Trigger one run manually** ("Run now" / the equivalent one-off trigger
   in the service's Deployments tab) to confirm it actually works before
   trusting the schedule:
   - A successful run's log should show the JSON body from
     `/admin/backup-now` (`Uploaded <name> (...) to Google Drive.`) and
     `HTTP status: 200`, and the run itself should show as succeeded.
   - Then confirm the backup actually landed: check `/admin/library-backup`
     for a fresh green banner entry and a new row in the history table with
     a timestamp matching the run, and spot-check the Drive folder link on
     that page shows a new snapshot file.
7. Once a manual run is confirmed working end-to-end, leave the schedule
   in place and stop checking it manually — `/admin/library-backup`'s
   status banner is the ongoing signal; it goes amber/red if a scheduled
   run stops landing.

### 7.2 Verifying without spending an extra backup

`/admin/backup-now` always performs a real backup when triggered — there's
no dry-run mode — so the manual "Run now" in step 6 above genuinely creates
one more Drive snapshot. That's expected and harmless (it's the same
action a manual "Force backup now" click already does from
`/admin/library-backup`, and `prune_old_backups()` keeps the retained
snapshot count bounded regardless of how it got there) — don't try to avoid
it by skipping the manual verification run. If you want to confirm the
service is wired correctly without touching production data at all, the
only real option is to point the Custom Start Command at a non-mutating
route first (e.g. `GET /health`, no auth needed) to prove the service and
its schedule work mechanically, then switch the command to the real
`/admin/backup-now` call once that's confirmed and do the one real
verification run from step 6.

### 7.3 If a scheduled run fails

Same triage as any other backup failure — this cron service is a trigger,
not a new failure mode of its own:

- **A non-2xx status printed in the run log**: read the response body the
  command echoed — `503` means `GOOGLE_OAUTH_*` isn't configured, `502`
  means the upload itself failed (check the message for the underlying
  Google API error). Fix per `/admin/library-backup`'s own status banner.
- **The run never started, or the service shows no run history**: check
  the cron schedule is still enabled on the service's Settings tab —
  unlike a GitHub Actions schedule, a Railway cron service doesn't
  auto-disable itself after a period of repo inactivity, but it can be
  paused manually from the dashboard.
- **The run succeeded (2xx) but `/admin/library-backup` shows nothing
  new**: this is the exact failure mode §Phase O's investigation found
  with the original GitHub Action (a redirect silently swallowing the
  request) — confirm the Custom Start Command is hitting the Railway
  origin (`*.up.railway.app`), not `bmweis.com` (which would 403 at
  Cloudflare, not redirect, but is still the wrong target for the same
  underlying "don't go through the CDN for this call" reason), and confirm
  `/admin/backup-now` isn't returning a 3xx anywhere in the chain.

---

## 8. Exa—outage, missing/revoked key, or the toggle

**Background:** Exa is a paid third-party search API, gated in most places
by both `EXA_API_KEY` and a live admin toggle (`Library.get_exa_enabled()`,
flipped at `/admin/system/ai`). Five call sites read it as of the 2026-09
fetch-error follow-up (`linklib.enrich._fetch_grounding_page`'s new
block-shaped-error fallback); two of them have no substitute at all when
Exa is unavailable.

### 8.1 Every call site, and what happens without Exa

| Call site | Module | Falls back to |
|---|---|---|
| FP&A Buddy web tier | `linklib/agent.py` | Claude's native `web_search_20250305`, same scope either way (trusted-sites allowlist for Current feed, unrestricted for Open web)—the only surface with a real substitute |
| Reader backfill: domain migration | `linklib/domain_migration.py` | Nothing of its own—the pipeline's existing Wayback Machine tier still catches the miss, same as any other backfill failure, but a real hit this tier would have found is simply not tried |
| Reader backfill: Medium-platform | `linklib/medium_platform.py` | Same as domain migration—Wayback only, no substitute of its own |
| Vendor profile drafting's grounding fallback (Description, Agent taxonomy, Community profile, Community listing) | `linklib/enrich.py::_fetch_grounding_page` | **Nothing—refuses to draft rather than saving anything**, when the direct fetch is blocked/thin and this can't recover it. The admin writes the field by hand instead. |
| Feature Taxonomy vendor research | `linklib/feature_scan.py::research_vendor_domain` | **Nothing—but doesn't refuse either.** The draft still runs against the model's own knowledge, flagged `low_confidence=True`, same as a genuinely thin vendor site. Real, just weaker grounding. |

`/admin/system/ai`'s Configuration card and Usage index both list all five
with this same fallback-or-nothing distinction—that page's copy is the
live source, this table is a snapshot of it for offline reading.

### 8.2 How to tell Exa is the problem

1. **`/admin/system/ai`**—Configuration → the Exa card shows the toggle's
   current state (On/Off) and, if `EXA_API_KEY` is unset, a banner saying
   every row is on its fallback regardless of the toggle. Press **Test
   connection** to fire one real, minimal Exa search—confirms the key
   itself works (or names the error) without waiting for a real backfill/
   drafting run to hit it.
2. **`content_refetch_log`** (Reader backfill's two tiers)—a successful
   Exa-sourced fetch logs `source='migration'` or `source='medium-fetch'`/
   `'medium-search'`. If a batch is landing only Wayback/failure rows where
   you'd expect one of those three, Exa isn't being reached or isn't
   finding anything.
3. **`ask_questions.exa_cost_usd`** (Buddy)—`0` on a turn that should
   have used Exa (and didn't error) means it fell through to Claude's
   native search instead—check the toggle and the key before assuming
   the web tier itself is broken.
4. **`enrichment_cost` where `model='exa-fetch'`** (vendor profile
   drafting)—a real row here means the grounding fallback fired and
   cost something; its absence on a run that should have needed it is the
   signal, same idea as `content_refetch_log` above.
5. **Feature Taxonomy vendor research has no database cost trail at all.**
   `scripts/originate_category_features.py` prints its own
   `Cost: Exa $X.XXXX Claude $X.XXXX` line per run—that printed output
   is the only record; nothing persists it anywhere you can check later.
   The script also prints a loud `WARNING` line naming the toggle
   (`/admin/system/ai`) and its off state, specifically, whenever
   `EXA_API_KEY` is present but the toggle is off—a missing key alone
   gets a quieter note, since a missing key is otherwise self-explanatory.

### 8.3 The toggle is the immediate stop

Flipping the switch off at `/admin/system/ai`—Configuration → the Exa
card → **Use Exa for web search**—takes effect immediately, no deploy,
and is the right first move the moment you suspect a billing lapse, a
revoked/expired key, or an Exa-side outage, while you sort out the real
fix. Every consumer already degrades the way §8.1's table describes the
moment it's off; nothing needs a code change to stop spending against a
key that might not be good.

### 8.4 Rotating or replacing `EXA_API_KEY`

1. Railway dashboard → the `cfo-navigator` service → **Variables** →
   set/update `EXA_API_KEY` to the new value → save. Saving triggers a
   redeploy, same as any other Railway variable change in this runbook.
2. Once the new deploy is live, use **Test connection** on
   `/admin/system/ai` to confirm the new key actually works before
   trusting it—don't wait for a real backfill/drafting run to find out.
3. If you're removing Exa entirely rather than replacing it (no
   replacement key), just delete the variable—every call site already
   treats a missing `EXA_API_KEY` as a normal, best-effort "not available"
   condition (never raises), so there's nothing else to change.

---

## 9. Locked out of admin, and rotating `LINKLIB_SECRET_KEY`

**Background.** The shared-secret "break-glass" login was retired (issue #627).
Logging in now needs a row in `users`. If the admin password is lost and the
emailed reset (`/forgot-password`, which needs an email on the user row and
working Gmail) can't help, reset it on the container.

### Recover access

1. Open a shell in the service:

   ```bash
   railway ssh
   ```

2. Preview (writes nothing, asks for no password). Always pass the absolute DB path:

   ```bash
   python -m scripts.reset_user_password --db /data/library.db --username <username>
   ```

3. Reset an existing user. It prompts twice, hidden. The password is never an
   argument and never logged:

   ```bash
   python -m scripts.reset_user_password --db /data/library.db --username <username> --apply
   ```

   Or generate one (printed once, copy it now):

   ```bash
   python -m scripts.reset_user_password --db /data/library.db --username <username> --apply --generate
   ```

4. If no admin account exists at all, create one:

   ```bash
   python -m scripts.reset_user_password --db /data/library.db --username <username> --create-admin --apply --generate
   ```

5. The script reads the row back and prints `verified by read-back`. Then sign in at `/login`
   and change the password at `/change-password`.

### Rotate `LINKLIB_SECRET_KEY` (signs out every session)

Do this once after the shared-secret login is retired, so any session issued
through the old fallback, and any legacy role-less cookie, stops working.

1. Generate a value: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Railway dashboard, service, **Variables**: set `LINKLIB_SECRET_KEY` to it. Saving redeploys.
3. Everyone, you included, signs in again. Nothing stored depends on this key, so
   MCP tokens, password-reset links, the bookmarklet and the database are unaffected.
4. Confirm it took: an old browser session lands on `/login`.

Only session cookies are signed with this key (`webapp/app.py` `_sign`/`_session_claims`).
MCP tokens and reset tokens are plain sha256 hashes in the database, not keyed to it.
