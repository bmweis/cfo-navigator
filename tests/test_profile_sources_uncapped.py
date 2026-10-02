"""Profile "Sources" lists are never capped (2026-10).

A software or community profile used to show only the first five Sources
chips, so a `[6]` or higher marker in the text had no chip to resolve to.
Compare already shows every chip; the profiles now match it. The marker set
in the text must equal the chip set, and each chip's number must sit beside
its own title.
"""
import importlib
import os
import re
import tempfile

import pytest

from linklib.db import Library

N = 6


def _cites():
    return [{"n": i + 1, "title": f"Title {i + 1}", "url": f"https://source{i + 1}.example",
             "type": "tool_page"} for i in range(N)]


def _text_with_markers():
    return " ".join(f"Claim {i + 1} [{i + 1}]." for i in range(N))


@pytest.fixture
def app_module(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _get(appmod, path):
    from fastapi.testclient import TestClient
    r = TestClient(appmod.app).get(path)
    assert r.status_code == 200
    return r.text


def _chips(html):
    """(number, title) for every Sources chip, in page order."""
    return re.findall(r'>\[(\d+)\] (Title \d+)</a>', html)


def _assert_chips_match_markers(html):
    chips = _chips(html)
    assert {n for n, _ in chips} == {str(i + 1) for i in range(N)}
    assert all(t == f"Title {n}" for n, t in chips)


def test_software_description_and_agent_taxonomy_show_all_six_sources(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    tid = lib.add_tool("Runway", _text_with_markers(), "https://runway.com", [], approved=1,
                       summary="S.")
    lib.update_tool_agent_taxonomy(tid, _text_with_markers())
    lib.set_entity_citations("tool", tid, "description", _cites(), model="m")
    lib.set_entity_citations("tool", tid, "agent_taxonomy", _cites(), model="m")
    slug = lib.get_tool(tid)["slug"]
    lib.close()
    html = _get(app_module, f"/tools/software/{slug}")
    # Two lists (Description card + Agent taxonomy card), six chips each.
    assert len(_chips(html)) == 2 * N
    assert html.count("source6.example") == 2
    _assert_chips_match_markers(html)


def test_community_profile_shows_all_six_sources(app_module):
    lib = Library(os.environ["LINKLIB_DB"])
    cid = lib.add_community(name="Chief", url="https://chief.com", demographic="Execs",
                            cost_band="Paid", categories=[], approved=1)
    lib.upsert_community_profile(cid, ideal_member=_text_with_markers(),
                                 verdict_summary="Best for X, not for Y.")
    lib.set_entity_citations("community", cid, "community_profile", _cites(), model="m")
    slug = lib.get_community(cid)["slug"]
    lib.close()
    html = _get(app_module, f"/tools/communities/{slug}")
    assert html.count("source6.example") == 1
    _assert_chips_match_markers(html)
