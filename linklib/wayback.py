"""Wayback Machine fallback for a direct fetch that failed — Phase 5b
follow-up (fetch reliability).

Only used as a LAST RESORT, after a direct fetch has already failed for any
reason — a stubborn 403 the browser-UA swap didn't fix, genuine link rot
(404), a timeout, a bot-challenge, whatever. Deliberately not scoped to 404
only: the same investigation that motivated this found three of four flagged
domains return a Cloudflare bot-management challenge no User-Agent string can
get past (see extract._BROWSER_HEADERS's comment) — but the Internet
Archive's own crawler generally isn't subject to that same per-request bot
gate, so a real archived snapshot of an otherwise-blocked page is a
genuinely plausible win, not just a 404 backstop.

**Investigation finding, worth knowing before touching this module again:**
the Wayback Availability API (`archive.org/wayback/available`) was found to
rate-limit (HTTP 429) unpredictably and BROADLY during the investigation that
built this — confirmed from two completely independent networks (a Railway
production container and a residential connection), on both a previously-
queried URL and one that had never been queried by anyone before. That rules
out "one IP got blocked" or "one URL got hammered" — it's archive.org's own
service being unavailable for stretches, for any requester, unrelated to
anything this codebase does. **This module is built defensively around that
finding**: every function here returns "no fallback" (None / "") on ANY
failure — a 429, a timeout, a malformed response, a network error — never
raises, and never retries. A retry loop here would just be more load against
a service already observed to be struggling, for a signal ("try again in a
few seconds") this investigation has no evidence would even help. If
archive.org is unavailable, the caller falls through to logging the
ORIGINAL direct-fetch failure, exactly as if this module didn't exist.

**Not verified end-to-end at build time**: because of the rate-limiting
above, this investigation was never able to watch a real snapshot's content
actually come back successfully — every attempt (Availability API, and the
CDX search API as a fallback) was rate-limited before returning real data.
The request/parsing logic here is standard and low-risk, but "a real snapshot
gets fetched and extracted correctly" is unverified pending archive.org's
rate limiting clearing — see the Phase 5b follow-up PR description and
CLAUDE.md for the full write-up. Verify via a small backfill batch
(`/admin/library/backfill-content`) once that clears, same as the tool's own
standing "verify small before full run" convention.

**Follow-up finding: the failure mode isn't always a 429.** The first real
production batch after this shipped ran into `ConnectionResetError`/
`ConnectTimeout` from archive.org instead — a different symptom, same
underlying story (archive.org unreliable from wherever this runs), but proof
the plain `find_snapshot()`/`fetch_snapshot()` contract (a bare `None`/`""`
on any failure) wasn't giving `linklib.pipeline.backfill_article_content`
enough to log *why* Wayback didn't help. `find_snapshot_verbose()`/
`fetch_snapshot_verbose()` exist for exactly that — same defensive
never-raises guarantee, plus a short outcome string a caller can persist.
`find_snapshot()`/`fetch_snapshot()` are unchanged, thin wrappers over the
verbose versions, for callers (the Reader's live-fetch path) that only care
whether a fallback is available, not why one isn't.
"""
from __future__ import annotations

import requests

from .extract import _BROWSER_HEADERS

_AVAILABILITY_URL = "https://archive.org/wayback/available"
# Short on purpose: this fallback sits on both the backfill's per-article loop
# AND the Reader's interactive live-fetch path (_resolve_reader_content) — a
# slow or hanging archive.org request must not turn into a long wait on either
# path. See the module docstring: real latency here is unverified pending
# archive.org's own rate limiting clearing, so this stays conservative rather
# than generous.
_TIMEOUT = 8


def _describe_wayback_error(exc: Exception) -> str:
    """Mirrors extract._describe_fetch_error, for the same reason: a bare
    'it failed' isn't enough once you've seen it fail two different ways
    (HTTP 429 one investigation session, ConnectionResetError/ConnectTimeout
    the next real batch) — see the module docstring's follow-up finding."""
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else "?"
        return f"HTTP {status}"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return f"SSL error: {exc}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"connection error: {exc}"
    return f"{type(exc).__name__}: {exc}"


def find_snapshot_verbose(url: str, timeout: int = _TIMEOUT) -> tuple[str | None, str]:
    """Like find_snapshot(), but also returns a short outcome string
    explaining why no snapshot came back: an HTTP status (`'HTTP 429'`), a
    connection-layer description (see _describe_wayback_error), a malformed
    response, or `'no snapshot archived'` when archive.org responded fine
    and there's just nothing there. `''` on success. Never raises."""
    try:
        resp = requests.get(_AVAILABILITY_URL, params={"url": url},
                            headers=_BROWSER_HEADERS, timeout=timeout)
    except Exception as exc:
        return None, _describe_wayback_error(exc)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    try:
        data = resp.json()
    except Exception:
        return None, "malformed response"
    snapshots = data.get("archived_snapshots") or {}
    closest = snapshots.get("closest") or {}
    snap_url = closest.get("url") or ""
    if snap_url:
        return snap_url, ""
    return None, "no snapshot archived"


def find_snapshot(url: str, timeout: int = _TIMEOUT) -> str | None:
    """Query the Wayback Availability API for the closest archived snapshot
    of `url`. Returns the snapshot's own URL (a web.archive.org/web/... URL,
    ready to fetch directly), or None on absolutely any failure — no snapshot
    exists, a non-200 response, a response body that isn't valid JSON, or a
    network error. Never raises. Thin wrapper over find_snapshot_verbose()
    that discards the reason — use that directly if the caller needs to log
    why, not just whether."""
    return find_snapshot_verbose(url, timeout=timeout)[0]


def fetch_snapshot_verbose(snapshot_url: str, timeout: int = _TIMEOUT) -> tuple[str, str]:
    """Like fetch_snapshot(), but also returns a short outcome string on
    failure (see _describe_wayback_error) — `''` on success. Never raises."""
    try:
        resp = requests.get(snapshot_url, headers=_BROWSER_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text, ""
    except Exception as exc:
        return "", _describe_wayback_error(exc)


def fetch_snapshot(snapshot_url: str, timeout: int = _TIMEOUT) -> str:
    """Fetch a Wayback snapshot's raw HTML. Returns "" on any failure (bad
    status, timeout, connection error) — never raises. The snapshot itself
    still has to pass the same content sanity check
    (extract.assess_extraction_quality) as a direct fetch before a caller
    treats it as usable; this function only gets the bytes. Thin wrapper
    over fetch_snapshot_verbose() that discards the reason."""
    return fetch_snapshot_verbose(snapshot_url, timeout=timeout)[0]
