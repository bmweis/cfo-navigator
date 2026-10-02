"""/tools/resources at sparse counts. Rule (BRAND.md section 5, card widths):
cards keep their floor width and the container distributes them
(`auto-fill`, never `auto-fit`). With auto-fit a lone card stretches to the
whole row. Rendered with 1, 2 and 3 cards per section at 1280px and 390px in
real Chromium (skips where none exists, as in CI). Measured engine: Chromium
only; WebKit is unverified here."""
import importlib
import os
import tempfile

import pytest


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import webapp.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    yield TestClient(appmod.app), appmod, db
    if os.path.exists(db):
        os.remove(db)


def _page_html(env, n):
    client, appmod, db = env
    lib = appmod.Library(db)
    for i in range(n):
        lib.add_benchmark(f"Bench {i} with a medium length name", f"https://ex{i}.example",
                          "Best for SaaS CFOs benchmarking growth and efficiency across stages, "
                          "with a long descriptive sentence that wraps onto more lines.",
                          "Private", "free", "benchmarking")
        lib.add_benchmark(f"Book {i}", f"https://bk{i}.example", "Short.", "Private", "free", "books")
    lib.close()
    return client.get("/tools/resources").text


@pytest.mark.parametrize("width", [1280, 390])
def test_sparse_rows_keep_card_width_and_height(env, tmp_path, width):
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception:
        pytest.skip("Chromium not installed in this environment")
    try:
        client, appmod, db = env
        html = _page_html(env, 3)
        f = tmp_path / "r.html"
        f.write_text(html, encoding="utf-8")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f.as_uri())
        grids = page.evaluate("""() => [...document.querySelectorAll('div[style*="display:grid"]')]
          .filter(g => g.querySelector('.bench-card')).map(g => ({
            gridW: g.getBoundingClientRect().width,
            tracks: getComputedStyle(g).gridTemplateColumns.split(' ').length,
            cards: [...g.querySelectorAll('.bench-card')].map(c => {
              const r = c.getBoundingClientRect(); return [r.width, r.height]; })}))""")
        page.close()
        assert len(grids) == 2
        floor = appmod._CARD_WIDTH_RESOURCE_MIN
        for g in grids:
            widths = {round(w) for w, h in g["cards"]}
            heights = {round(h) for w, h in g["cards"]}
            assert len(widths) == 1 and len(heights) == 1, g
            assert min(widths) >= min(floor, g["gridW"]), g
            # auto-fill reserves every track a full row has room for, so a
            # card in a sparse row is no wider than a card in a full row.
            expected_tracks = max(1, int((g["gridW"] + 14) // (floor + 14)))
            assert g["tracks"] == expected_tracks, (g["tracks"], expected_tracks, g)
            assert max(widths) <= g["gridW"] / expected_tracks + 1, g
    finally:
        browser.close()
        pw.stop()
