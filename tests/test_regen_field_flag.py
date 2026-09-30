"""--field flag for scripts/regen_ai_drafted_fields.py (2026-08 follow-up,
motivated by the Differentiation content-exclusion fix — see CLAUDE.md and
issue #445). Covers the pure --field parsing (_parse_fields) and that
_run_tools/_run_sample actually honor a narrowed `fields` tuple, without
making any real Claude API calls — the module's own generate_* functions
are never invoked here, only the dispatch around them.
"""
import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

regen = importlib.import_module("scripts.regen_ai_drafted_fields")


# -- _parse_fields -------------------------------------------------------

def test_parse_fields_defaults_to_all_three_when_omitted():
    # The whole point of the flag: no --field on the command line must be a
    # true no-op against every invocation written before this flag existed.
    assert regen._parse_fields(None) == regen.TOOL_FIELDS
    assert regen._parse_fields([]) == regen.TOOL_FIELDS


def test_parse_fields_single_repeatable_occurrence():
    assert regen._parse_fields(["description"]) == ("description",)


def test_parse_fields_repeatable_combines():
    # argparse's action="append" gives one list entry per --field occurrence.
    result = regen._parse_fields(["description", "agent_taxonomy"])
    assert result == ("description", "agent_taxonomy")


def test_parse_fields_comma_separated_combines():
    result = regen._parse_fields(["description,agent_taxonomy"])
    assert result == ("description", "agent_taxonomy")


def test_parse_fields_repeatable_and_comma_separated_combine_together():
    result = regen._parse_fields(["description", "agent_taxonomy,competitive_differentiation"])
    assert result == regen.TOOL_FIELDS


def test_parse_fields_normalizes_order_regardless_of_input_order():
    # Command-line order must not matter — always normalized back to the
    # real per-tool regeneration order (description, agent_taxonomy,
    # competitive_differentiation).
    result = regen._parse_fields(["competitive_differentiation", "description"])
    assert result == ("description", "competitive_differentiation")


def test_parse_fields_dedupes():
    result = regen._parse_fields(["description", "description,description"])
    assert result == ("description",)


def test_parse_fields_rejects_unknown_field():
    with pytest.raises(SystemExit):
        regen._parse_fields(["not_a_real_field"])


def test_parse_fields_rejects_community_field_name():
    # community_profile is a real field name in the DB/log, but it's not a
    # TOOLS-mode field --field can select — this flag never touches
    # Communities at all.
    with pytest.raises(SystemExit):
        regen._parse_fields(["community_profile"])


def test_parse_fields_rejects_all_blank():
    with pytest.raises(SystemExit):
        regen._parse_fields([" ", ",", ""])


# -- _run_tools honors a narrowed `fields` tuple --------------------------

def test_run_tools_default_fields_calls_all_three(monkeypatch):
    calls = []
    monkeypatch.setattr(regen, "_TOOL_FIELD_FNS", {
        "description": lambda *a, **k: calls.append("description"),
        "agent_taxonomy": lambda *a, **k: calls.append("agent_taxonomy"),
        "competitive_differentiation": lambda *a, **k: calls.append("competitive_differentiation"),
    })
    tool = {"id": 1, "name": "Runway"}
    # apply=False here — irrelevant to what we're testing (which fields get
    # dispatched to), and avoids the real INTER_CALL_SLEEP the loop would
    # otherwise sleep between calls when apply=True.
    regen._run_tools(None, [tool], "model", "voice", "log.jsonl", False, set())
    assert calls == ["description", "agent_taxonomy", "competitive_differentiation"]


def test_run_tools_narrowed_fields_calls_only_those(monkeypatch):
    calls = []
    monkeypatch.setattr(regen, "_TOOL_FIELD_FNS", {
        "description": lambda *a, **k: calls.append("description"),
        "agent_taxonomy": lambda *a, **k: calls.append("agent_taxonomy"),
        "competitive_differentiation": lambda *a, **k: calls.append("competitive_differentiation"),
    })
    tool = {"id": 1, "name": "Runway"}
    regen._run_tools(None, [tool], "model", "voice", "log.jsonl", False, set(),
                      fields=("competitive_differentiation",))
    assert calls == ["competitive_differentiation"]


def test_run_tools_narrowed_fields_still_respects_done_skip(monkeypatch):
    """The --field narrowing and the pre-existing resumability (`done` set)
    are independent mechanisms — narrowing to one field must not bypass the
    resume-skip logic for that field."""
    calls = []
    monkeypatch.setattr(regen, "_TOOL_FIELD_FNS", {
        "description": lambda *a, **k: calls.append("description"),
        "agent_taxonomy": lambda *a, **k: calls.append("agent_taxonomy"),
        "competitive_differentiation": lambda *a, **k: calls.append("competitive_differentiation"),
    })
    tool = {"id": 1, "name": "Runway"}
    done = {("tool", 1, "competitive_differentiation")}
    regen._run_tools(None, [tool], "model", "voice", "log.jsonl", False, done,
                      fields=("competitive_differentiation",))
    assert calls == []


# -- _run_sample honors a narrowed `fields` tuple -------------------------

def test_run_sample_narrowed_fields_only_samples_those(monkeypatch, capsys, tmp_path):
    seen_fields = []

    def _fake_description(name, url, **kw):
        seen_fields.append("description")
        return None

    def _fake_taxonomy(name, url, **kw):
        seen_fields.append("agent_taxonomy")
        return None

    def _fake_diff(name, url, description, **kw):
        seen_fields.append("competitive_differentiation")
        return None

    monkeypatch.setattr(regen, "generate_tool_description", _fake_description)
    monkeypatch.setattr(regen, "generate_tool_agent_taxonomy", _fake_taxonomy)
    monkeypatch.setattr(regen, "generate_tool_differentiation", _fake_diff)

    class _FakeLib:
        def list_tool_competitors(self, tool_id):
            return []

    tool = {"id": 1, "name": "Runway", "url": "https://runway.com", "description": ""}
    log_path = str(tmp_path / "log.jsonl")
    regen._run_sample(_FakeLib(), [tool], [], "model", "voice", log_path, 5,
                       fields=("competitive_differentiation",))
    assert seen_fields == ["competitive_differentiation"]


# -- over-limit drafts (2a.1) ---------------------------------------------------

def test_communities_run_skips_an_over_limit_draft_and_continues(monkeypatch, tmp_path, capsys):
    """generate_community_profile can return a field over its hard limit, which
    the library refuses whole. The run must skip that community, report it and
    carry on with the next one."""
    from linklib import enrich
    from linklib.db import Library

    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_community("Alpha", "https://alpha.example", "d", "Free", ["FP&A"], approved=1)
    b = lib.add_community("Beta", "https://beta.example", "d", "Free", ["FP&A"], approved=1)

    def _fake(name, url, existing=None, model="", voice_core=""):
        return enrich.CommunityProfileDraft(
            ideal_member="x" * 900 if name == "Alpha" else "Fine.", anti_fit="", value_prop="",
            format_reality="", engagement_level="", sponsor_relationship_note="", business_model="",
            application_friction="", cost_value_verdict="", notable_members="", public_criticism="",
            verdict_summary="Best for X.", jobs_program="", cpe_eligible="", resources_included="",
            low_confidence=False, model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01)

    monkeypatch.setattr(regen, "generate_community_profile", _fake)
    monkeypatch.setattr(regen, "INTER_CALL_SLEEP", 0)
    monkeypatch.setattr(regen, "INTER_BATCH_SLEEP", 0)
    regen._SKIPPED_OVER_LIMIT.clear()
    log = str(tmp_path / "log.jsonl")
    communities = [lib.get_community(a), lib.get_community(b)]
    regen._run_communities(lib, communities, "m", "voice", log, True, set())
    out = capsys.readouterr().out
    assert not (lib.get_community_profile(a) or {}).get("ideal_member")
    assert (lib.get_community_profile(b) or {}).get("verdict_summary") == "Best for X."
    assert len(regen._SKIPPED_OVER_LIMIT) == 1 and "Alpha" in regen._SKIPPED_OVER_LIMIT[0][0]
    assert "skipped, over limit" in out
    lib.close()


@pytest.mark.parametrize("stored,expected", [
    ("Yes (NASBA sponsor)", "Yes (NASBA sponsor)"), ("No", "No"), ("", "Unclear"),
])
def test_communities_run_keeps_a_stored_cpe_answer(monkeypatch, tmp_path, stored, expected):
    from linklib import enrich
    from linklib.db import Library

    lib = Library(str(tmp_path / "t.db"))
    cid = lib.add_community("Alpha", "https://alpha.example", "d", "Free", ["FP&A"], approved=1)
    lib.upsert_community_profile(cid, cpe_eligible=stored, ideal_member="Old.", verdict_summary="Old.")

    def _fake(name, url, existing=None, model="", voice_core=""):
        return enrich.CommunityProfileDraft(
            ideal_member="New.", anti_fit="", value_prop="", format_reality="", engagement_level="",
            sponsor_relationship_note="", business_model="", application_friction="",
            cost_value_verdict="", notable_members="", public_criticism="", verdict_summary="Best for X.",
            jobs_program="", cpe_eligible="Unclear", resources_included="", low_confidence=False,
            model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01)

    monkeypatch.setattr(regen, "generate_community_profile", _fake)
    monkeypatch.setattr(regen, "INTER_CALL_SLEEP", 0)
    monkeypatch.setattr(regen, "INTER_BATCH_SLEEP", 0)
    regen._run_communities(lib, [lib.get_community(cid)], "m", "voice", str(tmp_path / "l.jsonl"), True, set())
    prof = lib.get_community_profile(cid)
    assert prof["cpe_eligible"] == expected and prof["ideal_member"] == "New."
    lib.close()
