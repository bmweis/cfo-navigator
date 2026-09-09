"""Communities pre-wrap rendering fix — mirrors the software profile page's
own white-space:pre-wrap fix (voice enforcement + structure pass, 2026-08).
A bare <p style="margin:0;"> (and the compare table's bare <td>) collapses
embedded newlines in stored text, flattening any paragraph breaks or
"- " bulleted lines a regenerated field might contain. Covers all three
locations found by the follow-up investigation: the profile page's grouped
cards, its Bottom line callout, and the /tools/communities/compare table.

Real Markdown/List Rendering for Narrative Fields (2026-09) superseded the
pre-wrap approach for the first two of those three locations — the profile
page's grouped cards and its Bottom line callout now render through
`webapp.markdown_render.render_narrative_markdown` (real <ul><li>/<strong>/
<p>, not literal dashes preserved as visible text), so
`test_profile_group_field_has_pre_wrap`/`test_bottom_line_callout_has_pre_wrap`
below were updated to assert the new markup instead of the retired one — see
`tests/test_narrative_field_markdown.py` for the fuller markdown-specific
coverage (bold, HTML-escaping, etc.) of the same two surfaces. The
`/tools/communities/compare` table is the one location that's deliberately
UNCHANGED by that PR (`-webkit-line-clamp` doesn't reliably clamp block-level
list markup) — `test_compare_table_cell_has_pre_wrap`/
`test_compare_table_empty_cell_unaffected` below still assert the original
pre-wrap markup and still pass unmodified.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


MULTILINE = "First idea.\n\nSecond idea in its own paragraph.\n\n- Bullet one\n- Bullet two"


def test_profile_group_field_has_pre_wrap(env):
    """Renamed in spirit, not in name (keeps its place in this file's
    location-by-location coverage) — the profile page's grouped cards now
    render real markdown, not pre-wrap, per the 2026-09 module docstring
    note above. Real paragraph breaks and bulleted lines still survive,
    just as genuine <p>/<ul><li> now instead of preserved literal text."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Finance Leaders Guild", "https://example.com/one",
                            "Finance leaders", "Free", [], access="Invite-only", approved=1)
    lib.upsert_community_profile(c1, ideal_member=MULTILINE)
    slug = lib.get_community(c1)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert '<div class="narrative-md">' in r.text
    assert "<p>First idea.</p>" in r.text
    assert "<p>Second idea in its own paragraph.</p>" in r.text
    assert "<li>Bullet one</li>" in r.text
    assert "<li>Bullet two</li>" in r.text
    assert "white-space:pre-wrap" not in r.text


def test_bottom_line_callout_has_pre_wrap(env):
    """See test_profile_group_field_has_pre_wrap's docstring — the Bottom
    line callout now renders real markdown too, with its own inline
    color/font-size/line-height/overflow-wrap/word-break style carried on
    the wrapping .narrative-md div instead of the single <p> it replaced
    (those are inherited properties, so every child <p>/<li> still gets
    them)."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Finance Leaders Guild", "https://example.com/one",
                            "Finance leaders", "Free", [], access="Invite-only", approved=1)
    lib.upsert_community_profile(c1, verdict_summary=MULTILINE)
    slug = lib.get_community(c1)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert 'class="narrative-md" style="color:var(--navy);font-size:16px;line-height:1.5;overflow-wrap:break-word;word-break:break-word;">' in r.text
    assert "<li>Bullet one</li>" in r.text
    assert "white-space:pre-wrap" not in r.text


def test_compare_table_cell_has_pre_wrap(env):
    """Compare Redesign Phase 1 moved this fix's markup from an inline
    style on the <td> to a shared .cmp-clamp-inner CSS class (which
    declares white-space:pre-wrap in the page's own <style> block) — same
    fix, same guarantee (newlines/bulleted lines survive instead of
    flattening), different mechanism now that every compare cell shares
    one clamp/pre-wrap treatment rather than a per-cell inline style."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Finance Leaders Guild", "https://example.com/one",
                            "Finance leaders", "Free", [], access="Invite-only", approved=1)
    c2 = lib.add_community("Ops Collective", "https://example.com/two",
                            "Ops leaders", "<$1k/yr", [], access="Open", approved=1)
    lib.upsert_community_profile(c1, ideal_member=MULTILINE)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert r.status_code == 200
    assert f'<div class="cmp-clamp-inner">{MULTILINE}</div>' in r.text
    assert ".cmp-clamp-inner{white-space:pre-wrap;}" in r.text


def test_compare_table_empty_cell_unaffected(env):
    """The 'Not yet available.' / needs-verification branches carry no
    dynamic newline content — pre-wrap on them is harmless, but confirm the
    fix didn't change their actual text."""
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Finance Leaders Guild", "https://example.com/one",
                            "Finance leaders", "Free", [], access="Invite-only", approved=1)
    c2 = lib.add_community("Ops Collective", "https://example.com/two",
                            "Ops leaders", "<$1k/yr", [], access="Open", approved=1)
    lib.upsert_community_profile(c1, ideal_member=MULTILINE)
    lib.close()

    r = _client(env).get(f"/tools/communities/compare?ids={c1},{c2}")
    assert r.status_code == 200
    assert "Not yet available." in r.text
