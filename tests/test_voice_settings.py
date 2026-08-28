"""linklib.voice_settings — the shared resolution helper for the three
DB-backed voice-prompt settings (2026-08 visibility follow-up). See the
module's own docstring and CLAUDE.md's matching bullet for the incident and
design this closes: a voice setting used to silently fall back to its
code-constant default when empty; now it's seeded once from that default
(Library.seed_voice_prompts) and, after that, an empty setting refuses
generation rather than substituting anything."""
import os
import tempfile

import pytest

from linklib.db import Library
from linklib.voice_settings import (
    VOICE_SETTING_KEYS,
    VoicePromptMissing,
    any_voice_setting_missing,
    require_voice_setting,
)


@pytest.fixture
def lib():
    db = tempfile.mktemp(suffix=".db")
    library = Library(db)
    yield library
    library.close()
    if os.path.exists(db):
        os.remove(db)


def test_voice_setting_keys_are_the_three_expected():
    assert set(VOICE_SETTING_KEYS) == {"voice_core", "voice_fpa_buddy", "voice_matchmaker"}


def test_require_voice_setting_raises_when_empty(lib):
    with pytest.raises(VoicePromptMissing) as exc_info:
        require_voice_setting(lib, "voice_core")
    assert "voice_core" in str(exc_info.value)
    assert "/admin/voice" in str(exc_info.value)


def test_require_voice_setting_returns_stripped_value(lib):
    lib.set_setting("voice_core", "  Some voice text.  ")
    assert require_voice_setting(lib, "voice_core") == "Some voice text."


def test_require_voice_setting_treats_whitespace_only_as_missing(lib):
    lib.set_setting("voice_core", "   ")
    with pytest.raises(VoicePromptMissing):
        require_voice_setting(lib, "voice_core")


def test_any_voice_setting_missing_lists_all_three_when_unseeded(lib):
    assert sorted(any_voice_setting_missing(lib)) == sorted(VOICE_SETTING_KEYS)


def test_any_voice_setting_missing_narrows_as_fields_are_set(lib):
    lib.set_setting("voice_core", "x")
    lib.set_setting("voice_fpa_buddy", "y")
    assert any_voice_setting_missing(lib) == ["voice_matchmaker"]


def test_any_voice_setting_missing_empty_once_all_set(lib):
    for key in VOICE_SETTING_KEYS:
        lib.set_setting(key, "x")
    assert any_voice_setting_missing(lib) == []


# --- Library.seed_voice_prompts -----------------------------------------------

def test_seed_voice_prompts_populates_all_three_from_code_defaults(lib):
    from linklib.agent import VOICE_CORE_DEFAULT, VOICE_FPA_BUDDY_DEFAULT
    from linklib.matchmaker import VOICE_MATCHMAKER_DEFAULT

    result = lib.seed_voice_prompts()
    assert result["seeded"] is True
    assert sorted(result["populated"]) == sorted(VOICE_SETTING_KEYS)
    assert lib.get_setting("voice_core") == VOICE_CORE_DEFAULT
    assert lib.get_setting("voice_fpa_buddy") == VOICE_FPA_BUDDY_DEFAULT
    assert lib.get_setting("voice_matchmaker") == VOICE_MATCHMAKER_DEFAULT
    assert any_voice_setting_missing(lib) == []


def test_seed_voice_prompts_preserves_a_value_already_set(lib):
    """An admin who customized a field before this shipped keeps their own
    text — seeding only ever fills in a field that's CURRENTLY empty."""
    lib.set_setting("voice_core", "MY PRE-EXISTING CUSTOM VOICE")
    result = lib.seed_voice_prompts()
    assert result["seeded"] is True
    assert "voice_core" not in result["populated"]
    assert lib.get_setting("voice_core") == "MY PRE-EXISTING CUSTOM VOICE"
    # The other two still get seeded.
    assert "voice_fpa_buddy" in result["populated"]
    assert "voice_matchmaker" in result["populated"]


def test_seed_voice_prompts_is_a_noop_on_second_call(lib):
    lib.seed_voice_prompts()
    result2 = lib.seed_voice_prompts()
    assert result2 == {"seeded": False, "populated": []}


def test_seed_voice_prompts_does_not_resurrect_a_deliberate_clear(lib):
    """The core guarantee: once seeded, a deliberate clear-out through
    /admin/voice must survive every future deploy/restart — seeding is
    gated on a settings flag ('has this ever been seeded'), never on
    'is the field currently empty', or a cleared field would get silently
    repopulated on the very next boot."""
    lib.seed_voice_prompts()
    lib.set_setting("voice_core", "")  # deliberate clear, e.g. via /admin/voice

    result = lib.seed_voice_prompts()  # simulates the next boot's startup hook
    assert result == {"seeded": False, "populated": []}
    assert lib.get_setting("voice_core") == ""
    with pytest.raises(VoicePromptMissing):
        require_voice_setting(lib, "voice_core")
