"""scripts/originate_category_features.py's own CLI, main() — specifically
the 2026-09 fetch-error follow-up's loud toggle-off warning (see CLAUDE.md).

A missing EXA_API_KEY already had a self-explanatory print; a DELIBERATE
Exa toggle-off (a real, working key, but /admin/system/ai's kill switch is
flipped) previously had no signal anywhere on this page at all — every
tool would silently draft from the model's own knowledge instead of real
vendor content, reading as a quality problem rather than a switch left in
the wrong position. Covers the exact three states (key missing / key
present + toggle off / key present + toggle on) via the script's own
stderr output, mocking `originate_category_features` itself so this never
makes a real API call.
"""
import importlib
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library

ocf_script = importlib.import_module("scripts.originate_category_features")


@pytest.fixture
def temp_db_with_roster():
    db_path = tempfile.mktemp(suffix=".db")
    lib = Library(db_path)
    lib.set_setting("voice_core", "Test voice guide.")
    lib.add_tool_category("Neobanking")
    lib.add_tool("Mercury", "A banking tool.", "https://mercury.com", ["Neobanking"])
    lib.close()
    yield db_path


def _fake_summary():
    return types.SimpleNamespace(
        tools_researched=1, tools_failed=0, candidates_total=0, clusters_found=0,
        features_queued=0, features_merged=0, features_split=0,
        exa_cost_usd=0.0, claude_cost_usd=0.0, clustering_degraded=False,
        clustering_degraded_tools=[], queued_payloads=[], queue_item_ids=[],
    )


def _run_main(monkeypatch, db_path, env):
    monkeypatch.setattr(sys, "argv", ["originate_category_features.py", "--db", db_path,
                                       "--category", "Neobanking"])
    for k in ("ANTHROPIC_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(ocf_script, "originate_category_features", lambda *a, **kw: _fake_summary())
    return ocf_script.main()


def test_warns_loudly_when_key_present_but_toggle_off(monkeypatch, capsys, temp_db_with_roster):
    lib = Library(temp_db_with_roster)
    lib.set_exa_enabled(False)
    lib.close()

    rc = _run_main(monkeypatch, temp_db_with_roster, {"ANTHROPIC_API_KEY": "x", "EXA_API_KEY": "x"})
    assert rc == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "toggled OFF" in err
    assert "/admin/system/ai" in err


def test_no_warning_when_toggle_on(monkeypatch, capsys, temp_db_with_roster):
    """The default state (get_exa_enabled() defaults True on a fresh DB,
    never explicitly toggled) must NOT print the new warning — a
    regression here would mean every ordinary run starts printing a false
    alarm."""
    rc = _run_main(monkeypatch, temp_db_with_roster, {"ANTHROPIC_API_KEY": "x", "EXA_API_KEY": "x"})
    assert rc == 0
    err = capsys.readouterr().err
    assert "toggled OFF" not in err


def test_no_new_warning_when_key_missing(monkeypatch, capsys, temp_db_with_roster):
    """A missing key already gets its own pre-existing "Note: EXA_API_KEY
    is not set" line — the new toggle-off warning must not ALSO fire on
    top of it (the toggle state is moot when there's no key to gate)."""
    lib = Library(temp_db_with_roster)
    lib.set_exa_enabled(False)
    lib.close()

    rc = _run_main(monkeypatch, temp_db_with_roster, {"ANTHROPIC_API_KEY": "x"})
    assert rc == 0
    err = capsys.readouterr().err
    assert "EXA_API_KEY is not set" in err
    assert "toggled OFF" not in err
