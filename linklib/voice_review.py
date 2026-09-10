"""Voice review — keep written content on the site's voice guide.

Two layers, mirroring the two kinds of rules in the DB-backed voice guide
(``linklib.agent.VOICE_CORE_DEFAULT`` / ``voice_core`` setting):

* **Mechanical** (`mechanical_findings`) — deterministic, no API. The hard rules
  that can be checked with string matching: banned buzzwords, filler phrases,
  performative openers/closers. Used by the automated QA test and surfaced in the
  on-demand reviewer. Kept intentionally conservative — only unambiguous offenders,
  so it never false-positives on legitimate finance copy (e.g. "leverage" the noun,
  "actually"/"honestly" which are only banned *as filler*) — those judgment calls
  are left to the holistic review.
* **Holistic** (`review_text`) — calls Claude with the voice guide as the rubric to
  judge tone ("does this sound on-voice"). Needs ``anthropic`` + ``ANTHROPIC_API_KEY``;
  imported lazily so this module stays dependency-free for the test.
* **Typographic** (`typography_findings`) — deterministic too, but it takes PYTHON
  SOURCE rather than plain text, because the two rules it enforces (bare ampersands,
  spaced em dashes) are only meaningful against UI copy and would false-positive
  constantly against code. See that function's own docstring for the scoping rules.
"""
from __future__ import annotations

import ast
import os
import re

from .voice_mechanics import _SPACED_EM_DASH

# --- Deterministic rules (single source of truth) ---------------------------
# Unambiguous buzzwords from the voice guide's "Avoid" list. Context-dependent
# ones ("leverage", "journey", "actually", "honestly", "genuinely") are
# deliberately NOT here — the holistic review handles those to avoid false positives.
BANNED_WORDS = [
    "delve", "robust", "seamless", "synergy",
    "transformative", "game-changer", "game changer",
]
FILLER_PHRASES = [
    "at the end of the day", "needless to say", "in order to",
    "it's worth noting", "it is worth noting",
]
PERFORMATIVE = [
    "i'm excited to share", "i am excited to share", "thrilled to",
    "onward!", "excited for what's next", "without further ado",
]


def mechanical_findings(text: str) -> list[tuple[str, str]]:
    """Deterministic voice violations as (rule, matched_phrase). No API calls."""
    low = text.lower()
    findings: list[tuple[str, str]] = []
    for w in BANNED_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            findings.append(("buzzword", w))
    for p in FILLER_PHRASES:
        if p in low:
            findings.append(("filler", p))
    for p in PERFORMATIVE:
        if p in low:
            findings.append(("performative", p))
    return findings


# --- Typographic rules over UI copy in Python source ------------------------
# Two of voice_core's HARD MECHANICAL RULES that `mechanical_findings` above
# can't enforce, because they're only meaningful against UI copy and would
# false-positive relentlessly against code:
#
#   * "Spell out 'and'; never '&' except in terms like FP&A."
#   * "Emdashes have NO surrounding spaces."
#
# The em-dash half deliberately reuses `voice_mechanics._SPACED_EM_DASH` — the
# same regex the DB-write backstop already applies to every AI-drafted prose
# field — rather than defining a second, driftable idea of what "spaced em
# dash" means. This is the SOURCE-side half of that same rule: the backstop
# normalizes what Claude writes into the database, this catches what a human
# hand-types into webapp/app.py's inline HTML, which never passes through
# Library's write methods at all.
#
# SCOPE, and it matters: Python STRING LITERALS ONLY, docstrings excluded, and
# with embedded CSS/JS/HTML comments stripped out of each literal before it's
# scanned. Everything outside that is code or commentary, not copy:
#   * Python `#` comments are never string literals, so the AST walk skips them
#     for free.
#   * Docstrings ARE string literals, so they're excluded explicitly.
#   * webapp/app.py's inline `<style>`/`<script>` blocks live INSIDE string
#     literals and are full of `/* ... */` prose (the :root token table alone
#     accounts for hundreds of ` — ` spans) — those get stripped per-literal.
# Database-rendered content is never scanned: a real vendor or community name
# legitimately contains an ampersand (Bain & Company, Ernst & Young), and
# rewriting one would corrupt a real entity name. site_copy rows are Brian's
# own copy but live in the DB and are edited at /admin/copy, so they're out of
# scope here too — report them, don't rewrite them.

# Standard finance/business abbreviations that carry '&' as part of the term
# itself. Matches voice_core's own generalized carve-out (see the 2026-08
# "T and E" fix): a stated principle with examples, not a closed list — add a
# row when a real term turns up, don't spell the term out to satisfy the lint.
AMPERSAND_ACRONYMS = ["FP&A", "R&D", "Q&A", "P&L", "M&A", "S&P", "S&M", "D&A", "T&E"]

# Proper nouns and standard line-item names where the ampersand is part of the
# name, not a lazy stand-in for "and". Same "add a real one when it turns up"
# discipline as the acronym list above.
AMPERSAND_NAMES = [
    "Sales & Marketing",       # the GAAP line item, spelled out on the Growth Engine calculator
    "Research & Development",  # ditto
]

# Code that legitimately contains an ampersand inside a string literal, removed
# before scanning: JS's `&&` (HTML-escaped or raw) and the `&`-to-`&amp;`
# replacement every inline HTML-escaping helper in webapp/app.py performs.
_AMP_CODE_PATTERNS = [
    re.compile(r"&amp;&amp;"),
    re.compile(r"&&"),
    re.compile(r"replace\(\s*/&/g\s*,\s*'&amp;'\s*\)"),
    # A URL query string: `?a=1&amp;b=2`. The ampersand there is a parameter
    # separator, not the word "and" — no copy in webapp/app.py currently has
    # a two-parameter link, but the next one added would otherwise be a
    # guaranteed false positive.
    re.compile(r"""\?[A-Za-z_][\w\-]*=[^\s"'<>]*"""),
]

# A literal that is nothing but one HTML entity is an escape-map value, never
# copy — `_esc()`'s own `.replace("&", "&amp;")` is the case that motivated
# this. Skipped whole rather than pattern-masked, because at the AST level the
# literal really is just the four characters `&amp;` with no context attached.
# Matched against the literal's source segment, so the quoting is part of it.
_LONE_ENTITY = re.compile(r"""^[rfbu]*(?P<q>'''|\"\"\"|'|")&(?:amp|lt|gt|quot|\#\d+);(?P=q)$""")

# An ampersand that actually renders to the reader: the HTML entity, or a bare
# '&' standing alone as a word between whitespace/tag boundaries. Deliberately
# NOT a bare `&` anywhere — that would match every other HTML entity
# (`&mdash;`, `&rarr;`, `&nbsp;`) and every URL query separator.
_BARE_AMPERSAND = re.compile(r"&amp;|(?<=[\s>])&(?=[\s<])")

# The entity spelling of a spaced em dash. `_SPACED_EM_DASH` (imported from
# voice_mechanics) covers the literal character; HTML source can also write the
# identical rendered result as `&mdash;` with `&nbsp;`/space on both sides.
_SPACED_MDASH_ENTITY = re.compile(r"(?:\s|&nbsp;)+&mdash;(?:\s|&nbsp;)+")

_EMBEDDED_COMMENTS = [
    re.compile(r"/\*.*?\*/", re.S),      # CSS and JS block comments
    re.compile(r"<!--.*?-->", re.S),     # HTML comments
    re.compile(r"(?m)^[ \t]*//.*$"),     # whole-line JS comments
]


def strip_embedded_comments(text: str) -> str:
    """Blank out CSS/JS/HTML comments inside a string literal.

    Replaces each comment with spaces of the same length so every surviving
    offset still lines up with the original literal — the fixer relies on that
    to splice a correction back into the real source.
    """
    for rx in _EMBEDDED_COMMENTS:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _mask(text: str, patterns) -> str:
    """Same length-preserving blanking, for the allowlisted spans."""
    for rx in patterns:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def scannable_copy(literal: str) -> str:
    """One string literal reduced to just the parts that are real UI copy.

    Comments, JS ampersand code, and every allowlisted ampersand term are
    blanked to same-length whitespace, so what's left is only text a reader
    actually sees and offsets still map back to the original literal.
    """
    text = strip_embedded_comments(literal)
    text = _mask(text, _AMP_CODE_PATTERNS)
    allow = [re.compile(re.escape(t).replace(r"\&", r"(?:&amp;|&)"), re.IGNORECASE)
             for t in AMPERSAND_NAMES + AMPERSAND_ACRONYMS]
    return _mask(text, allow)


def _copy_literals(source: str) -> list[tuple[int, str]]:
    """(line number, source text) for every non-docstring string literal.

    Deliberately returns each literal's SOURCE SEGMENT, not the evaluated
    `ast.Constant` value. An f-string's value is split into one Constant per
    fragment at each `{...}` boundary, so an embedded CSS or JS comment that
    happens to interpolate something — `/* ... — ~{compare.EXCERPT_LINE_CLAMP}
    lines */`, `// (`/admin/tools/{id}/delete`) ... — a plain 404` — arrives as
    two fragments with the comment's opener in one and its closer in the other,
    and no per-fragment stripper can pair them up. Both of those are real
    comments in webapp/app.py that a value-based scan flagged as copy. Reading
    the whole segment keeps the comment intact so `strip_embedded_comments`
    can see both ends of it.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    nodes = [n for n in ast.walk(tree)
             if (isinstance(n, ast.JoinedStr)
                 or (isinstance(n, ast.Constant) and isinstance(n.value, str)))
             and id(n) not in docstrings and n.end_lineno is not None]

    # Absolute character offset of the start of each 1-based line, computed
    # once — `ast.get_source_segment` re-splits the whole file per call, which
    # is minutes rather than seconds across webapp/app.py's ~8k literals.
    starts = [0]
    for line in source.split("\n"):
        starts.append(starts[-1] + len(line) + 1)

    # Outermost span wins: an f-string's own fragments are nested inside it, and
    # scanning both would double-report every finding.
    spans = sorted({(starts[n.lineno - 1] + n.col_offset,
                     starts[n.end_lineno - 1] + n.end_col_offset,
                     n.lineno) for n in nodes})
    out: list[tuple[int, str]] = []
    reach = -1
    for begin, end, lineno in spans:
        if begin < reach:
            continue
        reach = end
        out.append((lineno, source[begin:end]))
    return out


def typography_findings(source: str) -> list[tuple[str, int, str]]:
    """Bare ampersands and spaced em dashes in UI copy, as (rule, line, excerpt).

    `source` is Python source text (webapp/app.py in practice), not rendered
    HTML and not database content — see the scoping note above this function.
    """
    findings: list[tuple[str, int, str]] = []
    for lineno, literal in _copy_literals(source):
        if _LONE_ENTITY.match(literal.strip()):
            continue
        copy = scannable_copy(literal)
        for rule, rx in (("bare-ampersand", _BARE_AMPERSAND),
                         ("spaced-em-dash", _SPACED_EM_DASH),
                         ("spaced-em-dash", _SPACED_MDASH_ENTITY)):
            for m in rx.finditer(copy):
                start, end = max(0, m.start() - 40), m.end() + 40
                excerpt = " ".join(literal[start:end].split())
                findings.append((rule, lineno, excerpt))
    return findings


# --- Holistic tone review (Claude) ------------------------------------------

def review_text(text: str, voice_prompt: str | None = None, model: str | None = None) -> dict:
    """Review `text` against the voice guide.

    Returns {"mechanical": [...], "review": str, "ok": bool|None}. `ok` is None when
    the LLM step is unavailable (no SDK / no key) — the mechanical findings still apply.
    """
    from .agent import VOICE_CORE_DEFAULT, DEFAULT_MODEL

    voice = (voice_prompt or VOICE_CORE_DEFAULT).strip()
    model = model or DEFAULT_MODEL
    mechanical = mechanical_findings(text)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return {"mechanical": mechanical, "review": "(Install `anthropic` to enable tone review.)", "ok": None}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"mechanical": mechanical, "review": "(Set ANTHROPIC_API_KEY to enable tone review.)", "ok": None}

    system = (
        "You are a careful editor checking whether a piece of writing matches a specific "
        "author's voice. Here is the author's voice guide:\n\n" + voice + "\n\n"
        "Review the user's text against this guide. Be concise and specific. Respond with:\n"
        "1. A one-line verdict: On voice / Minor drift / Off voice.\n"
        "2. Up to 5 bullet flags — each quotes the phrase, names the rule it bends, and gives a short fix.\n"
        "Judge tone and the hard rules, not formatting of UI labels. If it's clean, say so plainly. "
        "Do not rewrite the whole piece."
    )
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=900, system=system,
        messages=[{"role": "user", "content": text}],
    )
    review = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return {"mechanical": mechanical, "review": review, "ok": True}
