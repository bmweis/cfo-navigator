"""Restricted markdown rendering for AI-drafted narrative fields.

Real Markdown/List Rendering for Narrative Fields (2026-09) — the short
AI-drafted fields (tool Description/Agent taxonomy/Bottom line, community
profile group fields) previously rendered as `_esc(text)` inside a
`white-space:pre-wrap` `<p>`, so a "- " bulleted line the generation prompt's
own `_STRUCTURE_GUIDANCE` (linklib/enrich.py) asks for never became a real
`<ul><li>` — it just survived as literal dashes-and-linebreaks text. This
module gives those fields the same real rendering `_render_original_content_
markdown` already gives the 3 native `original_content` long-form posts
(`webapp/app.py`), through the same already-present `python-markdown`
dependency — no new library.

This is intentionally `webapp`-side, not `linklib`-side: `linklib/gates.py`'s
own design principle is that nothing HTML-producing is reachable from
`linklib`, specifically so a future MCP tool importing `linklib` directly can
never accidentally pull in an HTML fragment. This module's entire job is
producing an HTML string, so it belongs here instead.

Two real differences from `_render_original_content_markdown`, both
deliberate:

1. **Input is pre-escaped, output is a restricted feature subset.**
   `original_content.body_md` is Brian-authored and trusted — raw HTML
   passthrough there is a deliberate, documented choice (see `webapp/app.py`'s
   `_render_original_content_markdown`). The fields this module renders are
   Claude-drafted, not adversarial, but never previously trusted with markup
   either (they went through `_esc()` before this change) — so this module
   HTML-escapes the input first (`html.escape(text, quote=False)`, matching
   `_esc`'s own behavior on quotes) and only then hands it to a `Markdown`
   instance with every block/inline processor deregistered EXCEPT the ones
   that produce paragraphs, unordered/ordered lists, and bold/italic
   emphasis. Escaping first means a literal `<script>`/`<img onerror=...>`
   in a draft renders as inert text (`&lt;script&gt;...`), not a tag — the
   `html` deregistration below is belt-and-suspenders on top of that, not
   the only guard. Verified directly (see `tests/test_markdown_render.py`)
   rather than assumed from python-markdown's docs — `safe_mode` was removed
   in python-markdown 3.0, so pre-escaping is the documented replacement,
   not a shortcut.
2. **No headers, blockquotes, links/images, code, or horizontal rules** —
   deregistered outright rather than merely unused. None of the generation
   prompts (`_STRUCTURE_GUIDANCE`, or the Community-profile/Differentiation
   prompts' own explicit "no markdown syntax" instruction) ever ask a draft
   to produce any of these, so there is no live case that needs them, and a
   narrower feature set is less surface for a future prompt regression (or a
   model that ignores instructions) to exploit. Bold and italic are both
   kept, not just bold — python-markdown's emphasis processors don't cleanly
   separate "**bold**" from "*italic*" (both route through the same
   asterisk/underscore inline processors), and italic is harmless prose
   styling, so this treats "bold" in the brief as "inline emphasis" rather
   than building a bespoke bold-only pattern for no real benefit.

A single-newline "hard break" (two trailing spaces) still produces `<br>` —
harmless, and not disabled — but an ordinary single newline with no blank
line between two lines of prose is NOT converted to a line break (no nl2br):
`_STRUCTURE_GUIDANCE` already asks for a genuine blank line between distinct
paragraph ideas, and a list already renders correctly with consecutive
single-newline-separated "- " lines (core `ulist`/`olist` block processors
handle that natively, confirmed directly) — so nl2br was not needed to match
the prompts' own instructed shape.
"""
from __future__ import annotations

import html

import markdown

# Block processors kept: 'empty' (blank-line handling), 'indent' (list-item
# continuation), 'olist'/'ulist' (the actual point of this module),
# 'paragraph' (plain prose). Deregistered: 'code' (fenced/indented code
# blocks), 'hashheader'/'setextheader' (# and === headers), 'hr' (horizontal
# rules), 'quote' (blockquotes), 'reference' (link reference definitions) —
# none of the AI-drafted fields this renders are ever asked to produce any
# of these.
_DISABLED_BLOCK_PROCESSORS = (
    "code", "hashheader", "setextheader", "hr", "quote", "reference",
)

# Inline patterns kept: 'escape' (\* etc.), 'linebreak' (trailing two-space
# hard break), 'entity' (numeric/named HTML entities), 'not_strong'/
# 'em_strong'/'em_strong2' (bold + italic emphasis). Deregistered: every
# link/image/autolink/reference variant, 'html' (raw inline HTML — the
# pre-escape above already neutralizes this; deregistering it too means
# nothing even attempts to parse an escaped "&lt;tag&gt;" as HTML), and
# 'backtick' (inline code spans) — none of these are asked for by any
# generation prompt this module serves.
_DISABLED_INLINE_PATTERNS = (
    "reference", "link", "image_link", "image_reference",
    "short_reference", "short_image_ref", "autolink", "automail",
    "html", "backtick",
)

# 'html_block' is the preprocessor that stashes a raw HTML block for later
# raw insertion — moot once input is pre-escaped (there's no unescaped '<'
# left for it to find), deregistered anyway as defense in depth.
_DISABLED_PREPROCESSORS = ("html_block",)


def _build_restricted_markdown() -> markdown.Markdown:
    md = markdown.Markdown(extensions=[])
    for name in _DISABLED_BLOCK_PROCESSORS:
        md.parser.blockprocessors.deregister(name)
    for name in _DISABLED_INLINE_PATTERNS:
        md.inlinePatterns.deregister(name)
    for name in _DISABLED_PREPROCESSORS:
        md.preprocessors.deregister(name)
    return md


def render_narrative_markdown(text: str) -> str:
    """Render an AI-drafted narrative field (tool Description/Agent
    taxonomy/Bottom line, a community profile field) as restricted-markdown
    HTML — real paragraphs/lists/bold, everything else escaped to inert
    text. Empty input returns "". A fresh `Markdown` instance is built per
    call (the instance is stateful/single-use per the library's own docs —
    `Markdown.convert()` isn't safely reentrant across calls without
    `.reset()`), which is fine at this call volume (a handful of fields per
    page render, not a hot loop).
    """
    text = (text or "").strip()
    if not text:
        return ""
    escaped = html.escape(text, quote=False)
    return _build_restricted_markdown().convert(escaped)
