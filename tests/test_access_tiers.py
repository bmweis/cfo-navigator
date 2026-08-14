"""Three-tier access control: public / member / admin.

Asserts each route lands in the right tier — public reachable signed-out, member
pages redirect signed-out and open for members, admin stays admin-only, and the
in-app reader stays admin-only (resale-safe).
"""
import pathlib
import sys
import tempfile, os

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
    from fastapi.testclient import TestClient
    from linklib.db import Library
    lib = Library(db)
    lib.create_user("member1", "supersecret", role="user")
    lib.close()
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _member_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "member1", "password": "supersecret"}, follow_redirects=False)
    return c


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


PUBLIC = ["/", "/about", "/thought-leadership", "/contact",
          # CFO Toolbox browsing and everything linked from the thought-leadership
          # page are fully public; only the account tools below stay gated.
          "/tools", "/tools/software", "/tools/benchmarks", "/tools/communities",
          # /tools/communities/{slug} (the profile page), /tools/communities/gap
          # (redirects to /contact, not a 200), and /tools/communities/correct
          # (404s without a valid community_id) are all public but don't fit
          # this static 200-only list — covered separately in
          # tests/test_community_profiles.py.
          "/thought-leadership/growth-engine-ratio",
          "/thought-leadership/netsuite-mcp",
          "/thought-leadership/ai-hackathon-playbook",
          # Sail, Don't Row is fully playable signed-out, and the per-rank
          # leaderboards are publicly viewable — only submitting a run
          # (POST /play/submit) requires an account.
          "/play", "/play/leaderboard"]
# Submitting a tool / a piece, or requesting a warm intro, is account-only (spam
# control) even though the directory and home page are public.
MEMBER = ["/tools/fpa-buddy", "/tools/submit", "/library/submit"]
# Archive and Feed (Phase 1: admin-only) merged into the single Reader at
# /read in Phase 5 — a signed-in non-admin member is bounced to login just
# like any other admin page. The /library hub route itself was removed
# outright, no redirect. Auth gating on /read/{id} happens the same way
# (covered separately in test_reader_single_article_view below since it
# needs a seeded article to return 200 rather than 404 for an admin).
ADMIN_ONLY = ["/read"]
# Old flat URLs 301-redirect to their nested equivalents, unconditionally
# (even signed-out — the redirect itself carries no gated content). /ask and
# /questions used to redirect to /library/ask and /library/past-questions —
# both retired outright in Phase 2 (FP&A Buddy moved to /tools/fpa-buddy, no
# compatibility redirect), so those two flat URLs are just gone now too, not
# redirecting anywhere — see test_retired_ask_routes_are_gone below. /archive
# and /feed used to redirect to /library/archive and /library/feed; both were
# retired outright in Phase 5 (merged into /read), no compatibility redirect
# — see test_retired_reader_routes_are_gone below.
OLD_TO_NEW = {
    "/growth-engine-ratio": "/thought-leadership/growth-engine-ratio",
    "/finops-ai-hackathon": "/thought-leadership/ai-hackathon-playbook",
    "/netsuite-mcp": "/thought-leadership/netsuite-mcp",
}


def test_public_pages_open_to_anonymous(env):
    c = _client(env)
    for path in PUBLIC:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_member_pages_redirect_anonymous(env):
    c = _client(env)
    for path in MEMBER:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"], f"{path} -> {r.status_code}"


def test_member_can_reach_member_pages(env):
    c = _member_client(env)
    for path in MEMBER:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_admin_only_library_pages_redirect_non_admin_member(env):
    # Archive and Feed are admin-only (Phase 1) — a signed-in non-admin
    # member is bounced to login exactly like an anonymous visitor.
    for c in (_client(env), _member_client(env)):
        for path in ADMIN_ONLY:
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 303 and "/login" in r.headers["location"], f"{path} -> {r.status_code}"


def test_admin_reaches_admin_only_library_pages(env):
    c = _admin_client(env)
    for path in ADMIN_ONLY:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_library_hub_route_removed(env):
    # /library was removed outright in Phase 1 — no redirect, just gone.
    for c in (_client(env), _member_client(env), _admin_client(env)):
        assert c.get("/library", follow_redirects=False).status_code == 404


def test_retired_ask_routes_are_gone(env):
    # /library/ask and /library/past-questions were retired outright in
    # Phase 2 — FP&A Buddy moved to /tools/fpa-buddy, Past Questions folded
    # into that same page. No compatibility redirect (nothing bookmarked),
    # so both, plus the flat /questions redirect stub that used to point at
    # /library/past-questions, are just gone (404) for anonymous visitors,
    # members, and admins alike. GET /ask is the one exception: it 405s
    # rather than 404s, because POST /ask (the Q&A API) still lives at that
    # exact path — the GET redirect stub is gone, but the path itself isn't.
    retired_404 = ["/library/ask", "/library/past-questions", "/questions"]
    for c in (_client(env), _member_client(env), _admin_client(env)):
        for path in retired_404:
            assert c.get(path, follow_redirects=False).status_code == 404, f"{path} should be gone"
        assert c.get("/ask", follow_redirects=False).status_code == 405


def test_old_flat_urls_redirect_to_nested_paths(env):
    c = _client(env)
    for old, new in OLD_TO_NEW.items():
        r = c.get(old, follow_redirects=False)
        assert r.status_code == 301, f"{old} -> {r.status_code}"
        assert r.headers["location"] == new, f"{old} -> {r.headers['location']}"


def test_member_blocked_from_admin_and_reader(env):
    c = _member_client(env)
    # Admin pages redirect to login for a non-admin member.
    assert c.get("/admin", follow_redirects=False).status_code == 303
    assert c.get("/admin/users", follow_redirects=False).status_code == 303
    # In-app reader is admin-only (resale-safe) even for members.
    assert c.get("/read/1", follow_redirects=False).status_code == 303
    assert c.get("/api/read-article?id=1", follow_redirects=False).status_code == 401


def test_admin_reaches_everything(env):
    c = _admin_client(env)
    assert c.get("/admin", follow_redirects=False).status_code == 200
    assert c.get("/admin/library", follow_redirects=False).status_code == 200
    assert c.get("/read", follow_redirects=False).status_code == 200


def test_reader_single_article_view(env):
    """/read/{id} — the standalone single-article view (path param, replaces
    the old ?id= query param) — works for a saved article and 404s for one
    that doesn't exist."""
    from linklib.db import Library, Article
    lib = Library(os.environ["LINKLIB_DB"])
    aid = lib.upsert(Article(url="https://ex.com/x", title="Test Article", source="Ex", content="Body text " * 60))
    lib.close()

    c = _admin_client(env)
    r = c.get(f"/read/{aid}", follow_redirects=False)
    assert r.status_code == 200
    assert "Test Article" in r.text
    assert c.get("/read/999999", follow_redirects=False).status_code == 404


def test_retired_reader_routes_are_gone(env):
    # /library/archive and /library/feed were retired outright in Phase 5
    # (merged into /read) — no compatibility redirect, since Phase 1's
    # precedent for retired routes was "nothing bookmarked, nothing to
    # redirect." /archive and /feed used to 301 to them and are gone too.
    retired_404 = ["/library/archive", "/library/feed", "/archive", "/feed"]
    for c in (_client(env), _member_client(env), _admin_client(env)):
        for path in retired_404:
            assert c.get(path, follow_redirects=False).status_code == 404, f"{path} should be gone"


def test_member_api_gating(env):
    anon, member = _client(env), _member_client(env)
    # /api/search: 401 anon, 200 member
    assert anon.get("/api/search?q=x", follow_redirects=False).status_code == 401
    assert member.get("/api/search?q=x", follow_redirects=False).status_code == 200
    # admin-only curation API stays 401 for members
    assert member.post("/library/1/delete", follow_redirects=False).status_code in (401, 303)


def test_member_nav_shows_logout_not_admin(env):
    c = _member_client(env)
    html = c.get("/tools/fpa-buddy").text
    assert "Log out" in html
    assert ">Admin<" not in html and ">Draft<" not in html


def test_public_pages_are_role_aware(env):
    """A signed-in member sees Log out (not Sign in) even on public pages;
    an anonymous visitor sees Sign in."""
    member = _member_client(env)
    for path in ["/", "/about", "/thought-leadership", "/contact"]:
        html = member.get(path).text
        assert "Log out" in html and ">Sign in<" not in html, path
    anon = _client(env)
    assert ">Sign in<" in anon.get("/").text and "Log out" not in anon.get("/").text


def test_admin_nav_mirrors_member_plus_admin(env):
    # Admin sees the public nav + Admin + Log out. Draft is an admin tool that
    # lives in the Admin hub, not the top nav. Library was removed from the
    # nav entirely (Phase 1) — Archive/Feed are admin-only now and FP&A Buddy
    # is reachable directly by URL, not via a top-nav entry.
    html = _admin_client(env).get("/").text
    assert ">Admin<" in html and "Log out" in html
    assert ">CFO Toolbox<" in html
    nav = html.split("<nav")[1].split("</nav>")[0]
    assert ">Draft<" not in nav
    assert ">Library<" not in nav


def test_blank_username_no_longer_logs_in(env):
    # The old "leave username blank" admin shortcut is gone — a username is required.
    c = _client(env)
    c.post("/login", data={"username": "", "password": "adminpass"}, follow_redirects=False)
    assert c.get("/admin", follow_redirects=False).status_code == 303   # not signed in


def test_admin_username_breakglass_with_host_password(env):
    # Host password still works as lockout-proof admin, but with the reserved username.
    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    assert c.get("/admin", follow_redirects=False).status_code == 200


def test_community_route_removed(env):
    anon, admin = _client(env), _admin_client(env)
    assert anon.get("/community", follow_redirects=False).status_code == 404
    assert admin.get("/community", follow_redirects=False).status_code == 404


def test_public_pages_have_no_community_links(env):
    c = _client(env)
    for path in ["/", "/about", "/thought-leadership"]:
        assert 'href="/community"' not in c.get(path).text, path


def test_last_admin_cannot_be_demoted_disabled_or_deleted(env):
    from linklib.db import Library
    db = os.environ["LINKLIB_DB"]
    lib = Library(db)
    boss = lib.create_user("boss", "supersecret", role="admin")
    lib.close()
    c = _admin_client(env)   # host-password admin session

    def _boss():
        lib = Library(db)
        try:
            return lib.get_user("boss")
        finally:
            lib.close()

    # The only DB admin is protected on all three destructive actions.
    c.post(f"/admin/users/{boss}/role", follow_redirects=False)
    assert _boss()["role"] == "admin"
    c.post(f"/admin/users/{boss}/toggle", follow_redirects=False)
    assert _boss()["active"] == 1
    c.post(f"/admin/users/{boss}/delete", follow_redirects=False)
    assert _boss() is not None

    # With a second admin present, the guard lifts.
    lib = Library(db)
    lib.create_user("boss2", "supersecret", role="admin")
    lib.close()
    c.post(f"/admin/users/{boss}/role", follow_redirects=False)
    assert _boss()["role"] == "user"
