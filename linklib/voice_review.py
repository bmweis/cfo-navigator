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
    # Added 2026-09 (voice-enforcement PR) — voice_core's own prose already
    # names this as a generic-hedging example ("don't pad the gap with
    # generic hedging"), quoted verbatim, but it was never in this list —
    # found by voice_core_gap_problems() below, which exists specifically
    # to catch a rubric promising a rejection the mechanical lists don't
    # enforce. See that function's docstring.
    "there are many factors to consider",
]
PERFORMATIVE = [
    "i'm excited to share", "i am excited to share", "thrilled to",
    "onward!", "excited for what's next", "without further ado",
]

# A rubric line that enumerates words/phrases NOT to write — the model reads
# these as instructions, but the words inside are cited as examples of what
# to avoid, not live copy that violates them. Masking is scoped to the
# enumeration itself (the marker phrase through the next sentence-ending
# period), never the whole string literal, so a real mechanical violation
# elsewhere in the same literal — even the same sentence, before the marker —
# still gets caught. `[^.]` already matches a newline (only a bare `.` needs
# DOTALL to do that), so this spans a line-wrapped enumeration for free.
#
# Curated, not general — same "add a real one when it turns up" discipline
# as AMPERSAND_NAMES/AMPERSAND_ACRONYMS below: a marker is added here only
# once real source needs it, never guessed at. Found via linklib/enrich.py's
# "No marketing language: no 'powerful,' 'seamless,' ..." rule text (which
# quotes BANNED_WORDS members as cited bad examples) — the identical shape
# recurs in linklib.agent.VOICE_CORE_DEFAULT's own "- Avoid: ... delve,
# robust, seamless, ..." line, not yet in a scanned file, kept here so a
# future file sweep doesn't have to rediscover this.
_RUBRIC_ENUMERATION_RE = re.compile(
    r"(?:No marketing language:|-\s*Avoid:)[^.]*\.", re.IGNORECASE,
)


def _mask_rubric_enumerations(text: str) -> str:
    """Same-length blanking (never trims) of a rubric's own "words to avoid"
    listing, so offsets any caller computes against the original `text`
    stay valid. See `_RUBRIC_ENUMERATION_RE`'s own comment for scope."""
    return _RUBRIC_ENUMERATION_RE.sub(lambda m: " " * len(m.group(0)), text)


def mechanical_findings(text: str) -> list[tuple[str, str]]:
    """Deterministic voice violations as (rule, matched_phrase). No API calls.

    Scans `text` with a rubric's own "words to avoid" enumeration masked out
    first (see `_mask_rubric_enumerations`) — a prompt telling Claude not to
    write "seamless" is not itself a violation of that rule.
    """
    low = _mask_rubric_enumerations(text).lower()
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


# --- Semantic contradiction: does voice_core promise a rejection the ------
# --- mechanical lists don't enforce? ---------------------------------------
# The mechanical contradiction (voice_review.py disagreeing with itself) is
# now impossible by construction — there's one copy of the lists. This is
# the other kind: voice_core's PROSE names a word or phrase as unwanted, in
# quotes, but the word never made it into BANNED_WORDS/FILLER_PHRASES/
# PERFORMATIVE, so nothing actually rejects it in real copy.
#
# One direction only, deliberately: a list entry the prose doesn't mention
# is fine (the lists are allowed to be more specific than the rubric) — only
# a prose-named term with no mechanical backing is a problem.
#
# Extraction is deliberately narrowed to quoted spans of 2+ words. Checked
# against the real VOICE_CORE_DEFAULT before shipping (not assumed): a bare
# regex over every double-quoted span there flags `"&"`, `"and"`, and `"to"`
# too — single-word/character asides quoted for an unrelated reason (the
# ampersand-spelling rule, and an arrow-notation replacement suggestion),
# not "avoid this phrase" examples. Every FILLER_PHRASES/PERFORMATIVE
# example in that same text is a real phrase (2+ words), so a word-count
# floor removes exactly the false positives and none of the real signal.
# BANNED_WORDS' own members never appear quoted in voice_core (they're
# listed bare, comma-separated, after "- Avoid:") — that line is already
# masked by `_mask_rubric_enumerations` above and, being a verbatim copy of
# BANNED_WORDS itself, has nothing new to find anyway.
_QUOTED_TERM_RE = re.compile(r'"([^"]+)"')


def quoted_voice_examples(voice_core_text: str) -> list[str]:
    """Deduped 2+-word quoted phrases in `voice_core_text` — the exact
    candidate set `voice_core_gap_problems` below checks one by one.
    Factored out (2026-09) so a caller reporting execution ("checked N
    examples") can never drift from what was actually checked — the same
    reasoning `linklib.voice_db_scan.scan_db_copy_report` uses to report
    tables/columns scanned alongside violations found, not just the
    violations. Public, not a leading-underscore private helper, precisely
    because it's meant to be called from outside this module."""
    seen: list[str] = []
    for term in _QUOTED_TERM_RE.findall(voice_core_text):
        if len(term.split()) < 2 or term in seen:
            continue
        seen.append(term)
    return seen


def voice_core_gap_problems(voice_core_text: str) -> list[str]:
    """Quoted 2+-word phrases in `voice_core_text` that `mechanical_findings`
    would not catch if they appeared as real copy — i.e. the rubric cites an
    example the machine doesn't actually enforce. Reuses `mechanical_findings`
    itself as the "is this covered" oracle (run against the quoted term
    alone) rather than reimplementing containment logic, so this can never
    disagree with what real copy scanning actually does."""
    problems: list[str] = []
    for term in quoted_voice_examples(voice_core_text):
        if not mechanical_findings(term):
            problems.append(
                f'"{term}" is named in the voice guide but not enforced by '
                f"BANNED_WORDS/FILLER_PHRASES/PERFORMATIVE"
            )
    return problems


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
# rewriting one would corrupt a real entity name. Brian's own editable copy is
# out of scope for the same reason — it lives in `settings` (there is no
# site_copy table) and is edited at /admin/copy/* and /admin/voice, so a
# violation there gets reported to him, never rewritten from code.

# Standard finance/business abbreviations that carry '&' as part of the term
# itself. Matches voice_core's own generalized carve-out (see the 2026-08
# "T and E" fix): a stated principle with examples, not a closed list — add a
# row when a real term turns up, don't spell the term out to satisfy the lint.
AMPERSAND_ACRONYMS = ["FP&A", "R&D", "Q&A", "P&L", "M&A", "S&P", "S&M", "D&A", "T&E",
                       "G&A", "L&D"]

# Proper nouns and standard line-item names where the ampersand is part of the
# name, not a lazy stand-in for "and". Same "add a real one when it turns up"
# discipline as the acronym list above.
AMPERSAND_NAMES = [
    "Sales & Marketing",       # the GAAP line item, spelled out on the Growth Engine calculator
    "Research & Development",  # ditto
    "CFOs & VP Finance",       # linklib/enrich.py community-profile prompt example, per Brian
    "Flux Analysis & Summaries",  # linklib/feature_scan.py few-shot example, per Brian
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
    # Built token-by-token (not a single re.escape + substitute pass) so the
    # space BETWEEN tokens becomes \s+ rather than a literal single space —
    # a triple-quoted prompt string can line-wrap mid-term (e.g. "...Flux
    # Analysis\n  & Summaries...") with no change in rendered meaning, and a
    # literal-space pattern would miss that.
    def _term_pattern(term: str) -> re.Pattern:
        def _tok(tok: str) -> str:
            if tok == "&":
                return r"(?:&amp;|&)"
            # An acronym token can carry the '&' embedded with no surrounding
            # whitespace (e.g. "FP&A") — re.escape() turns a literal '&' into
            # '\&', so a straight substring replace still finds it here.
            return re.escape(tok).replace(r"\&", r"(?:&amp;|&)")

        parts = [_tok(tok) for tok in re.split(r"\s+", term)]
        return re.compile(r"\s+".join(parts), re.IGNORECASE)

    allow = [_term_pattern(t) for t in AMPERSAND_NAMES + AMPERSAND_ACRONYMS]
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


def _typography_findings_in_literal(literal: str) -> list[tuple[str, str]]:
    """(rule, excerpt) pairs for one already-extracted span of copy — no line
    number, since that's a source-file concept. Shared by `typography_findings`
    (Python source, one span per string literal) and `typography_findings_plain`
    (a single plain-text value, e.g. a database column, one span total)."""
    findings: list[tuple[str, str]] = []
    if _LONE_ENTITY.match(literal.strip()):
        return findings
    copy = scannable_copy(literal)
    for rule, rx in (("bare-ampersand", _BARE_AMPERSAND),
                     ("spaced-em-dash", _SPACED_EM_DASH),
                     ("spaced-em-dash", _SPACED_MDASH_ENTITY)):
        for m in rx.finditer(copy):
            start, end = max(0, m.start() - 40), m.end() + 40
            excerpt = " ".join(literal[start:end].split())
            findings.append((rule, excerpt))
    return findings


def typography_findings(source: str) -> list[tuple[str, int, str]]:
    """Bare ampersands and spaced em dashes in UI copy, as (rule, line, excerpt).

    `source` is Python source text (webapp/app.py in practice), not rendered
    HTML and not database content — see the scoping note above this function.
    For a single plain-text value (a database column), use
    `typography_findings_plain` instead — this one requires valid Python
    syntax to extract string literals from via `ast.parse`.
    """
    findings: list[tuple[str, int, str]] = []
    for lineno, literal in _copy_literals(source):
        for rule, excerpt in _typography_findings_in_literal(literal):
            findings.append((rule, lineno, excerpt))
    return findings


def typography_findings_plain(text: str) -> list[tuple[str, str]]:
    """Same bare-ampersand/spaced-em-dash rules as `typography_findings`, but
    for a single already-plain-text value rather than Python source — the
    shape a database column's content comes in. No `ast.parse`, no line
    number: the whole `text` IS the copy, not something to extract a literal
    from. Used by the DB-backed-copy scanner (see CLAUDE.md's "Database
    content is scanned too" note) — never by anything reading Python source."""
    return _typography_findings_in_literal(text)


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
