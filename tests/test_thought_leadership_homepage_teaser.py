"""Homepage Restructure phase: the homepage's consolidated Thought
Leadership section (flagship cards + "Recent highlights" grid + bullets,
replacing both the old standalone card and the Phase 3 addendum's separate
recency-pin teaser), the sidebar Toolbox panel and admin-only Reader-access
placeholder, and the "Speaking &amp; Events" double-escaping fix on
/thought-leadership.

"Recent highlights" itself (originally one-per-type via the now-deleted
Library.get_thought_leadership_representative fallback mechanism) was
replaced by a hand-curated 4-slot featured set, any mix of types, selected
by the same "Feature on homepage" checkbox — see the featured-cap section
below.
"""
import pathlib
import sys
import tempfile
import os

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _seed_flagship_original_content(appmod):
    """Seed the 3 flagship `original_content` rows a real
    scripts/archive/migrate_original_content.py --apply run would produce (Original
    Content Phase 1). This test file exercises homepage/thought-leadership
    rendering as it looks post-migration, not the pre-seed empty-table
    state — a fresh test DB otherwise has zero flagship cards, since seeding
    is a manual, run-by-hand migration now, not automatic schema setup.

    Also applies the one later-phase change this file's assertions actually
    depend on: Original Content Phase 4b's ai-hackathon-playbook title fix
    (scripts/archive/migrate_hackathon_playbook_content.py). That migration only
    touches the ai-hackathon-playbook row (title/body_md/date_label), and
    the flagship homepage/thought-leadership cards render straight from
    the DB row's own title (_oc_featured_cards_html/_tl_fcard, not the
    frozen _TL_FEATURED_CARDS tuple) — so leaving this fixture pinned to
    the pre-4b title would silently drift from what production actually
    shows once that migration is applied there. Phase 4a's netsuite-mcp
    migration needed no equivalent here since it never touched that row's
    title, only its body_md."""
    from scripts.archive.migrate_original_content import planned_rows
    from scripts.archive.migrate_hackathon_playbook_content import TITLE as HACKATHON_TITLE
    lib = appmod._lib()
    try:
        for r in planned_rows():
            lib.add_original_content(
                r["slug"], r["title"], r["teaser"], r["tag_label"], r["link_label"],
                r["body_md"], r["status"], r["featured_home"], r["date_label"], r["sort_key"],
                r["display_order"],
            )
        row = lib.get_original_content_by_slug("ai-hackathon-playbook")
        lib.update_original_content(
            row["id"], row["slug"], HACKATHON_TITLE, row["teaser"], row["tag_label"],
            row["link_label"], row["body_md"], row["status"], row["featured_home"],
            row["date_label"], row["sort_key"], row["display_order"],
        )
    finally:
        lib.close()


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib, webapp.app as appmod
    importlib.reload(appmod)
    _seed_flagship_original_content(appmod)
    yield appmod
    if os.path.exists(db):
        os.remove(db)


def _client(appmod):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app, raise_server_exceptions=True)


def _admin_client(appmod):
    c = _client(appmod)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    return c


def test_homepage_has_one_consolidated_thought_leadership_section(env):
    html = _client(env).get("/").text
    # Exactly one section — old standalone card + old separate teaser are gone.
    # (">Thought Leadership<" alone would also match the top nav link, so
    # "What I write about" — the section's own unique heading — is the
    # reliable one-section signal here.)
    assert html.count("What I write about") == 1
    assert "See all thought leadership" in html
    assert 'href="/thought-leadership"' in html
    # The 3 flagship pieces, using the shared _tl_fcard/.tl-card treatment.
    assert "The Growth Engine Ratio" in html
    # Straight apostrophe, not the curly &rsquo; entity — Phase 4b's
    # double-escape fix (the row's stored title is real, unescaped text;
    # _tl_fcard() renders title raw, so a pre-escaped value would render
    # literally as "&rsquo;" text). Sentence-cased per the 2026-08
    # sentence-case audit — this is the article headline reusing "Sail,
    # Don't Row" as a pun, not the /play game's own name, so it doesn't
    # keep the game's capitalization (see BRAND.md §3.2).
    assert "Sail, don't row" in html
    assert "Sail, Don&rsquo;t Row" not in html
    assert "Sail, Don't Row" not in html
    assert "Connecting Claude to NetSuite" in html
    assert 'class="tl-card"' in html
    # Sail Don't Row correction: real playbook copy/link, not the design
    # file's arcade-game placeholder copy.
    assert "Play it" not in html
    assert "Read the playbook" in html
    assert ">Playbook<" in html
    assert ">Interactive<" not in html
    # Exact bullet copy, not rephrased.
    assert "AI in finance&mdash;separating signal from noise, tracking what&rsquo;s changing." in html
    assert ("Frameworks myself and others have built, real opinions, and stories from the "
            "trenches.") in html
    assert ("How to move from scorekeeper to strategic partner: stop reporting what happened, "
            "start shaping what&rsquo;s next.") in html
    assert ("Showing up for the finance community&mdash;hosting my own podcast, speaking on "
            "panels, co-chairing demo days and events.") in html
    assert "The F Suite" not in html


def test_flagship_cards_content_shared_between_homepage_and_thought_leadership(env):
    """The two pages must render identical flagship-card content (title,
    description, tag, link label) — a deliberate shared-source guarantee,
    now the `original_content` DB table (Original Content Phase 1) rendered
    through the shared _oc_card_tuple/_tl_fcard helpers, so the two surfaces
    still can't silently drift apart, even though their card *sizing* differs
    (each page's own .tl-featured grid track width narrows the cards on the
    homepage's tighter column). The homepage shows only featured_home=1
    pieces while /thought-leadership shows every live piece — for the 3
    seeded flagship rows that's the same set, so this also confirms both
    query paths (list_original_content_for_home vs.
    list_original_content(status='live')) land on identical card content."""
    from webapp.app import _oc_card_tuple, _tl_fcard

    lib = env._lib()
    try:
        home_rows = lib.list_original_content_for_home()
        live_rows = lib.list_original_content(status="live")
    finally:
        lib.close()
    assert home_rows and live_rows

    home_html = _client(env).get("/").text
    tl_html = _client(env).get("/thought-leadership").text
    for i, row in enumerate(home_rows):
        card_html = _tl_fcard(*_oc_card_tuple(row, i))
        assert card_html in home_html, f"missing/diverged on homepage: {row['title']}"
    for i, row in enumerate(live_rows):
        card_html = _tl_fcard(*_oc_card_tuple(row, i))
        assert card_html in tl_html, f"missing/diverged on /thought-leadership: {row['title']}"


def test_toolbox_panel_present_and_matches_design(env):
    html = _client(env).get("/").text
    assert ">CFO Toolbox<" in html
    assert "Everything in the toolbox" in html
    assert "See the full toolbox" in html
    # Regression check: the sticker was missing from an earlier design file
    # export (an omission, confirmed by Brian — not an intentional removal),
    # so it stays on the rebuilt panel same as every earlier round.
    assert "🚧 building" in html
    # A later re-export of the design file added the sticker back at the
    # source with a specific position/rotation — lock that in so it can't
    # silently drift from the file a second time.
    idx = html.find("🚧 building")
    assert "rotate(4deg)" in html[idx - 250:idx]
    assert "right:20px" in html[idx - 250:idx]


def test_reader_access_box_admin_only(env):
    # /read shipped (Phase 5 Reader merge) before this box was reconciled
    # onto this branch, so it's a real link now, not a "build pending"
    # placeholder.
    anon_html = _client(env).get("/").text
    assert "Reader access" not in anon_html

    c = _client(env)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    admin_html = c.get("/").text
    assert "Reader access" in admin_html
    assert "Admin only" in admin_html
    assert 'href="/read"' in admin_html
    assert c.get("/read").status_code == 200


def test_add_and_edit_forms_have_feature_on_homepage_checkbox(env):
    c = _admin_client(env)
    add_html = c.get("/admin/thought-leadership/third-party/new").text
    assert 'name="featured_home"' in add_html
    assert "Feature on homepage" in add_html

    lib = env._lib()
    try:
        item_id = lib.add_thought_leadership("writing", "A Piece", "https://example.com/piece",
                                              "Forbes", "Jan 2026", "2026-01")
    finally:
        lib.close()
    edit_html = c.get(f"/admin/thought-leadership/third-party/{item_id}/edit").text
    assert 'name="featured_home"' in edit_html
    assert "Feature on homepage" in edit_html


def test_featured_home_checkbox_persists_via_add_and_edit_routes(env):
    c = _admin_client(env)
    c.post("/admin/thought-leadership/third-party/new", data={
        "type": "writing", "title": "Pinned Piece", "url": "https://example.com/pinned",
        "venue": "Forbes", "date_label": "Jan 2020", "display_order": "",
        "featured_home": "1",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        items = lib.list_thought_leadership(type="writing")
    finally:
        lib.close()
    assert len(items) == 1
    assert items[0]["featured_home"] == 1

    item_id = items[0]["id"]
    c.post(f"/admin/thought-leadership/third-party/{item_id}/edit", data={
        "type": "writing", "title": "Pinned Piece", "url": "https://example.com/pinned",
        "venue": "Forbes", "date_label": "Jan 2020", "display_order": "0",
    }, follow_redirects=False)
    lib = env._lib()
    try:
        it = lib.get_thought_leadership(item_id)
    finally:
        lib.close()
    assert it["featured_home"] == 0


def test_get_thought_leadership_representative_no_longer_exists(env):
    # Deleted outright, not renamed or kept as a dead alias — the per-type
    # representative/fallback mechanism it implemented is gone, replaced by
    # a hand-curated 4-slot featured set (Library.list_thought_leadership_featured_home).
    lib = env._lib()
    try:
        assert not hasattr(lib, "get_thought_leadership_representative")
    finally:
        lib.close()


def test_homepage_renders_a_single_featured_piece(env):
    lib = env._lib()
    try:
        lib.add_thought_leadership("writing", "Featured Writing Piece", "https://example.com/w",
                                   "Forbes", "Jan 2026", "2026-01", featured_home=True)
        # No "speaking", "podcast", or "press" entries at all — an empty
        # type must not break the page (there's no per-type column anymore
        # to break; the featured set is just whatever's checked, any mix).
    finally:
        lib.close()
    resp = _client(env).get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Featured Writing Piece" in html
    assert 'href="https://example.com/w"' in html


def test_homepage_empty_db_does_not_break_page(env):
    resp = _client(env).get("/")
    assert resp.status_code == 200
    assert ">Thought leadership<" in resp.text


def test_hero_polish_avatar_size(env):
    html = _client(env).get("/").text
    assert "width:200px;height:200px" in html


def test_speaking_and_events_renders_without_double_escaping(env):
    html = _client(env).get("/thought-leadership").text
    assert "&amp;amp;" not in html
    # "Speaking & Events" -> "Speaking and Events" in PR 9's typographic
    # sweep (linklib.voice_review.typography_findings). The double-escape bug
    # this test was written for is unchanged and still guarded: what must
    # never appear is a re-escaped entity, whatever the label says.
    assert "Speaking and Events" in html
    assert "&amp;amp;" not in html


def test_mobile_dom_order_photo_card_between_hero_and_thought_leadership(env):
    """On mobile, .home-grid is a plain stacked flow (no CSS grid override
    below the desktop breakpoint), so DOM order is what actually renders. The
    photo/status card must sit between the hero block and the Thought
    Leadership section — not at the very end with the rest of the sidebar —
    per Brian's explicit mobile-order request. Desktop layout is restored via
    separate explicit grid-column/grid-row placement, independent of this
    DOM order."""
    html = _client(env).get("/").text
    hero_idx = html.index('class="home-hero-block"')
    photo_idx = html.index('class="home-photo-wrap"')
    tl_idx = html.index('class="home-tl-section"')
    sidebar_rest_idx = html.index('class="home-sidebar-rest"')
    assert hero_idx < photo_idx < tl_idx < sidebar_rest_idx


def test_mobile_order_breakpoint_survives_phone_landscape_widths(env):
    """The desktop 2-column grid must not kick in at typical phone-landscape
    widths (the largest common phones land around ~930px), or the mobile
    ordering above would flip back to the desktop layout purely from a
    screen rotation, not an actual desktop viewport. 1024px (not the
    sitewide-standard 900px other sections on this page use) is the
    deliberate, wider breakpoint that keeps phones — portrait or
    landscape — on the stacked mobile order."""
    html = _client(env).get("/").text
    idx = html.find("@media(min-width:1024px)")
    assert idx != -1
    assert ".home-grid{display:grid" in html[idx:idx + 60]
    assert "@media(min-width:900px){\n  .home-grid{display:grid" not in html


def test_only_one_thought_leadership_section_old_standalone_card_removed(env):
    """Exactly one Thought Leadership presence on the homepage — the
    consolidated section (home-tl-section) — with no trace of either
    superseded predecessor: the original standalone "Thought Leadership"
    card (pre-Phase-3, _rcard-based, its own distinct copy) or the stray
    parallel-session PR's version of homepage() (which never touched the
    Thought Leadership section at all, so the standalone card was still the
    live one at the time it merged — see CLAUDE.md's reconciliation note)."""
    html = _client(env).get("/").text
    assert html.count('class="home-tl-section"') == 1
    assert html.count("What I write about") == 1
    # The old standalone card's own distinct copy — must not survive under
    # any form (it predates the flagship-card/bullet copy entirely).
    assert "Frameworks and playbooks worth keeping" not in html
    assert "plus the podcasts, writing, and press." not in html
    # The old card's containing markup (a Phase-3-era single-card row) is
    # gone too, not just its text.
    assert 'class="home-cards"' not in html


def test_hero_copy_has_no_artificial_width_cap(env):
    """The hero headline and subhead paragraph used to be capped at
    max-width:640px — a leftover from an earlier hero-polish pass that
    pre-dated the wider two-column grid. Once the grid gave the hero column
    ~975px to work with, that cap made the text wrap far narrower than the
    design file shows (which lets it fill the column), leaving a large
    unused gap next to the sidebar. Cap removed outright — no reason for a
    hero paragraph to be narrower than its own column."""
    html = _client(env).get("/").text
    assert 'max-width:640px;font-family:var(--font-head)' not in html
    assert 'margin:0 0 12px;max-width:640px' not in html


def test_avatar_image_fills_its_wrapper_at_desktop_size(env):
    """_avatar() bakes a fixed pixel width/height into its <img> (or
    placeholder <div>) inline style — correct for every other call site,
    which all use a single fixed size. The homepage hero photo is the one
    place the wrapper itself resizes (200px -> 240px at the desktop
    breakpoint), so without an override the photo stays pinned at its
    original 200px inside a now-larger frame, opening a gap between it and
    the status box below that isn't in the design. The override must apply
    to the avatar image/placeholder specifically, not to every div inside
    the wrapper — the "hi, I'm Brian" sticker is a sibling div in the same
    wrapper and must NOT get stretched to 100%."""
    html = _client(env).get("/").text
    assert (".home-avatar-wrap>img,.home-avatar-wrap>div[aria-label]"
            "{width:100% !important;height:100% !important;}") in html


def test_status_box_in_normal_flow_not_absolutely_positioned(env):
    """.home-status used to be position:absolute with a hardcoded top
    offset, floating free of .home-photo-wrap's own (also hardcoded)
    height. Admin-edited status copy longer than that guess would overflow
    silently into the Toolbox panel in the next grid row, with nothing to
    catch it. Kept in normal flow (pulled up under the avatar with a
    negative margin-top instead) so .home-photo-wrap's height genuinely
    reflects its content and the grid row sizes itself correctly regardless
    of how long the status copy is."""
    html = _client(env).get("/").text
    # The rule itself: no more position:absolute/top offset, uses margin-top instead.
    assert ".home-status{position:relative;margin-top:-50px;" in html
    assert "position:absolute;top:150px" not in html
    assert "top:190px;left:-30px" not in html


def test_hero_headline_underlines_strategic_partner_not_last_word(env):
    """The hero heading used _underline_last_word(), which always accents
    whatever word happens to end the (admin-editable) headline text — for
    the default copy that's "scorekeeper.", not the design's intended
    "strategic partner" mid-sentence accent. Switched to _underline_phrase()
    targeting "strategic partner" specifically, with a same-behavior
    fallback to the last word if an admin ever rewrites the headline
    without that phrase in it."""
    html = _client(env).get("/").text
    idx = html.find("<h1")
    h1_html = html[idx:html.find("</h1>", idx)]
    assert "<span>strategic partner</span>" in h1_html
    assert "<span>scorekeeper.</span>" not in h1_html
    # The rest of the sentence must still render, just not re-escaped twice
    # and not duplicated around the underlined phrase.
    assert h1_html.count("strategic partner") == 1
    assert "leadership team leans on" in h1_html
    assert "the scorekeeper." in h1_html


# --- Featured-homepage cap of 4 (any mix of types), replacing the deleted
# per-type representative/fallback mechanism above. ---

_CAP_ERROR_TEXT = "All 3 homepage highlight slots are in use"


def _feature_four(lib):
    """(Name kept from when the cap was 4; now creates 3.) Already-featured rows, spread across types on purpose (the cap is
    on the total, not per type) — returns their ids in insertion order."""
    return [
        lib.add_thought_leadership("writing", "Feat A", "https://example.com/a",
                                   "Forbes", "Jan 2026", "2026-01", featured_home=True),
        lib.add_thought_leadership("speaking", "Feat B", "https://example.com/b",
                                   "SaaStr", "Feb 2026", "2026-02", featured_home=True),
        lib.add_thought_leadership("podcast", "Feat C", "https://example.com/c",
                                   "Cash Flow Show", "Mar 2026", "2026-03", featured_home=True),
    ]


def test_fifth_featured_is_refused(env):
    """Regression for correction 2/3: LIMIT 4 makes a 5th row harmless on
    the public homepage, but the ADMIN WRITE PATH must still refuse it —
    shown failing against pre-fix code (no cap check at all: the add
    would have silently succeeded, creating a 5th featured=1 row)."""
    lib = env._lib()
    try:
        _feature_four(lib)
        before_count = len(lib.list_thought_leadership())
        featured_before = lib.count_featured_home()
    finally:
        lib.close()
    assert featured_before == 3

    c = _admin_client(env)
    resp = c.post("/admin/thought-leadership/third-party/new", data={
        "type": "writing", "title": "Fifth Wheel", "url": "https://example.com/fifth",
        "venue": "Forbes", "date_label": "May 2026", "display_order": "",
        "featured_home": "1",
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert _CAP_ERROR_TEXT in resp.text
    # The submitted title survives in the re-rendered form.
    assert 'value="Fifth Wheel"' in resp.text
    # A real bug found mid-build: _tl_form_fields's display_order field used
    # to do `item.get('display_order', '')`, which returns None (not the
    # default '') for a dict that HAS the key with value None — exactly
    # what _tl_form_values produces for a blank display_order. Rendered as
    # the literal string "value=\"None\"", harmless only by browser
    # coincidence (a <input type="number"> silently discards an invalid
    # value and falls back to the placeholder) — real broken markup either
    # way. Pinned here since this exact refusal path is what exposed it.
    assert 'value="None"' not in resp.text

    lib = env._lib()
    try:
        after_count = len(lib.list_thought_leadership())
        featured_after = lib.count_featured_home()
    finally:
        lib.close()
    # DB unchanged: no row was inserted at all, and the featured count
    # didn't move.
    assert after_count == before_count
    assert featured_after == 3


def test_editing_already_featured_row_at_cap_succeeds(env):
    """The exclude_id case — the one most likely to be built wrong.
    Re-saving an already-featured row while all 4 slots are full must
    succeed, not be refused against itself. Shown failing against a
    naive `count_featured_home()` with no exclude_id: that count would
    already be 4 before this save, refusing every edit to any of the 4
    currently-featured rows forever."""
    lib = env._lib()
    try:
        ids = _feature_four(lib)
    finally:
        lib.close()
    item_id = ids[0]

    c = _admin_client(env)
    resp = c.post(f"/admin/thought-leadership/third-party/{item_id}/edit", data={
        "type": "writing", "title": "Feat A Renamed", "url": "https://example.com/a",
        "venue": "Forbes", "date_label": "Jan 2026", "display_order": "0",
        "featured_home": "1",
    }, follow_redirects=False)
    assert resp.status_code == 303

    lib = env._lib()
    try:
        it = lib.get_thought_leadership(item_id)
        featured_after = lib.count_featured_home()
    finally:
        lib.close()
    assert it["title"] == "Feat A Renamed"
    assert it["featured_home"] == 1
    assert featured_after == 3


def test_zero_featured_omits_recent_highlights_section_heading(env):
    """No featured_home=1 rows at all — the "Recent highlights" heading
    and grid must be absent entirely, not rendered empty. The "See all
    thought leadership" nav link (a sibling of the wrap, not inside it)
    must still render — it's the homepage's only path to
    /thought-leadership, and correction accepted this plan specifically
    on the condition that omitting the wrap doesn't remove it."""
    html = _client(env).get("/").text
    assert "Recent highlights" not in html
    assert "See all thought leadership" in html


def test_undated_featured_piece_does_not_sort_to_slot_one(env):
    """Pins the actual defect that motivated this build. Same TYPE for both
    pieces on purpose — under the old per-type get_thought_leadership_
    representative, a same-type pair never both rendered at all (its
    featured branch was `LIMIT 1` per type, `ORDER BY updated_at DESC`):
    only the most-recently-SAVED one showed, with no notion of date_label
    ordering, and the other's checkbox was silently inert (id 34's actual
    production defect). Fails against that old mechanism for two
    independent reasons — it renders only one title, not both, and even
    when both happen to survive it never orders by date. The new featured
    SET query (_TL_FEATURED_ORDER_SQL: sort_key DESC, display_order ASC,
    no undated-first clause — unlike _TL_ORDER_SQL) renders both, dated
    piece first."""
    lib = env._lib()
    try:
        lib.add_thought_leadership("podcast", "Standing Feed", "https://example.com/feed",
                                   "Cash Flow Show", "", "", featured_home=True)
        lib.add_thought_leadership("podcast", "Newer Dated Piece", "https://example.com/newer",
                                   "Cash Flow Show", "Sep 2026", "2026-09", featured_home=True)
    finally:
        lib.close()
    html = _client(env).get("/").text
    assert "Standing Feed" in html
    assert "Newer Dated Piece" in html
    assert html.index("Newer Dated Piece") < html.index("Standing Feed")


def test_admin_list_shows_featured_slot_and_count(env):
    """The admin list's Featured column shows the homepage RENDER slot
    (1-4), not a plain yes/no badge — computed from the same query the
    homepage uses, so it can't disagree with what's actually live. Also
    covers the "N of 4 slots used" line."""
    lib = env._lib()
    try:
        ids = [
            lib.add_thought_leadership("writing", "Newest Featured", "https://example.com/newest",
                                       "Forbes", "Sep 2026", "2026-09", featured_home=True),
            lib.add_thought_leadership("writing", "Older Featured", "https://example.com/older",
                                       "Forbes", "Jan 2026", "2026-01", featured_home=True),
            lib.add_thought_leadership("press", "Not Featured", "https://example.com/not",
                                       "TechCrunch", "Aug 2026", "2026-08", featured_home=False),
        ]
    finally:
        lib.close()
    html = _admin_client(env).get("/admin/thought-leadership/third-party").text
    assert "Homepage highlights: 2 of 3 slots used." in html
    # Newest sort_key renders first (slot 1); older featured piece is slot 2.
    newest_idx = html.index("Newest Featured")
    older_idx = html.index("Older Featured")
    not_featured_idx = html.index("Not Featured")
    assert ">1<" in html[newest_idx:newest_idx + 700]
    assert ">2<" in html[older_idx:older_idx + 700]
    # The unfeatured row's Featured cell is blank, not "0" or a dash.
    row_slice = html[not_featured_idx:not_featured_idx + 700]
    assert ">0<" not in row_slice


def test_checkbox_helper_text_describes_the_four_slot_cap(env):
    """The old per-type helper text ("Represents this entry's type... in
    the homepage's 4-column breakdown") described the deleted mechanism —
    replaced with copy describing the actual rule: 4 slots, any mix of
    types, refused when full."""
    html = _admin_client(env).get("/admin/thought-leadership/third-party/new").text
    assert "4 slots total, any mix of" in html
    assert "Represents this entry's type" not in html
    assert "4-column breakdown" not in html
