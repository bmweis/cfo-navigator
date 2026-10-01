"""Compare shows full field text, never clamped (2026-10).

Brian's standing rule: never cut off text in a profile, a comparison or an
MCP response. This replaces the Compare Redesign Phase 1 Step 0 decision
(a 4-line CSS `-webkit-line-clamp`). Nothing on either Compare table may hide
text, by CSS or otherwise, and every `[n]` marker has its Sources chip.

The Chromium tests measure the real layout (a markup assertion cannot see a
clipped box). They skip cleanly where no Chromium binary exists, as CI does.
"""
import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

LONG = 3000


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


def _long_text(tag: str, markers: int = 6, n: int = LONG) -> str:
    """About n characters of sentences carrying [1]..[markers], ending in a
    sentence that holds the last marker, so a clipped cell would lose it."""
    sentences = []
    i = 0
    while sum(len(s) + 1 for s in sentences) < n - 80:
        i += 1
        sentences.append(f"{tag} sentence {i} says something specific and checkable about this vendor[{(i % markers) + 1}].")
    sentences.append(f"{tag} FINAL SENTENCE closes the field[{markers}].")
    return "\n\n".join(" ".join(sentences[j:j + 4]) for j in range(0, len(sentences), 4))


def _cites(count: int = 6) -> list[dict]:
    return [{"n": k, "title": f"Source {k}", "url": f"https://example.com/s{k}"} for k in range(1, count + 1)]


def _seed_tools(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = []
    for name in ("Longco", "Shortco"):
        t = lib.add_tool(name, "FP&A", f"https://{name.lower()}.example", ["FP&A"], approved=1)
        ids.append(t)
    a, b = ids
    lib.conn.execute("UPDATE tools SET summary=? WHERE id=?", (_long_text("Summary"), a))
    lib.conn.execute("UPDATE tools SET summary=? WHERE id=?", ("A short summary.", b))
    lib.conn.commit()
    # Straight SQL: the write path refuses text over the field limits, but
    # stored rows predating those limits are exactly what Compare must show whole.
    lib.conn.execute("UPDATE tools SET agent_taxonomy_note=?, competitive_differentiation=? WHERE id=?",
                     (_long_text("Agent"), _long_text("Bottom", markers=1), a))
    lib.conn.execute("UPDATE tools SET agent_taxonomy_note=? WHERE id=?", ("Short agent note.", b))
    lib.conn.commit()
    lib.set_entity_citations("tool", a, "agent_taxonomy", _cites(6))
    lib.set_entity_citations("tool", a, "description", _cites(6))
    lib.close()
    return a, b


def _seed_communities(env):
    from linklib.db import Library
    lib = Library(os.environ["LINKLIB_DB"])
    ids = []
    for name, url in (("Longcomm", "https://one.example"), ("Shortcomm", "https://two.example")):
        ids.append(lib.add_community(name, url, "Finance leaders", "Free", [], access="Open", approved=1))
    a, b = ids
    lib.upsert_community_profile(a, ideal_member="x", verdict_summary="x")
    lib.upsert_community_profile(b, ideal_member="Short fit.", verdict_summary="Short verdict.")
    lib.conn.execute("UPDATE community_profiles SET verdict_summary=?, ideal_member=?, value_prop=? WHERE community_id=?",
                     (_long_text("Verdict"), _long_text("Fit", markers=1), _long_text("Value", markers=1), a))
    lib.conn.commit()
    lib.set_entity_citations("community", a, "community_profile", _cites(6))
    lib.close()
    return a, b


# -- markup-level (no browser) ---------------------------------------------

def _cmp_css_rules(html: str) -> list[tuple[str, str]]:
    """Every CSS rule whose selector names a Compare class (.cmp-* / .cc-*)."""
    out = []
    for style in re.findall(r"<style>(.*?)</style>", html, re.S):
        style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
            if re.search(r"\.(cmp|cc)-", sel):
                out.append((sel.strip(), body))
    return out


def _assert_no_hiding_css(html: str):
    assert "cmp-clamp" not in html
    rules = _cmp_css_rules(html)
    assert any(".cmp-text" in sel for sel, _ in rules)
    for sel, body in rules:
        for bad in ("clamp", "max-height", "overflow:hidden", "text-overflow"):
            assert bad not in body.replace(" ", ""), f"{sel} hides text via {bad}"


def test_software_compare_has_no_clamp_and_shows_every_character(env):
    a, b = _seed_tools(env)
    r = _client(env).get(f"/tools/software/compare?ids={a},{b}")
    assert r.status_code == 200
    _assert_no_hiding_css(r.text)
    assert "FINAL SENTENCE closes the field[6]" in r.text      # the very last sentence of the agent field
    from html import escape
    assert escape(_long_text("Agent")) in r.text                # the whole field, byte for byte


def test_community_compare_has_no_clamp_and_shows_every_character(env):
    a, b = _seed_communities(env)
    r = _client(env).get(f"/tools/communities/compare?ids={a},{b}")
    assert r.status_code == 200
    _assert_no_hiding_css(r.text)
    from html import escape
    for tag, m in (("Verdict", 6), ("Fit", 1), ("Value", 1)):
        assert escape(_long_text(tag, markers=m)) in r.text


def test_every_marker_has_its_sources_chip_with_the_same_number(env):
    a, b = _seed_tools(env)
    html = _client(env).get(f"/tools/software/compare?ids={a},{b}").text
    agent_cell = html.split("AI / Agent involvement", 1)[1].split("</tr>", 1)[0]
    marker_ns = {int(n) for n in re.findall(r"\[(\d+)\]", agent_cell.split("Sources", 1)[0])}
    chip_ns = {int(n) for n in re.findall(r">\[(\d+)\] Source \d+</a>", agent_cell)}
    assert marker_ns == {1, 2, 3, 4, 5, 6}
    assert chip_ns == marker_ns, "a marker has no chip, or a chip has no marker"
    # chip n and title agree (the chip labelled [4] is Source 4)
    for n, t in re.findall(r">\[(\d+)\] Source (\d+)</a>", agent_cell):
        assert n == t


def test_sources_are_not_capped_on_compare(env):
    """The profile page caps Sources at 5; on Compare a sixth marker would be
    left without its chip, so Compare shows them all."""
    a, b = _seed_tools(env)
    html = _client(env).get(f"/tools/software/compare?ids={a},{b}").text
    assert "[6] Source 6</a>" in html


def test_mcp_text_is_untouched_by_the_page_change(env):
    from linklib import compare
    assert not hasattr(compare, "EXCERPT_LINE_CLAMP")


# -- real layout (Chromium) -------------------------------------------------

def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    for kwargs in ({}, {"executable_path": "/opt/pw-browsers/chromium"}):
        try:
            pw = sync_playwright().start()
            return pw, pw.chromium.launch(**kwargs)
        except Exception:
            try:
                pw.stop()
            except Exception:
                pass
    return None


_MEASURE = """() => {
  const out = {clipped: [], hidden: [], texts: 0, rows: []};
  const body = document.querySelector('table.cc-table tbody');
  body.querySelectorAll('td.cc-cell, td.cc-cell *').forEach(el => {
    const cs = getComputedStyle(el);
    if (el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 1)
      out.clipped.push([el.tagName, el.className, el.clientHeight, el.scrollHeight]);
    if (el.matches('td.cc-cell, td.cc-cell *') && (cs.overflowY !== 'visible' || cs.maxHeight !== 'none') && el.clientHeight > 0)
      out.hidden.push([el.tagName, el.className, cs.overflowY, cs.maxHeight]);
  });
  out.texts = body.querySelectorAll('td.cc-cell div').length;
  body.querySelectorAll('tr').forEach(tr => {
    const cells = [...tr.querySelectorAll('td.cc-cell')];
    const tops = cells.map(c => Math.round(c.getBoundingClientRect().top));
    out.rows.push({h: Math.round(tr.getBoundingClientRect().height), sameTop: tops.every(t => t === tops[0]),
                   label: cells[0] ? cells[0].textContent.trim().slice(0, 24) : '',
                   labelVAlign: cells[0] ? getComputedStyle(cells[0]).verticalAlign : ''});
  });
  const lab = body.querySelector('td.cc-label');
  out.labelSticky = lab ? getComputedStyle(lab).position : '';
  return out;
}"""


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("page_name", ["software", "community"])
def test_no_compare_cell_clips_its_text_in_a_real_browser(env, tmp_path, width, page_name):
    launched = _launch()
    if launched is None:
        pytest.skip("Chromium not available in this environment")
    pw, browser = launched
    try:
        if page_name == "software":
            a, b = _seed_tools(env)
            url = f"/tools/software/compare?ids={a},{b}"
        else:
            a, b = _seed_communities(env)
            url = f"/tools/communities/compare?ids={a},{b}"
        html = _client(env).get(url).text
        f = tmp_path / "compare.html"
        f.write_text(html, encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        m = page.evaluate(_MEASURE)
        assert m["clipped"] == [], f"cells whose content is taller than their box: {m['clipped']}"
        assert m["hidden"] == [], f"overflow or max-height set on a cell: {m['hidden']}"
        for row in m["rows"]:
            assert row["sameTop"], f"cells of one row do not top-align: {row}"
            assert row["labelVAlign"] == "top", row
        if width <= 700:
            assert m["labelSticky"] == "sticky"
        page.close()
    finally:
        browser.close()
        pw.stop()
