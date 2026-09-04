"""Unit tests for webapp/markdown_render.py — Real Markdown/List Rendering
for Narrative Fields (2026-09). Pure function tests, no DB/app boot needed.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from webapp.markdown_render import render_narrative_markdown


def test_empty_and_blank_return_empty_string():
    assert render_narrative_markdown("") == ""
    assert render_narrative_markdown(None) == ""
    assert render_narrative_markdown("   \n  ") == ""


def test_plain_paragraphs_render_as_p_tags():
    html = render_narrative_markdown("First idea here.\n\nSecond idea here.")
    assert "<p>First idea here.</p>" in html
    assert "<p>Second idea here.</p>" in html


def test_bulleted_list_becomes_real_ul_li():
    text = "- Contract Review Agent—extracts key terms\n- Approval Agent—routes for sign-off"
    html = render_narrative_markdown(text)
    assert "<ul>" in html and "</ul>" in html
    assert "<li>Contract Review Agent—extracts key terms</li>" in html
    assert "<li>Approval Agent—routes for sign-off</li>" in html
    # The literal bug this PR fixes: dashes surviving as literal text.
    assert "- Contract Review Agent" not in html


def test_ordered_list_becomes_real_ol_li():
    html = render_narrative_markdown("1. First step\n2. Second step")
    assert "<ol>" in html
    assert "<li>First step</li>" in html
    assert "<li>Second step</li>" in html


def test_bold_renders_as_strong():
    html = render_narrative_markdown("This is **bold** text.")
    assert "<strong>bold</strong>" in html


def test_italic_renders_as_em():
    html = render_narrative_markdown("This is *italic* text.")
    assert "<em>italic</em>" in html


def test_raw_script_tag_is_escaped_not_executed():
    html = render_narrative_markdown("<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_raw_img_onerror_is_escaped():
    html = render_narrative_markdown('<img src=x onerror=alert(1)>')
    assert "<img" not in html
    assert "&lt;img" in html


def test_markdown_link_syntax_does_not_become_a_link():
    # No link support — the prompts never ask for links and this keeps the
    # feature set minimal. Left as literal text.
    html = render_narrative_markdown("[click here](javascript:alert(1))")
    assert "<a " not in html
    assert "href" not in html


def test_hash_header_syntax_does_not_become_a_heading():
    html = render_narrative_markdown("# Not a real heading\nJust text.")
    assert "<h1>" not in html
    assert "<h2>" not in html


def test_blockquote_syntax_does_not_become_a_blockquote():
    html = render_narrative_markdown("> not a quote")
    assert "<blockquote>" not in html


def test_fenced_code_does_not_become_a_code_block():
    html = render_narrative_markdown("```\nsome code\n```")
    assert "<pre>" not in html
    assert "<code>" not in html


def test_ampersand_and_quotes_survive_correctly():
    html = render_narrative_markdown('AT&T uses "quotes" & ampersands.')
    assert "AT&amp;T" in html
    assert "&amp; ampersands" in html


def test_realistic_multi_paragraph_and_list_content():
    text = (
        "Concourse gives finance teams a roster of purpose-built agents.\n\n"
        "- Contract Review Agent—extracts key terms\n"
        "- Close Assistant—flags anomalies during month-end\n\n"
        "Each agent runs inside existing approval workflows."
    )
    html = render_narrative_markdown(text)
    assert html.count("<p>") == 2
    assert "<ul>" in html
    assert html.index("<p>Concourse") < html.index("<ul>") < html.index("<p>Each agent")
