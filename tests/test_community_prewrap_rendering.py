"""Communities pre-wrap rendering fix — mirrors the software profile page's
own white-space:pre-wrap fix (voice enforcement + structure pass, 2026-08).
A bare <p style="margin:0;"> (and the compare table's bare <td>) collapses
embedded newlines in stored text, flattening any paragraph breaks or
"- " bulleted lines a regenerated field might contain. Covers all three
locations found by the follow-up investigation: the profile page's grouped
cards, its Bottom line callout, and the /tools/communities/compare table.
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
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Finance Leaders Guild", "https://example.com/one",
                            "Finance leaders", "Free", [], access="Invite-only", approved=1)
    lib.upsert_community_profile(c1, ideal_member=MULTILINE)
    slug = lib.get_community(c1)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert f'<p style="margin:0;white-space:pre-wrap;">{MULTILINE}</p>' in r.text


def test_bottom_line_callout_has_pre_wrap(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    c1 = lib.add_community("Finance Leaders Guild", "https://example.com/one",
                            "Finance leaders", "Free", [], access="Invite-only", approved=1)
    lib.upsert_community_profile(c1, verdict_summary=MULTILINE)
    slug = lib.get_community(c1)["slug"]
    lib.close()

    r = _client(env).get(f"/tools/communities/{slug}")
    assert r.status_code == 200
    assert "white-space:pre-wrap;" in r.text
    # the Bottom line callout's own <p> tag carries it alongside its other inline styles
    assert 'overflow-wrap:break-word;word-break:break-word;white-space:pre-wrap;">' in r.text


def test_compare_table_cell_has_pre_wrap(env):
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
    assert f'<td class="cc-cell" style="white-space:pre-wrap;">{MULTILINE}</td>' in r.text


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
