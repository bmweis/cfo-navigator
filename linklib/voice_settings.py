"""Shared resolution helper for the three DB-backed voice-prompt settings
(`voice_core`, `voice_fpa_buddy`, `voice_matchmaker`) — 2026-08 visibility
follow-up to the spaced-em-dash incident.

Before this module existed, every call site resolved a voice setting with
`lib.get_setting(key) or CODE_DEFAULT_CONSTANT` — a silent fallback that
made it genuinely ambiguous, from outside the code, whether a given answer
was governed by an admin-edited value in `/admin/voice` or by a hardcoded
constant nobody could see without reading source. `Library.seed_voice_prompts()`
(see `linklib/db.py`) closes the "why would it ever be empty" half of that
by populating all three settings from their current code-default constants
on first boot, via a settings-flagged ("has this ever been seeded") gate —
never an emptiness check, so a deliberate clear-out through `/admin/voice`
stays cleared across every future deploy, the same seeding-gate precedent
`seed_paywall_cookie_flags`/`seed_feeds_from_opml` already established.

This module closes the other half: once seeding has run, a voice setting
should never again silently fall back to its code constant. `require_voice_setting`
is the one place every caller resolves a voice setting from now on — it
raises `VoicePromptMissing` on an empty value instead of substituting
anything, so "generation refuses and says why" replaces "generation runs on
an invisible fallback" as the failure mode for an emptied field. Each
caller catches `VoicePromptMissing` and responds in whatever shape its own
module already uses for an unavailable-precondition failure (`enrich.py`'s
generate_* functions return `None`; `agent.py`/`matchmaker.py` return their
own `Answer`/`MatchAnswer` with `failed=True` and the detail in `error` (never `text`); a `webapp/app.py`
AJAX route returns a 503 JSON error) — this module deliberately does not
prescribe one response shape, only the resolution contract.

The three code-default constants (`agent.VOICE_CORE_DEFAULT`/
`VOICE_FPA_BUDDY_DEFAULT`, `matchmaker.VOICE_MATCHMAKER_DEFAULT`) are NOT
imported here and stay exactly where they are — they remain real Python
values (seed-only references now, not active runtime fallbacks) rather
than being duplicated or relocated. This module can't import them anyway
without a circular import (`agent.py`/`matchmaker.py` both import
`Library` from `db.py`, and seeding needs to reach the constants from
`db.py` — see `Library.seed_voice_prompts`'s own lazy import for how that's
avoided).
"""
from __future__ import annotations

from .db import Library

# The three settings keys this module governs. Kept as one tuple so seeding,
# the /admin/voice banner, and any future consumer share one definition of
# "the voice settings" rather than three independently-typed key lists.
VOICE_SETTING_KEYS = ("voice_core", "voice_fpa_buddy", "voice_matchmaker")

_LABELS = {
    "voice_core": "General voice",
    "voice_fpa_buddy": "FP&A Buddy voice",
    "voice_matchmaker": "Chat Matchmaker voice",
}


class VoicePromptMissing(RuntimeError):
    """Raised by `require_voice_setting` when `settings.<key>` is empty.
    Every caller catches this and returns its own module's established
    "can't run right now" response — never re-substitutes a code default."""

    def __init__(self, key: str):
        self.key = key
        label = _LABELS.get(key, key)
        super().__init__(
            f"{label} ({key!r}) is not configured. Populate it at /admin/voice "
            f"before generation can use it — no code-level fallback is used."
        )


def require_voice_setting(lib: Library, key: str) -> str:
    """Reads `settings.<key>` and returns it verbatim (stripped). Raises
    `VoicePromptMissing` if empty or unset — callers must not substitute a
    code constant on their own. `key` should be one of `VOICE_SETTING_KEYS`,
    though this function doesn't enforce that (a future fourth voice
    setting can reuse it without an update here)."""
    value = (lib.get_setting(key) or "").strip()
    if not value:
        raise VoicePromptMissing(key)
    return value


def any_voice_setting_missing(lib: Library) -> list[str]:
    """Returns the subset of VOICE_SETTING_KEYS that are currently empty —
    used by /admin/voice's banner to show which setting(s) are blocking
    generation, without needing to catch three separate exceptions."""
    return [key for key in VOICE_SETTING_KEYS if not (lib.get_setting(key) or "").strip()]
