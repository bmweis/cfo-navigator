"""Client-side sort/filter toolbar on the Communities and Software admin tables
(Phase 2): the toolbar markup and the data-* attributes it reads off each row."""
import os
import tempfile

import pytest


@pytest.fixture
def admin_client(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=True)
    client.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield client, appmod, db
    if os.path.exists(db):
        os.remove(db)


def test_software_sort_filter_toolbar_renders(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool_category("FP&A")
    lib.add_tool("Tool A", "desc", "https://a.example", ["FP&A"], approved=1, promoted=1)
    lib.close()

    r = client.get("/admin/tools/software")
    assert 'id="software-sort-field"' in r.text
    assert '<option value="name">Name</option>' in r.text
    assert '<option value="promoted">Featured</option>' in r.text
    assert 'id="software-sort-filter-count"' in r.text
    # No scalar (enum) filters for Software — only the categories multi-select.
    assert 'id="software-filter-categories"' in r.text
    # Row carries lowercased data-* attributes the JS sorts/filters on.
    assert 'data-name="tool a"' in r.text
    assert 'data-promoted="1"' in r.text
    assert 'data-categories="fp&amp;a"' in r.text


def test_software_categories_are_always_visible_pills_not_a_dropdown(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_tool_category("FP&A")
    lib.add_tool("Tool A", "desc", "https://a.example", ["FP&A"], approved=1)
    lib.close()

    r = client.get("/admin/tools/software")
    # Pills render always-visible (no <details>/<summary> click-to-reveal wrapper
    # around the Software category filter — that's the old dropdown pattern).
    assert 'id="software-filter-categories"' in r.text
    assert 'class="admin-cat-pill"' in r.text
    cats_pos = r.text.index('id="software-filter-categories"')
    preceding = r.text[max(0, cats_pos - 200):cats_pos]
    assert "<details" not in preceding
    # Live search box, no submit button needed.
    assert 'id="software-filter-search"' in r.text
    assert 'type="search"' in r.text
    assert 'data-search="tool a https://a.example"' in r.text


def test_communities_categories_still_use_the_dropdown(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Peer Group")
    lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                       cost_band="<$1k/yr", categories=["Peer Group"], approved=1,
                       access="Open", sponsorship_type="Independent", format="Online", reach="National")
    lib.close()

    r = client.get("/admin/tools/communities")
    # Communities keeps the existing dropdown affordance — only Software's
    # category filter switched to always-visible pills.
    assert '<details style="display:inline-block;">' in r.text
    assert 'class="admin-cat-pill"' not in r.text
    assert 'id="communities-filter-search"' not in r.text


def test_communities_sort_filter_toolbar_renders(admin_client):
    client, appmod, db = admin_client
    from linklib.db import Library
    lib = Library(db)
    lib.add_community_category("Peer Group")
    lib.add_community(name="Comm A", url="https://ca.example", demographic="CFOs",
                       cost_band="<$1k/yr", categories=["Peer Group"], approved=1,
                       access="Open", sponsorship_type="Independent", format="Online", reach="National")
    lib.close()

    r = client.get("/admin/tools/communities")
    assert 'id="communities-sort-field"' in r.text
    for field in ("name", "cost_band", "access", "sponsorship_type", "format", "reach"):
        assert f'<option value="{field}">' in r.text
    # Scalar filter selects, one per enum field.
    assert 'data-filter-field="cost_band"' in r.text
    assert 'data-filter-field="access"' in r.text
    assert 'data-filter-field="sponsorship_type"' in r.text
    assert 'data-filter-field="format"' in r.text
    assert 'data-filter-field="reach"' in r.text
    assert 'id="communities-filter-categories"' in r.text
    # Row data attributes, lowercased.
    assert 'data-name="comm a"' in r.text
    assert 'data-cost_band="&lt;$1k/yr"' in r.text
    assert 'data-access="open"' in r.text
    assert 'data-categories="peer group"' in r.text


def test_default_sort_field_is_name_for_both_tables(admin_client):
    client, appmod, db = admin_client
    r1 = client.get("/admin/tools/software")
    r2 = client.get("/admin/tools/communities")
    # The first <option> in each sort <select> is the default — matches the
    # tables' existing server-side ORDER BY name. No other <option> tag may
    # appear between the <select> and the "name" option.
    for text, select_id in ((r1.text, "software-sort-field"), (r2.text, "communities-sort-field")):
        select_pos = text.index(f'<select id="{select_id}"')
        name_option_pos = text.index('<option value="name">Name</option>', select_pos)
        between = text[select_pos:name_option_pos]
        assert "<option" not in between
