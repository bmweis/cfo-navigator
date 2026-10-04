"""Status chips (BRAND.md, Status chips): every rating, Private and Hidden marker on
a Buddy question is drawn by ONE function, _ask_status_chip, and nothing else."""
import re
from html import unescape

import webapp.app as appmod
from linklib.voice_review import mechanical_findings, typography_findings_plain

SRC = open("webapp/app.py").read()


def test_chip_set_and_labels():
    exp = {"helpful": ("Rated helpful", "✓", "Helpful"),
           "not_helpful": ("Rated not helpful", "✕", "Not helpful"),
           "mixed": ("Rated mixed", "±", "Mixed"),
           "private": ("Private", "\U0001F512", "Private"),
           "hidden": ("Hidden by an admin", "⊘", "Hidden")}
    for kind, (label, glyph, word) in exp.items():
        html = appmod._ask_status_chip(kind)
        assert f'aria-label="{label}"' in html and f'title="{label}"' in html
        assert unescape(html).count(glyph) == 1 and f"</span> {word}</span>" in html
        assert 'role="img"' in html and "<button" not in html and "<a " not in html


def test_rendered_chips_have_no_invisible_or_flagged_characters():
    for kind in appmod._ASK_STATUS_CHIPS:
        text = unescape(re.sub(r"<[^>]+>", "", appmod._ask_status_chip(kind)))
        assert not mechanical_findings(text) and not typography_findings_plain(text), kind
        assert all(ord(c) not in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0xFE0F) for c in text)
        glyph = text.split(" ")[0]
        assert len(glyph) == 1, kind                    # one plain code point, no ZWJ or selector


def test_order_is_hidden_private_rating_and_unrated_is_empty():
    f = appmod._ask_status_chips_html
    assert f(0, 0) == ""
    out = f(1, 1, private=True, hidden=True, viewer_is_admin=True)
    assert out.index("ask-chip-hidden") < out.index("ask-chip-private") < out.index("ask-chip-mixed")
    assert "ask-chip-hidden" not in f(1, 0, private=True, hidden=True, viewer_is_admin=False)   # Hidden is admin-only
    assert f(0, 2) == appmod._ask_status_chip("not_helpful")


def test_every_status_render_goes_through_the_one_function():
    """No hand-built chip markup, glyph or aria-label anywhere else in app.py."""
    assert SRC.count('class="ask-chip ask-chip-{kind}"') == 1
    assert SRC.count("ask-chip ask-chip-") == 1          # only the builder writes the class pair
    for label in ("Rated helpful", "Rated not helpful", "Rated mixed", "Hidden by an admin"):
        assert SRC.count(f'"{label}"') == 1, label        # defined once, in the table
    for ent in ("&#10003;", "&#10005;", "&#177;", "&#8856;"):
        assert SRC.count(f'"{ent}"') == 1, ent
    # the lock entity is used by the chip table and by the Reader's unrelated Paywalled badge only
    assert SRC.count("&#128274;") == 2
    for gone in ("_ask_rating_html", "ask-pq-rate", "ask-pq-slot", "ask-pq-priv", "ask-pq-hidden", "&#128683;"):
        assert gone not in SRC, gone


def test_the_buddy_page_scripts_take_chips_from_the_server():
    js = SRC[SRC.index("function recentItemHtml"):SRC.index("var RECENT_MAX")]
    assert "PRIVATE_CHIP_HTML" in js and "&#128274;" not in js
    sim = SRC[SRC.index("function simItemHtml"):SRC.index("var resume = x.resume_id")]
    assert "chips_html" in sim and "&#128274;" not in sim and "&#128683;" not in sim
    assert appmod._ask_status_chip("private") in unescape_json(appmod._ASK_PRIVATE_CHIP_JS)


def unescape_json(js_literal):
    import json
    return json.loads(js_literal.replace("<\\/", "</"))


def test_chip_css_is_a_status_not_a_button():
    css = "\n".join(l for l in SRC.splitlines() if l.startswith(".ask-chip"))
    base = [l for l in css.splitlines() if l.startswith(".ask-chip{")][0]
    assert "height:18px" in base and "border:" not in base and "cursor" not in base and "background:var(--line)" in base
    hid = [l for l in css.splitlines() if l.startswith(".ask-chip-hidden{")][0]
    assert "color:var(--seafoam-deep)" in hid and "background:none" in hid and "border" not in hid
