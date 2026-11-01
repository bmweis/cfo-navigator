"""FP&A Buddy's benchmark pre-step (issue #738, Phase B3).

Before the answer call, `plan_turn` notices benchmark and metric-definition
questions, looks the exact numbers up in the stored figures
(`linklib.figures`), and returns Citations-API document blocks to send ahead
of the retrieved articles, so a number reaches the model exactly as the report
printed it and is cited to its report. No tool loop, no agents.

Steps, in order:
1. RULES, no model call: `figures.detect_metrics` plus cheap signals (bands,
   change-over-time words, definition words, whether the question is mixed).
   Nothing detected means nothing here runs and the turn is unchanged.
2. SPLIT, only when the rules say the question names more than one metric or
   band, or mixes a benchmark ask with a broader one: one small model call
   (the `prestep` role in model_roles) returns up to MAX_SUBQUERIES
   sub-queries and a `rest_of_question`. Any failure (no key, a raised call,
   unusable JSON, a budget that cannot cover it) falls back to one rules-only
   lookup and is recorded in the turn log. Raw model output is never shown.
3. BUDGET: each extra sub-query adds a full retrieval and prompt, which is
   accepted. The estimate is checked against the user's remaining monthly
   budget BEFORE anything runs; a split that does not fit degrades to fewer
   sub-queries with a plain notice. A turn is never blocked by its own cost.
4. LOOKUP per sub-query: figures (own character budget), and for a definition
   the workbook metric card (own budget).

Pure with respect to the database: reads only. The agent runs the retrievals
(each sub-query gets the tier's full library cap and its own character
budget; nothing is divided) and sends the documents.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

from . import figures as fg
from .answer_rules import SHAPE_RULES
from .citation_label import make_label

_log = logging.getLogger(__name__)

MAX_SUBQUERIES = 4
# The fourth retrieval is reserved for the part of the question that is not a benchmark
# lookup, so benchmark parts stop at three (a 4-of-4 split left the rest unsearched).
MAX_BENCHMARK_PARTS = MAX_SUBQUERIES - 1
SPLIT_MAX_TOKENS = 600
SPLIT_TIMEOUT_SECONDS = 10.0
SPLIT_ROLE = "prestep"
SPLIT_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Character budgets for the figures documents and the metric cards of ONE
# sub-query, per Buddy tier. Separate from the tier's per-source and shared
# article budgets (EFFORT_SETTINGS), which they never draw on.
FIGURES_CHARS = {"quick": 4000, "standard": 9000, "deep": 20000}
CARD_CHARS = {"quick": 3000, "standard": 6000, "deep": 12000}
FIGURE_FETCH_LIMIT = 150
# What one part sends, and what the whole turn sends (Part B, 2026-11). 150 rows per part cost about
# 15,000 input tokens; the rows beyond the cap are the oldest and least close, and the admin row says so.
PART_ROWS_CAP = 40
TURN_ROWS_CAP = 80
CLOSE_MATCHES = ("exact", "adjacent_band")
RELATED_CAP = 8
WINDOW_NOTE_PREFIX = "Older editions outside the recent window"
# When every row is only a related metric (a different metric from the one asked), send a few as
# pointers, not a table of them.
RELATED_METRIC_CAP = 3
CARD_SUFFIX = " (workbook definition)"
# First line of every figures document. The document title is "<Source>, <Report> (<year>)",
# so this line is what tells the model (and the tests) it is holding stored report figures.
FIGURES_HEADER = "Stored benchmark figures (exact numbers from the published report)."
# Rough size of the splitter call, for the pre-flight estimate only.
SPLIT_EST_TOKENS = (1100, 400)

_BAND_RE = re.compile(
    # A range first (the unit may sit on the second number only: "$20-$30M"), then a single size.
    r"(?:<|>|under |over |above |below )?\$\s?\d[\d,.]*\s?[kmb]?\s*(?:-|to|–)\s*\$?\s?\d[\d,.]*\s?[kmb]\b\+?"
    r"|(?:<|>|under |over |above |below )?\$\s?\d[\d,.]*\s?[kmb]\b\+?",
    re.I)
# Words that describe the asker's own company or a ratio's denominator, not the metric asked
# about: "$20-$30M in annual revenue", "as a percent of revenue".
_SIZE_CTX = re.compile(r"\s*(?:in|of|with|at)?\s*(?:(?:annual|recurring|total)\s+)*(?:revenue|arr)\b", re.I)
_DENOM_CTX = re.compile(
    r"\b(?:as\s+an?\s+)?(?:percent(?:age)?|%|share|ratio)\s+of\s+(?:(?:total|net|annual)\s+)*(?:revenue|arr)\b", re.I)
_BENCH_CUE = re.compile(
    r"\b(benchmark\w*|median|percentile|quartile|peers?|typical|average|compare[ds]?|comparison|"
    r"industry|norms?)\b", re.I)
_OVER_TIME = re.compile(
    r"\b(trend|over time|year[- ]over[- ]year|yoy|historical|historically|history|has .{0,30} (changed|improved|declined)|"
    r"changed? (since|from)|compared (to|with) (last|prior|previous) year|evolv|over the (last|past) \w+ years|"
    r"since (?:fy ?)?(?:19|20)\d{2}|all editions|every edition)\b", re.I)
# How far back an over-time question goes. Default is a recent window; these widen it.
_SINCE = re.compile(r"\bsince\s+(?:fy\s?)?((?:19|20)\d{2})\b", re.I)
_FULL_HISTORY = re.compile(
    r"\b(historically|full history|entire history|all editions|every edition|all years|going back|as far back|"
    r"decades?|over the (?:last|past) (?:[6-9]|\d{2,}|ten|twelve|fifteen|twenty) years)\b", re.I)
_DEFINITION = re.compile(
    r"\b(define|definition|formula|calculate[ds]?|calculation|computed?|stand for|how is .{0,40} (measured|derived))\b", re.I)


def _lc_text(s) -> str:
    return " ".join(str(s or "").lower().split())


# Words that ask for a benchmark or only describe it; not a broader ask.
_NOT_BROADER = set("""the and for are was were what whats how does did can could should would will with that this these those
 our your their from into than then have has had its not but any per about over under between against among
 median mean average typical typically benchmark benchmarks benchmarking peer peers compare compared comparison comparing
 industry industries range ranges quartile percentile saas company companies startup startups stage business
 arr revenue many much good great normal look looks like level levels number numbers figure figures rate rates
 define definition formula calculate calculated calculation computed measure measured stand mean""".split())


@dataclass
class Sub:
    intent: str = "benchmark"            # benchmark | definition | other
    text: str = ""                       # standalone retrieval question
    metrics: list[str] = field(default_factory=list)
    band: str = ""
    segment: str = ""
    year: str = ""                       # "" newest | "all" | "2024"
    docs: list[dict] = field(default_factory=list)     # [{"block", "sent"}]
    notes: list[str] = field(default_factory=list)
    rows: int = 0
    match_mix: dict = field(default_factory=dict)
    cards: int = 0
    library_hits: int = 0
    truncated_rows: int = 0
    illustrative_left_out: int = 0
    illustrative_ids: set = field(default_factory=set)
    hits_left_out: int = 0               # library articles the per-part allowance left out (after the cross-part dedupe)
    merged: int = 0                      # parts folded into this one because they were the same lookup
    segment_dropped: str = ""
    # Over-time lookups: "" = the recent window, "full" = every edition (historically, all editions),
    # a year = editions from that year on ("since 2015").
    history: str = ""
    candidates: list = field(default_factory=list, repr=False)   # rows found, before the caps
    capped_rows: int = 0                 # candidates left out by the row cap (oldest, least close first)
    fetch_cut: int = 0                   # rows the per-metric fetch limit cut (oldest first)
    also_stored: list = field(default_factory=list, repr=False)  # stored rows gather() chose not to send (less close)
    documents: list = field(default_factory=list)                # [{"title", "rows"}] this part's rows in each merged document
    picked: list = field(default_factory=list, repr=False)       # rows chosen by the cap (before the documents are built)
    left_out_by_report: dict = field(default_factory=dict)       # {report title: stored rows not sent}


@dataclass
class Prestep:
    active: bool = False
    subs: list[Sub] = field(default_factory=list)
    split_ran: bool = False
    rest_of_question: str = ""
    # '' | no_key | call_failed | invalid_json | over_cap
    split_fallback: str = ""
    split_model: str = ""
    detected: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    requested_subs: int = 0
    notice: str = ""                     # plain, shown to the user when a split was cut back
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    est_extra_usd: float = 0.0
    remaining_usd: float | None = None
    plan_text: str = ""                  # extra text block sent before the question
    system_rules: bool = False
    # What the pre-step did this turn, always recorded: "ran", "skipped: <reason>" or
    # "error: <class>". An empty record is not allowed.
    outcome: str = ""
    figure_documents: list = field(default_factory=list)     # merged documents: [{"title","rows","parts"}]
    left_out_detail: dict = field(default_factory=dict)      # {report title: stored rows not sent}, whole turn

    def log(self) -> dict:
        if not self.active and not self.split_fallback:
            return {"outcome": self.outcome or "skipped: no benchmark metric detected"}
        mix: dict[str, int] = {}
        for s in self.subs:
            for k, v in s.match_mix.items():
                mix[k] = mix.get(k, 0) + v
        return {
            "outcome": self.outcome or "ran",
            "detected": self.detected, "signals": self.signals, "split_ran": self.split_ran,
            "split_fallback": self.split_fallback, "split_model": self.split_model,
            "requested_subs": self.requested_subs, "notice": self.notice,
            "rest_of_question": self.rest_of_question, "match_mix": mix,
            "subs": [{"intent": s.intent, "text": s.text[:200], "metrics": s.metrics, "band": s.band, "segment": s.segment,
                      "year": s.year, "merged": s.merged, "library_hits_left_out": s.hits_left_out, "figure_rows": s.rows, "cards": s.cards,
                      "library_hits": s.library_hits, "figure_rows_left_out": s.truncated_rows + s.capped_rows + s.fetch_cut,
                      "rows_capped": s.capped_rows + s.fetch_cut, "history": s.history,
                      "editions_left_out": next((n for n in s.notes if n.startswith(WINDOW_NOTE_PREFIX)), ""),
                      "illustrative_left_out": s.illustrative_left_out, "segment_dropped": s.segment_dropped,
                      "documents": s.documents,
                      "match_mix": s.match_mix} for s in self.subs],
            "documents": self.figure_documents,
            "left_out_detail": dict(sorted(self.left_out_detail.items(), key=lambda kv: -kv[1])),
            "extra_cost_usd": round(self.cost_usd, 6), "est_extra_usd": round(self.est_extra_usd, 6),
            "remaining_usd": None if self.remaining_usd is None else round(self.remaining_usd, 4),
        }


# --- step 1: rules ---------------------------------------------------------------

def _context_spans(question: str) -> list[tuple[int, int]]:
    """Character spans that describe the asker's size or a ratio's denominator
    (a band plus "in annual revenue", "as a percent of revenue"). A metric word
    that only occurs inside one of them is not a metric the user asked about."""
    return _size_spans(question) + [m.span() for m in _DENOM_CTX.finditer(question)]


def _size_spans(question: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _BAND_RE.finditer(question):
        e = m.end()
        c = _SIZE_CTX.match(question, e)
        spans.append((m.start(), c.end() if c else e))
    return spans


def _only_in_context(question: str, term: str, spans: list[tuple[int, int]]) -> bool:
    """True when `term` (a short generic one, two words or fewer) occurs only inside a context span."""
    if len(term.split()) > 2:
        return False
    hits = [m.span() for m in re.finditer(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", question, re.I)]
    return bool(hits) and all(any(s <= a and b <= e for s, e in spans) for a, b in hits)


def _mask_spans(question: str, spans: list[tuple[int, int]]) -> str:
    out = list(question)
    for a, b in spans:
        for i in range(a, min(b, len(out))):
            out[i] = " "
    return "".join(out)


_name_key = fg.name_key


_TOTAL_HEADCOUNT_KEY = fg.name_key("Total employees")


def distinct_metric_count(found: list[dict], extra_names: list[str]) -> int:
    """How many different METRICS a question asks about, not how many names it used.
    Registry phrases that resolve to the same canonical metric are one (an ambiguous
    phrase that names several is still one ask). A stored name that is only a variant
    of one already counted ("Gross Margin %", "Gross Margin % (LTM)") is that metric."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by_term: dict[str, set] = {}
    for d in found:
        by_term.setdefault(d["term"], set()).add(d["canonical"])
    for owners in by_term.values():
        owners = sorted(owners)
        for o in owners[1:]:
            parent[find(o)] = find(owners[0])
        find(owners[0])
    roots = {find(c) for owners in by_term.values() for c in owners}
    seen_keys = {_name_key(c) for c in parent} | {_name_key(t) for t in by_term}
    seen_keys.discard(frozenset())
    extra_keys: set = set()
    for n in extra_names:
        k = _name_key(n)
        if k and k not in seen_keys:
            extra_keys.add(k)
    return len(roots) + len(extra_keys)


def detect(lib, question: str) -> dict:
    """Rules-only signals for a question; no model call."""
    found = fg.detect_metrics(lib, question)
    ctx = _context_spans(question or "")
    if ctx:
        found = [d for d in found if not _only_in_context(question, d["term"], ctx)]
    # Only the size phrase is masked for the stored-name search: a stored name may itself
    # contain a denominator ("Expense as a percent of revenue").
    masked = _mask_spans(question or "", _size_spans(question or ""))
    canon = []
    for d in found:
        if d["canonical"] not in canon:
            canon.append(d["canonical"])
    terms = []
    for d in found:
        if d["term"] not in terms:
            terms.append(d["term"])
    bands = []
    for m in _BAND_RE.finditer(question or ""):
        t = m.group(0).strip()
        if t not in bands:
            bands.append(t)
    # Metric names stored in the figures but not in the registry (full-text
    # path). Needs benchmark context as well as the name words: a band, or a
    # benchmark-asking word. Names a registry metric already reaches are skipped.
    via_fts = False
    if bands or _BENCH_CUE.search(question or ""):
        covered: set[str] = set()
        idx = fg._load_metric_index(lib)
        for c in canon:
            covered |= fg._names_for(idx, c)
        known_keys = {k for k in (_name_key(x) for x in list(covered) + terms + canon) if k}
        extra = []
        # Expense families by rules ("S&M as a percent of revenue", "opex by function"): one
        # term per function, so every spelling stored for it (and both bases) is looked up.
        fam_terms = [t for t in fg.detect_expense_families(masked) if t not in terms]
        for n in fg.detect_stored_metric_names(lib, masked):
            if _lc_text(n) in covered or n in terms:
                continue
            # A stored name the family rule already reaches is covered by the family term.
            if fam_terms and fg.expense_line_of(n, "percent of revenue"):
                continue
            k = _name_key(n)
            # A variant of a metric already named ("Gross Margin %") is reached through that
            # metric's lookup, and two variants of one stored metric are one lookup.
            if k and k in known_keys:
                continue
            known_keys.add(k)
            extra.append(n)
        # Family terms go last so they never crowd out another stored metric (headcount) at the cap.
        # "expense as a percent of revenue" already reaches every spend function through its own
        # registry term or stored name; the generic family term would only repeat that lookup.
        if fg.GENERIC_EXPENSE_LABEL in fam_terms and any(
                set(fg.expense_family_request(t)) >= set(fg.GENERIC_EXPENSE_KEYS) for t in terms + extra):
            fam_terms.remove(fg.GENERIC_EXPENSE_LABEL)
        extra += fam_terms
        # "Org structure" and "spend profile" are defined shapes (total FTE and headcount by function;
        # operating expenses by function, cost of revenue and gross margin). One label each, headcount
        # first. A registry or stored name the label already reaches is dropped so it is not looked up twice.
        shape = fg.detect_shape_terms(masked)
        if shape:
            hc, gm = fg.HEADCOUNT_FAMILY_LABEL in shape, fg.COGS_FAMILY_LABEL in shape

            def _reached(t: str) -> bool:
                if hc and (fg.headcount_family_row(t) or fg.name_key(t) == _TOTAL_HEADCOUNT_KEY):
                    return True
                return bool(gm and fg.cogs_family_row(t, "percent of revenue", "percent"))
            terms = [t for t in terms if not _reached(t)]
            canon = [c for c in canon if not _reached(c)]
            extra = [t for t in extra if not _reached(t) and t not in shape]
            if hc and shape[0] == fg.HEADCOUNT_FAMILY_LABEL:
                terms, canon = [fg.HEADCOUNT_FAMILY_LABEL] + terms, [fg.HEADCOUNT_FAMILY_LABEL] + canon
                shape = shape[1:]
            extra = [t for t in shape if t not in terms] + [t for t in extra if t not in shape]
        room = MAX_SUBQUERIES - len(terms)
        extra = extra[:max(room, 0)]
        if extra:
            via_fts = True
            terms = terms + extra
            canon = canon + extra
    words = len((question or "").split())
    questions = (question or "").count("?")
    # What is left once the metric phrases, bands and benchmark-asking words are
    # taken out is the "broader ask" (hiring, structure, strategy).
    left = _lc_text(question)
    for t in terms:
        left = re.sub(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", " ", left)
    left = _BAND_RE.sub(" ", left)
    rest_words = [w for w in re.findall(r"[a-z]+", left) if len(w) > 2 and w not in _NOT_BROADER]
    mixed = (questions >= 2 or words > 40 or len(rest_words) >= 6
             or (len(rest_words) >= 3 and bool(re.search(r"\b(and|also|plus|then|as well as)\b", left))))
    over_time = bool(_OVER_TIME.search(question or ""))
    history = ""
    if over_time:
        since = _SINCE.search(question or "")
        history = since.group(1) if since else ("full" if _FULL_HISTORY.search(question or "") else "")
    definition = bool(_DEFINITION.search(question or ""))
    reasons = []
    registry_terms = {d["term"] for d in found}
    n_metrics = distinct_metric_count(found, [t for t in terms if t not in registry_terms])
    if n_metrics >= 2:        # distinct metrics, not names: "Gross Margin" and "Gross Margin %" are one
        reasons.append("more than one metric")
    if len(bands) >= 2:
        reasons.append("more than one band")
    if canon and mixed:
        reasons.append("a benchmark ask inside a broader question")
    return {"metrics": canon, "terms": terms, "via_fts": via_fts, "found": found, "bands": bands, "over_time": over_time,
            "history": history, "definition": definition, "mixed": mixed, "rest_words": len(rest_words), "split_reasons": reasons,
            "n_metrics": n_metrics,
            "needs_split": bool(canon) and bool(reasons)}


# --- step 2: the split call ------------------------------------------------------

SPLIT_SYSTEM = (
    "You split one question from a finance leader into at most 4 independent parts so each can be "
    "looked up on its own. Output ONLY a JSON object, no prose, in this shape:\n"
    '{"subqueries":[{"intent":"benchmark|definition|other","metric":"","band":"","segment":"",'
    '"year":"","text":""}],"rest_of_question":""}\n'
    "- intent: benchmark = asks for a number or a peer comparison; definition = asks what a metric "
    "means or how it is calculated; other = anything else (hiring, strategy, process).\n"
    "- metric: pick from the metric list you are given when one fits; otherwise the metric's plain name; "
    "empty for an \"other\" part.\n"
    "- band: the company size or range as the user wrote it (for example \"$25M ARR\"); empty if none.\n"
    "- segment: a segment the user stated that narrows the peer group (for example \"Enterprise\" or \"Fintech\"); "
    "empty if none. Never \"SaaS\", \"B2B\", \"software\" or \"company\": every report is a SaaS report.\n"
    "- A metric the user named stays that metric. Never replace headcount, total employees or FTEs with a "
    "ratio such as \"ARR per Head\"; add a per-employee ratio only when the user asked for one or for productivity.\n"
    "- Never repeat a part: one part per metric, band and segment. A question about change over time is that "
    "same part with year \"all\", not a second part.\n"
    "- year: \"all\" only if the part asks about change over time; a four-digit year only if the user named one; otherwise empty.\n"
    "- text: that part restated as a standalone search question.\n"
    "- rest_of_question: whatever the parts do not cover, or empty.\n"
    "Never answer the question. Never add parts the user did not ask for."
)


@dataclass
class SplitResult:
    subs: list[Sub] = field(default_factory=list)
    rest: str = ""
    error: str = ""                      # '' | call_failed | invalid_json | no_key
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _clip(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def parse_split(raw: str) -> tuple[list[Sub], str] | None:
    """Validate the splitter's JSON. None for anything unusable: not JSON, not
    an object, no usable sub-query. Unknown intents become "other". At most
    MAX_SUBQUERIES are kept."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("subqueries"), list):
        return None
    subs: list[Sub] = []
    for item in data["subqueries"]:
        if not isinstance(item, dict):
            continue
        intent = _clip(item.get("intent"), 20).lower()
        if intent not in ("benchmark", "definition", "other"):
            intent = "other"
        year = _clip(item.get("year"), 10).lower()
        if year != "all" and not re.fullmatch(r"(?:19|20)\d{2}", year):
            year = ""
        text = _clip(item.get("text"), 300)
        metric = _clip(item.get("metric"), 120)
        if not text and not metric:
            continue
        subs.append(Sub(intent=intent, text=text or metric, metrics=[metric] if metric else [],
                        band=_clip(item.get("band"), 60), segment=_clip(item.get("segment"), 60), year=year))
        if len(subs) == MAX_SUBQUERIES:
            break
    if not subs:
        return None
    rest = data.get("rest_of_question")
    return subs, _clip(rest, 600) if isinstance(rest, str) else ""


def run_split(question: str, canonical_names: list[str], model: str, client_factory=None) -> SplitResult:
    """One small model call. Any failure returns a SplitResult with `error`
    set and whatever real usage was spent; it never raises."""
    import importlib.util
    if importlib.util.find_spec("anthropic") is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return SplitResult(error="no_key")
    try:
        if client_factory is None:
            from .agent import _get_client as client_factory
        resp = client_factory().messages.create(
            model=model, max_tokens=SPLIT_MAX_TOKENS, system=SPLIT_SYSTEM,
            messages=[{"role": "user", "content": (
                "Metric list: " + "; ".join(canonical_names) + "\n\nQuestion: " + question)}],
            timeout=SPLIT_TIMEOUT_SECONDS)
    except Exception:
        _log.exception("benchmark pre-step split call failed (model=%s)", model)
        return SplitResult(error="call_failed")
    from .pricing import compute_cost
    u = getattr(resp, "usage", None)
    in_tok = getattr(u, "input_tokens", 0) or 0
    out_tok = getattr(u, "output_tokens", 0) or 0
    cost = compute_cost(model, in_tok, out_tok)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    parsed = parse_split(text)
    if parsed is None:
        _log.warning("benchmark pre-step split returned unusable output (model=%s)", model)
        return SplitResult(error="invalid_json", input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost)
    return SplitResult(subs=parsed[0], rest=parsed[1], input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost)


# --- step 3: budget --------------------------------------------------------------

def estimate_extra_usd(model: str, tier: str, settings: dict, n_subs: int, with_split: bool,
                       split_model: str = SPLIT_DEFAULT_MODEL) -> float:
    """What the pre-step adds to a turn, before it runs: the split call, plus
    for every sub-query after the first, a second retrieval's worth of prompt
    (full library cap at the per-source size, bounded by the shared budget)
    and its figures documents. Input tokens at the answer model's rate, 4
    characters a token. An estimate, used only to decide how many parts fit."""
    from .pricing import compute_cost
    extra = 0.0
    if with_split:
        extra += compute_cost(split_model, *SPLIT_EST_TOKENS)
    per_sub_chars = min(settings.get("global_chars", 16000),
                        settings.get("max_library", 8) * settings.get("source_chars", 1800))
    per_sub_chars += FIGURES_CHARS.get(tier, 9000)
    extra += max(0, n_subs - 1) * compute_cost(model, per_sub_chars // 4, 0)
    # The first sub-query still adds its figures documents to the base prompt.
    extra += compute_cost(model, FIGURES_CHARS.get(tier, 9000) // 4, 0)
    return extra


# --- step 4: lookup and formatting -----------------------------------------------

def _row_line(r: dict) -> str:
    parts = [r["band"] or "no band", r["band_basis"] or "basis not stated", r["segment"] or "all segments",
             r["statistic"] or "statistic not stated"]
    if r["denominator"]:
        parts.append(f"per {r['denominator']}")
    parts.append(f"value {r['value']}{' ' + r['unit'] if r['unit'] else ''}")
    if r["n"]:
        parts.append(f"n={r['n']}")
    if r["page"]:
        parts.append(f"page {r['page']}")
    if r["data_period"]:
        parts.append(f"period {r['data_period']}")
    tag = f"match: {r['match']}"
    if r.get("basis_group") in ("revenue", "arr"):
        parts.insert(0, "basis group: " + fg.BASIS_LABELS[r["basis_group"]] + (" (OTHER BASIS)" if r.get("other_basis") else ""))
    if r["gaps"]:
        tag += " (" + "; ".join(r["gaps"]) + ")"
    return " | ".join(parts) + " | " + tag


def _row_identity(r: dict) -> tuple:
    return (r["report_slug"], _norm(r["source"]), _norm(r["report"]), _norm(r["report_year"]))


def _doc_title(r: dict) -> str:
    """"<Source>, <Report> (<year>)", built only from the row's own fields."""
    who = (r["source"] or "").strip()
    title = ", ".join(x for x in (who, r["report"]) if x) or r["report"]
    if r["report_year"]:
        title += f" ({r['report_year']})"
    return title


def _figure_docs(rows: list[dict], budget: int, reports: dict, included: set | None = None) -> tuple[list[dict], int]:
    """One Citations-API document per report, rows in match order, inside a
    character budget. Returns (docs, rows_left_out). Left-out rows are named in
    the last document, never dropped silently. `included` (if given) receives the
    figure_key of every row that made it into a document.

    A document's title comes only from its own rows: rows are grouped by report_slug AND the
    source, report and year each row prints. A file whose rows print a different publisher than
    the file's first row (the loader notes "source differs across rows") therefore gets a
    document of its own, titled by what those rows print, and no link (the link belongs to the
    report record, which is a different report): such rows are cited by name."""
    by_report: dict[tuple, list[dict]] = {}
    for r in rows:
        by_report.setdefault(_row_identity(r), []).append(r)
    docs: list[dict] = []
    used = 0
    left_out = 0
    for ident, grp in by_report.items():
        slug = ident[0]
        head = grp[0]
        lines = [FIGURES_HEADER, f"Metric: {', '.join(sorted({g['metric'] for g in grp}))}"]
        kept = 0
        for g in grp:
            line = _row_line(g)
            if used + sum(len(x) for x in lines) + len(line) > budget and kept:
                break
            lines.append(line)
            if included is not None:
                included.add((g["report_slug"], g["figure_key"]))
            kept += 1
        left_out += len(grp) - kept
        if len(grp) > kept:
            lines.append(f"({len(grp) - kept} more figures in this report matched and were left out for length.)")
        rep = reports.get(slug, {})
        # Titled "<Source>, <Report> (<year>)" so the model names the publisher in its own
        # sentences. The citation carries `figures` (the Go deeper line keys off it, not the title).
        title = _doc_title(head)
        link = head["link"]
        # The rows print a different publisher or report than the report record they are stored under.
        # The record's link would send the reader to the wrong report, so it is not used.
        if rep and (_norm(rep.get("source")) != ident[1] or _norm(rep.get("report")) != ident[2]):
            link = ""
            lines.insert(1, f"These rows print {title} but are stored with {rep.get('source', '')}, {rep.get('report', '')}. "
                            "Cite the publisher and report these rows print.")
        if not link:
            lines.insert(1, "No link on file for this report. Cite it by name.")
        body = "\n".join(lines)
        used += len(body)
        sent = {"title": title, "url": link, "type": "library", "figures": True,
                "label": make_label(source=head["source"], title=head["report"], year=head["report_year"],
                                    url=link, kind="report")}
        if rep.get("article_id") and link:
            sent["article_id"] = rep["article_id"]
        docs.append({"title": title, "body": body, "sent": sent, "rows": kept})
        if used >= budget:
            rest = [g for k2, g2 in list(by_report.items())[len(docs):] for g in g2]
            left_out += len(rest)
            break
    return docs, left_out


def _card_docs(lib, metrics: list[str], budget: int) -> list[dict]:
    out = []
    used = 0
    for m in metrics:
        d = fg.metric_definition(lib, m)
        for item in d.get("metrics", []):
            card = item.get("card")
            if not card or used >= budget:
                continue
            text = card["text"] or card["summary"]
            body = text[: max(0, budget - used)]
            if len(text) > len(body):
                body += "\n(The card continues; the rest was left out for length.)"
            used += len(body)
            title = f"{item['canonical_metric']}{CARD_SUFFIX}"
            # The registry's card URL is the card's own page; failing that, the
            # digest article's URL without its ?digest= parameter.
            url = (item.get("card_url") or "").strip() or fg.strip_digest_param(card["url"])
            sent = {"title": title, "url": url, "type": "library", "article_id": card["article_id"]}
            out.append({"title": title, "body": body, "sent": sent})
    return out


def _window_args(sub: Sub) -> dict:
    """find_figures arguments for an over-time lookup: the recent window by default, every
    edition for "historically" / "all editions", editions from a year for "since 2015"."""
    if sub.history == "full":
        return {}
    if sub.history.isdigit():
        return {"from_year": int(sub.history)}
    return {"recent_window": True}


# Rows kept for each basis group (revenue, ARR, basis not stated) before the cap fills the rest. A cap
# that binds on a part where revenue-basis rows outnumber ARR-basis rows ten to one used to leave one ARR row.
BASIS_RESERVE = 6


def pick_rows(rows: list[dict], cap: int) -> list[dict]:
    """At most `cap` rows from rows already sorted best first (basis group, match, band,
    sample, newest year). Before the rest is filled, the best row of every publisher and of
    every basis group is kept, then up to BASIS_RESERVE rows of each basis group, so a cap never
    drops a publisher or one basis. The result keeps the sorted order."""
    if len(rows) <= cap:
        return rows
    chosen: list[int] = []
    # Total headcount first: one row per publisher edition (newest first), the median-ranked, all-segments
    # row of it, so a part full of function-mix rows never crowds the total out. At most half the cap.
    best: dict[tuple, int] = {}
    for i, r in enumerate(rows):
        if fg.is_total_fte_row(r):
            ed = (_norm(r["source"]), _norm(r["report"]), _norm(r["report_year"]))
            if ed not in best or (_norm(r["segment"]) in ("all", "") and _norm(rows[best[ed]]["segment"]) not in ("all", "")):
                best[ed] = i
    reserved = sorted(best.values(), key=lambda i: (-fg._year_num(rows[i].get("report_year")), i))[:max(cap // 2, 1)]
    chosen += reserved
    # The publisher floor: the best-ranked row (rows are sorted best first, newest data period first) of each
    # publisher's NEWEST edition inside the rows given. Older editions of a publisher never take a floor slot.
    newest_ed: dict[str, int] = {}
    for r in rows:
        src = _norm(r["source"])
        newest_ed[src] = max(newest_ed.get(src, -1), fg._year_num(r.get("report_year")))
    floor: list[int] = []
    seen_src: set = set()
    seen_basis: set = set()
    for i, r in enumerate(rows):
        src, basis = _norm(r["source"]), r.get("basis_group") or ""
        if i in chosen:
            seen_src.add(src)
            continue
        if fg._year_num(r.get("report_year")) != newest_ed[src]:
            continue
        if src not in seen_src or (basis and basis not in seen_basis):
            seen_src.add(src)
            if basis:
                seen_basis.add(basis)
            floor.append(i)
    # If the floor alone would overfill the cap, newer editions keep their slot and older ones are cut first.
    floor.sort(key=lambda i: (-fg._year_num(rows[i].get("report_year")), i))
    chosen += floor[:max(cap - len(chosen), 0)]
    per_basis: dict[str, int] = {}
    for i in chosen:
        b = rows[i].get("basis_group") or ""
        per_basis[b] = per_basis.get(b, 0) + 1
    for i, r in enumerate(rows):
        b = r.get("basis_group") or ""
        if b and i not in chosen and per_basis.get(b, 0) < BASIS_RESERVE:
            chosen.append(i)
            per_basis[b] = per_basis.get(b, 0) + 1
    chosen = chosen[:cap]
    for i in range(len(rows)):
        if len(chosen) >= cap:
            break
        if i not in chosen:
            chosen.append(i)
    return [rows[i] for i in sorted(chosen)]


def allocate_caps(subs: list[Sub]) -> dict[int, int]:
    """Row cap per part: each part up to PART_ROWS_CAP, all parts together up to TURN_ROWS_CAP.
    A part that needs less than its share leaves the rest to the others."""
    need = {id(s): min(len(s.candidates), PART_ROWS_CAP) for s in subs}
    caps: dict[int, int] = {}
    left, pending = TURN_ROWS_CAP, [s for s in subs if need[id(s)]]
    while pending:
        share = left // len(pending)
        small = [s for s in pending if need[id(s)] <= share]
        if not small:
            for s in pending:
                caps[id(s)] = share
            break
        for s in small:
            caps[id(s)] = need[id(s)]
            left -= need[id(s)]
            pending.remove(s)
    return caps


def hit_allowance(sub: Sub, max_library: int) -> int:
    """Library articles one part of a split turn may send. A part with stored figure rows gets half the
    tier's per-part count (the numbers come from the figures, the articles add context); a part with
    none, such as the rest-of-question part, gets the full count. Never fewer than one."""
    return max(max_library if not sub.rows else max_library // 2, 1)


def lookup(lib, sub: Sub, tier: str, reports: dict, *, over_time: bool = False,
           include_illustrative: bool = False, row_cap: int | None = None) -> None:
    """Gather and build one part on its own (a single lookup). plan_turn gathers every part
    first, shares the turn's row cap between them, then builds."""
    gather(lib, sub, over_time=over_time, include_illustrative=include_illustrative)
    build(lib, sub, tier, reports, row_cap if row_cap is not None else PART_ROWS_CAP)


def gather(lib, sub: Sub, *, over_time: bool = False, include_illustrative: bool = False) -> None:
    """Find the rows for one part (sub.candidates, best first) and the notes. Reads only."""
    names = list(sub.metrics)
    rows: list[dict] = []
    seen_keys: set = set()
    # "SaaS", "B2B SaaS", "software" and "company" are not segments (every report is SaaS), and a
    # segment no stored figure prints narrows to nothing: search without it.
    segment = fg.usable_segment(lib, sub.segment)
    if sub.segment and not segment:
        sub.segment_dropped = sub.segment
    for m in names:
        seg_m = segment
        if seg_m:
            # The segment must be used by THIS metric's stored rows, not just by some other metric's.
            probe = fg.find_figures(lib, m, band=sub.band or None, all_editions=True, limit=FIGURE_FETCH_LIMIT,
                                    include_illustrative=include_illustrative, basis_hint=sub.text)
            if not any(_norm(r["segment"]) == _norm(seg_m) for r in probe["rows"]):
                seg_m = ""
        if sub.segment and not seg_m:
            sub.segment_dropped = sub.segment
            sub.notes.append(f"Segment not used by the data: {sub.segment}. The figures below are not narrowed to it.")
        all_ed = sub.year == "all" or over_time
        res = fg.find_figures(lib, m, band=sub.band or None, segment=seg_m or None,
                              year=(sub.year if sub.year not in ("", "all") else None),
                              all_editions=all_ed, limit=FIGURE_FETCH_LIMIT,
                              include_illustrative=include_illustrative, basis_hint=sub.text,
                              **(_window_args(sub) if all_ed else {}))
        sub.fetch_cut += max(res["total_matching"] - res["returned"], 0)
        sub.illustrative_ids |= set(res.get("illustrative_ids", ()))
        sub.illustrative_left_out = len(sub.illustrative_ids)
        sub.notes += res["notes"]
        if not res["rows"]:
            sub.notes.append(f"No stored figure was found for the metric labelled '{m}'"
                             + (f" at {sub.band}" if sub.band else "")
                             + ". That label is internal: never quote it. Describe the metric in plain words, say "
                               "plainly that no figure is on file for it, and do not estimate it.")
        for r in res["rows"]:
            key = (r["report_slug"], r["figure_key"])
            if key not in seen_keys:
                seen_keys.add(key)
                rows.append(r)
    # Closest match first, then newest edition first across ALL the sub-query's metrics. Always
    # newest first, change over time included: oldest first let a long run of old editions use up
    # the cap and the character budget before the newest ones (the 2025 rows never reached the model).
    wants = fg.statistic_wants(sub.text)
    fg.mark_period_gap(rows)
    rows.sort(key=lambda r: (r.get("basis_rank", 0), fg._MATCH_RANK[r["match"]], r.get("band_context", False),
                             fg._has_sample_size(r), r.get("period_gap", 0), fg.statistic_rank(r["statistic"], wants),
                             -fg._year_num(r.get("report_year")), r["source"]))
    # Noise rule: exact and adjacent-band rows are always sent. Less-close rows (another
    # segment, statistic, basis or a related metric) are sent only when nothing closer
    # exists, at most RELATED_CAP, and the notes say so.
    close = [r for r in rows if r["match"] in CLOSE_MATCHES]
    if close and len(close) < len(rows):
        sub.also_stored += [r for r in rows if r["match"] not in CLOSE_MATCHES]
        sub.notes.append(f"{len(rows) - len(close)} less-close stored figures (another segment, statistic, "
                         "basis or a related metric) were not sent because closer matches exist.")
        rows = close
    elif rows and not close:
        sub.notes.append("There is no exact or adjacent-band match for this. The figures below are the closest stored "
                         "ones, each labelled with how it differs. Say plainly that there is no exact match.")
        cap = RELATED_METRIC_CAP if all(r["match"] == "related_metric" for r in rows) else RELATED_CAP
        if len(rows) > cap:
            sub.notes.append(f"{len(rows) - cap} further less-close figures were not sent.")
            sub.also_stored += rows[cap:]
            rows = rows[:cap]
    sub.candidates = rows



# --- line-item status: shown, left out, or not printed -----------------------------------

STATUS_CAP = 16


def _item_key(r: dict) -> tuple:
    """The line item a row is: an expense function (S&M, R&D, ...) or the metric's name family."""
    line = fg.expense_line_of(r["metric"], r["denominator"], r["unit"])
    return ("line", line) if line else ("name", fg.name_key(r["metric"]) or frozenset({_norm(r["metric"])}))


def status_notes(candidates: list[dict], also_stored: list[dict], sent: set) -> list[str]:
    """For each report and line item in a part, one of three states, so the answer's table cells
    can say the true thing: shown (a row was sent), left out (stored rows exist but were cut by the
    row cap, the document length or the less-close filter) or not printed (no stored row for that
    report, line item and size). Only the left-out and not-printed cells are listed; a part where
    every cell is shown adds nothing. A cell says only "left out" or "not printed": counts and the
    detail are never written inside a table (the one closing line carries them, see left_out_line)."""
    reports: dict[str, str] = {}
    items: dict[tuple, str] = {}
    stored: dict[tuple, int] = {}
    shown: dict[tuple, int] = {}
    for r in list(candidates) + list(also_stored):
        slug = r["report_slug"]
        reports.setdefault(slug, _doc_title(r))
        k = _item_key(r)
        items.setdefault(k, r["metric"])
        stored[(slug, k)] = stored.get((slug, k), 0) + 1
        if (slug, r["figure_key"]) in sent:
            shown[(slug, k)] = shown.get((slug, k), 0) + 1
    if len(items) < 2 and len(reports) < 2:
        return []
    cells = []
    for slug, title in reports.items():
        for k, label in items.items():
            n, sh = stored.get((slug, k), 0), shown.get((slug, k), 0)
            if sh:
                continue
            if n:
                cells.append(f"{label} / {title}: left out. In a table cell write only \"left out\" (no count, no note); "
                             "never \"not printed\".")
            else:
                cells.append(f"{label} / {title}: not printed (no stored row at this size).")
    if not cells:
        return []
    more = len(cells) - STATUS_CAP
    cells = cells[:STATUS_CAP] + ([f"({more} more cells in the same state, not listed.)"] if more > 0 else [])
    return ["Line-item status by report. A table cell or sentence follows this exactly; every line item and report not listed "
            "here was shown above:"] + ["  " + c for c in cells]


LEFT_OUT_LINE_REPORTS = 4


def left_out_by_report(candidates: list[dict], also_stored: list[dict], sent: set) -> dict[str, int]:
    """{report title: stored matching rows that were not sent}, over the rows a part found."""
    out: dict[str, int] = {}
    for r in list(candidates) + list(also_stored):
        if (r["report_slug"], r["figure_key"]) not in sent:
            t = _doc_title(r)
            out[t] = out.get(t, 0) + 1
    return out


def left_out_line(per_part: list[dict]) -> str:
    """The one short line that names what was stored but not sent, for the end of the answer (never a table
    row): "Left out (not sent): <report> <N> rows, ...". At most LEFT_OUT_LINE_REPORTS reports are named, most
    rows first; the full list is on the admin row. "" when nothing was left out."""
    total: dict[str, int] = {}
    for d in per_part:
        for t, n in d.items():
            total[t] = total.get(t, 0) + n
    if not total:
        return ""
    ranked = sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))
    named = ranked[:LEFT_OUT_LINE_REPORTS]
    text = ", ".join(f"{t} {n} row{'s' if n != 1 else ''}" for t, n in named)
    if len(ranked) > len(named):
        text += f", and {len(ranked) - len(named)} more report{'s' if len(ranked) - len(named) != 1 else ''}"
    return f"Left out (not sent): {text}."


def pick(lib, sub: Sub, tier: str, row_cap: int) -> None:
    """Apply the row cap to one part and build its definition cards. The figure documents are built
    later, once, for the whole turn (merge_figure_docs). Reads only."""
    names = list(sub.metrics)
    rows = pick_rows(sub.candidates, row_cap)
    sub.picked = rows
    sub.capped_rows = len(sub.candidates) - len(rows)
    if sub.capped_rows:
        sub.notes.append(f"{sub.capped_rows} further matching figures were not sent (the oldest and least close go first). "
                         "Ask a narrower question for them.")
    sub.rows = len(rows)
    for r in rows:
        sub.match_mix[r["match"]] = sub.match_mix.get(r["match"], 0) + 1
    if sub.intent == "definition":
        cards = _card_docs(lib, names, CARD_CHARS.get(tier, 6000))
        sub.cards = len(cards)
        sub.docs += cards
        if names and not cards:
            sub.notes.append("No workbook definition card is stored for this metric.")


def merge_figure_docs(subs: list[Sub], tier: str, reports: dict) -> list[dict]:
    """One figures document per report for the whole turn. The rows every part picked are merged by
    (report, figure), a row keeping the numbers of the parts that asked for it, then built once, so a report
    is one document and one entry in Sources however many parts used it. Each part keeps its own character
    budget (nothing is divided); the merged budget is their sum. Sets, on each part, `documents` (its rows in
    each document), `truncated_rows`, `left_out_by_report` and the line-item status notes, all measured against
    what was really sent. Returns the documents (the caller attaches them to a part)."""
    merged: list[dict] = []
    index: dict[tuple, dict] = {}
    active = [(n, s) for n, s in enumerate(subs, 1) if s.picked]
    for n, s in active:
        for r in s.picked:
            key = (r["report_slug"], r["figure_key"])
            if key in index:
                index[key]["asked_by"].append(n)
            else:
                row = dict(r, asked_by=[n])
                index[key] = row
                merged.append(row)
    if not merged:
        return []
    budget = FIGURES_CHARS.get(tier, 9000) * len(active)
    included: set = set()
    docs, left = _figure_docs(merged, budget, reports, included)
    for d in docs:
        d["parts"] = sorted({n for g in merged if _doc_title(g) == d["title"] for n in g["asked_by"]})
    for n, s in active:
        mine: dict[str, int] = {}
        for r in s.picked:
            if (r["report_slug"], r["figure_key"]) in included:
                t = _doc_title(r)
                mine[t] = mine.get(t, 0) + 1
        s.documents = [{"title": t, "rows": c} for t, c in mine.items()]
        s.truncated_rows = sum(1 for r in s.picked if (r["report_slug"], r["figure_key"]) not in included)
        s.notes += status_notes(s.candidates, s.also_stored, included)
        s.left_out_by_report = left_out_by_report(s.candidates, s.also_stored, included)
        if s.truncated_rows:
            s.notes.append(f"{s.truncated_rows} matching figures were left out for length; ask a narrower question for them.")
    return docs


def build(lib, sub: Sub, tier: str, reports: dict, row_cap: int) -> None:
    """Pick, then build the figure documents, for one part on its own (a single lookup)."""
    pick(lib, sub, tier, row_cap)
    sub.docs += merge_figure_docs([sub], tier, reports)


PRESTEP_RULES = (
    "\n\nBenchmark figures (only when documents starting \"Stored benchmark figures\" are present):\n"
    "- Each such document holds exact numbers from one published report. State a figure only as "
    "the document prints it, with its band, basis, statistic and sample size when given, and cite it.\n"
    "- Every figure line ends with a match tag. When it is not \"match: exact\", say in plain words how it "
    "differs from what the user asked (the band, basis, statistic, segment or definition), using the text in "
    "brackets after the tag. Never present a close figure as an exact one.\n"
    "- Default to the newest edition of each publisher's report. When a figures document for a newer edition "
    "of the same publisher is present, do not use an older edition's number for the same thing. For a question "
    "about change over time, show every edition in one small table, oldest to newest.\n"
    "- If the lookup notes say nothing close was found, say so plainly. Do not estimate a benchmark from "
    "memory and do not stretch a loosely related figure.\n"
    "- Do not average or combine figures from different reports or bases yourself.\n"
    "- A document titled \"... (workbook definition)\" governs. Give that definition first. If other sources "
    "define the metric differently, say so after it, newest source first.\n"
    "- The question may mix a benchmark ask with a broader one. Answer both: the benchmark part from the "
    "figures, the rest normally.\n"
    "- A report whose document says \"No link on file\" is cited by name only.\n"
    "- Name the publisher in your own sentences (the document title starts with it). The page adds a \"Go "
    "deeper\" line naming each report you cited, so do not write one.\n"
    "\nTable mechanics when figures are present:\n"
    "- If a table helps, its columns are, in this order: metric, band as printed, statistic, value, report and "
    "year, match note. Six rows at most unless the user asked for every edition. Newest report first.\n"
    "- The match note is a short phrase of a few words. A caveat that applies to every row (for example, "
    "\"band wider than asked\") is stated once above the table and never repeated in each row.\n"
    "- State a period exactly as the figures give it (the years printed), and compute a percent change or "
    "difference only from numbers shown in the answer. Count the years between two dates correctly."
)


_PER_HEAD = re.compile(r"\bper\s+(?:head|employee|fte|person|capita)\b|\b(?:arr|revenue)\s*/\s*(?:head|employee|fte)\b"
                       r"|\bproductivity\b", re.I)
_HEADCOUNT = re.compile(r"\b(?:headcount|head count|employees?|ftes?|staff)\b", re.I)


def _norm(v: str) -> str:
    return " ".join(str(v or "").lower().split())


def _family_key(lib, metric: str) -> frozenset:
    """The metric family a name belongs to: the registry's canonical name when the name resolves to exactly
    one, else the name itself, reduced to its significant words (name_key drops "total" and folds headcount,
    FTEs and employees together). "total employees" and "Headcount: Total" are one family; "Headcount:
    Sales" is another."""
    r = fg.resolve_metric(lib, metric)
    base = r[0]["canonical"] if len(r) == 1 else metric
    return fg.name_key(base) or frozenset({_norm(base)})


def _merge_key(lib, s: Sub) -> tuple:
    """Two parts with the same key are one lookup: same intent, same metric families, same band, same
    segment, same asked basis (revenue or ARR). Not name equality."""
    return (s.intent, frozenset(_family_key(lib, m) for m in s.metrics), _norm(s.band), _norm(s.segment),
            fg.asked_basis(s.text or ""))


def _guard_split(lib, subs: list[Sub], question: str, sig: dict) -> list[Sub]:
    """Three rules the splitter's output must obey, applied before any lookup.
    A2: a headcount metric the user named is never replaced by a per-employee ratio
    the user did not ask for. A4: parts that resolve to the same lookup (same metrics,
    band and segment) are one part; a change-over-time duplicate makes the survivor
    year "all". The slot this frees goes to any detected metric no part covers."""
    asked_ratio = bool(_PER_HEAD.search(question))
    head_terms = [t for t in sig["terms"] if _HEADCOUNT.search(t) and not _PER_HEAD.search(t)]
    if head_terms and not asked_ratio:
        for s in subs:
            if s.intent != "other" and _PER_HEAD.search(" ".join(s.metrics + [s.text])):
                s.metrics = [head_terms[0]]
                s.text = head_terms[0] + (f" at {s.band}" if s.band else "")
    # A shape label the rules chose ("Headcount by function", "Cost of revenue and gross margin") is the
    # lookup for every part that asks for rows of that family, whatever the splitter called its metric.
    for label, belongs in ((fg.HEADCOUNT_FAMILY_LABEL, lambda m: fg.headcount_family_row(m)),
                           (fg.COGS_FAMILY_LABEL, lambda m: bool(fg.cogs_family_row(m, "percent of revenue", "percent")))):
        if label in sig["terms"]:
            for s in subs:
                if s.intent != "other" and s.metrics and all(belongs(m) or m == label for m in s.metrics):
                    s.metrics = [label]
    merged: list[Sub] = []
    for s in subs:
        key = _merge_key(lib, s)
        twin = None
        if s.intent != "other" and s.metrics:
            twin = next((m for m in merged if m.intent != "other" and m.metrics and _merge_key(lib, m) == key), None)
        if twin is None:
            merged.append(s)
            continue
        # One lookup: the names are variants of one metric family (a registry name and a stored name),
        # so the survivor searches under both, keeps the wider search text, and the admin row says so.
        for m in s.metrics:
            if m not in twin.metrics:
                twin.metrics.append(m)
        if len(s.text or "") > len(twin.text or ""):
            twin.text = s.text
        if s.year == "all":
            twin.year = "all"
        twin.merged += 1 + s.merged
    subs = merged
    if len(subs) < MAX_SUBQUERIES:
        # Covered by canonical metric, so the splitter's "NRR" covers the detected "net revenue retention".
        covered: set = set()
        for s in subs:
            if s.intent != "other":
                for m in s.metrics:
                    covered |= {r["canonical"] for r in fg.resolve_metric(lib, m)}
                covered |= {d["canonical"] for d in fg.detect_metrics(lib, s.text)}
        term_canon = {d["term"]: d["canonical"] for d in fg.detect_metrics(lib, question)}
        covered_names = {_norm(m) for s in subs for m in s.metrics}
        covered_fams = {_family_key(lib, m) for s in subs if s.intent != "other" for m in s.metrics}
        missing = [t for t in sig["terms"] if term_canon.get(t) not in covered
                   and _norm(t) not in covered_names and _family_key(lib, t) not in covered_fams]
        lookups = [s for s in subs if s.intent != "other"]
        if missing and lookups:
            ref = lookups[0]
            others = [x for x in subs if x.intent == "other"]
            new = Sub(intent="benchmark", text=" ".join(missing[:2]), metrics=missing[:2], band=ref.band,
                      segment=ref.segment, year="all" if sig["over_time"] else "")
            subs = lookups + [new] + others
            subs = subs[:MAX_SUBQUERIES]
    return subs


def _reserve_other_slot(subs: list[Sub], rest: str) -> list[Sub]:
    """Cap benchmark/definition parts at MAX_BENCHMARK_PARTS and keep the last slot for
    one "other" part (the rest of the question). Parts beyond the cap are not dropped:
    their text and metrics move into the "other" part, so the figures are still looked
    up and the words are still searched for articles. Total parts never exceed
    MAX_SUBQUERIES, so the number of retrievals is unchanged."""
    lookups = [x for x in subs if x.intent != "other"]
    others = [x for x in subs if x.intent == "other"]
    if len(lookups) <= MAX_BENCHMARK_PARTS:
        return subs
    overflow = lookups[MAX_BENCHMARK_PARTS:]
    keep = lookups[:MAX_BENCHMARK_PARTS]
    other = others[0] if others else Sub(intent="other", text=rest or "")
    extra = " ".join(x.text for x in overflow)
    other.text = " ".join(t for t in (other.text, extra) if t).strip()
    for x in others[1:]:
        other.text = (other.text + " " + x.text).strip()
    for x in overflow + others[1:]:
        for m in x.metrics:
            if m not in other.metrics:
                other.metrics.append(m)
    return keep + [other]


def plan_turn(lib, question: str, *, tier: str, settings: dict, model: str,
              remaining_usd: float | None, split_model: str | None = None,
              client_factory=None) -> Prestep:
    """Decide and prepare the pre-step for one turn. Reads the database,
    makes at most one small model call, writes nothing."""
    ps = Prestep()
    sig = detect(lib, question)
    if not sig["metrics"]:
        ps.outcome = "skipped: no benchmark metric detected"
        return ps
    ps.active = True
    ps.outcome = "ran"
    ps.system_rules = True
    ps.detected = sig["metrics"]
    ps.signals = {k: sig[k] for k in ("bands", "over_time", "definition", "mixed", "rest_words", "split_reasons", "via_fts")}
    ps.remaining_usd = remaining_usd
    reports = {r["report_slug"]: r for r in lib.list_benchmark_reports()}

    # Rules-only single lookup: every detected metric against one retrieval.
    rules_intent = "definition" if sig["definition"] else "benchmark"
    single = Sub(intent=rules_intent, text=question, metrics=list(sig["terms"][:MAX_SUBQUERIES]),
                 band=sig["bands"][0] if len(sig["bands"]) == 1 else "",
                 year="all" if sig["over_time"] else "")
    ps.subs = [single]
    ps.requested_subs = 1

    if sig["needs_split"]:
        smodel = split_model or SPLIT_DEFAULT_MODEL
        ps.split_model = smodel
        ps.est_extra_usd = estimate_extra_usd(model, tier, settings, 2, True, smodel)
        if remaining_usd is not None and ps.est_extra_usd > remaining_usd:
            ps.split_fallback = "over_cap"
        else:
            canon = [r["canonical_metric"] for r in lib.conn.execute(
                "SELECT canonical_metric FROM metric_registry ORDER BY canonical_metric")]
            res = run_split(question, canon, smodel, client_factory)
            ps.input_tokens, ps.output_tokens, ps.cost_usd = res.input_tokens, res.output_tokens, res.cost_usd
            if res.error:
                ps.split_fallback = res.error
            else:
                ps.split_ran = True
                ps.requested_subs = len(res.subs)
                ps.rest_of_question = res.rest
                subs = _guard_split(lib, res.subs, question, sig)
                # The split call's parts are what get retrieved; the broader half of the
                # question is retrieved only if the splitter made it a part. When it left it
                # in rest_of_question alone, no article was ever searched for it (the
                # org-structure digests dropped out of a turn this way). Make it a part.
                if res.rest and not any(x.intent == "other" for x in subs) and len(subs) < MAX_SUBQUERIES:
                    subs = subs + [Sub(intent="other", text=res.rest)]
                    ps.requested_subs = len(subs)
                subs = _reserve_other_slot(subs, res.rest)
                ps.requested_subs = len(subs)
                if remaining_usd is not None:
                    allowed = len(subs)
                    while allowed > 1 and (estimate_extra_usd(model, tier, settings, allowed, False) > remaining_usd - res.cost_usd):
                        allowed -= 1
                    if allowed < len(subs):
                        ps.notice = (f"Your remaining monthly budget covered {allowed} of the {len(subs)} parts of this "
                                     "question, so the rest were folded into the last part. Ask the remainder as a follow-up.")
                        dropped = subs[allowed:]
                        subs = subs[:allowed]
                        subs[-1].text = (subs[-1].text + " " + " ".join(d.text for d in dropped)).strip()
                        for d in dropped:
                            for m in d.metrics:
                                if m not in subs[-1].metrics:
                                    subs[-1].metrics.append(m)
                ps.subs = subs
                # A part the splitter left without a metric still gets any the rules found in its text.
                for s in ps.subs:
                    if not s.metrics and s.intent != "other":
                        s.metrics = [d["term"] for d in fg.detect_metrics(lib, s.text)][:2]
                    if sig["over_time"] and not s.year:
                        s.year = "all" if s.intent == "benchmark" else s.year
    todo = [s for s in ps.subs if s.intent != "other" or s.metrics]
    for s in todo:
        s.history = sig.get("history", "")
        gather(lib, s, over_time=sig["over_time"] and not ps.split_ran)
    caps = allocate_caps(todo)
    for s in todo:
        pick(lib, s, tier, caps.get(id(s), PART_ROWS_CAP))
    fdocs = merge_figure_docs(todo, tier, reports)
    ps.figure_documents = [{"title": d["title"], "rows": d.get("rows", 0), "parts": d.get("parts", [])} for d in fdocs]
    holder = next((s for s in todo if s.picked), None)
    if holder is not None:
        holder.docs += fdocs
    ps.left_out_detail = {}
    for s in todo:
        for t, c in s.left_out_by_report.items():
            ps.left_out_detail[t] = ps.left_out_detail.get(t, 0) + c
    # The text block sent before the question: lookup notes, the plan, the notice.
    lines: list[str] = []
    seen_notes: set[str] = set()
    for s in ps.subs:
        for n in s.notes:
            if n not in seen_notes:
                seen_notes.add(n)
                lines.append(f"- {n}")
    plan: list[str] = []
    if ps.split_ran:
        plan.append("The question was split into parts that were each looked up:")
        for i, s in enumerate(ps.subs, 1):
            plan.append(f"{i}. [{s.intent}] {s.text}")
        if ps.rest_of_question:
            plan.append(f"Not covered by a lookup: {ps.rest_of_question}")
        plan.append("Answer every part, then the rest of the question.")
    closing = left_out_line([ps.left_out_detail]) if ps.left_out_detail else ""
    if closing:
        plan.append("Closing line for the end of the answer (never inside a table; copy it as written, once, after the last "
                    "table or paragraph, and add nothing about left-out rows anywhere else):\n" + closing)
    shape_used = any(m in fg.SHAPE_LABELS for s in ps.subs for m in s.metrics)
    if shape_used:
        plan.append(SHAPE_RULES)
    ps.plan_text = "\n".join(
        ([("BENCHMARK LOOKUP NOTES:\n" + "\n".join(lines))] if lines else [])
        + (["\n".join(plan)] if plan else []))
    return ps


def dedupe_docs(docs: list[dict]) -> list[dict]:
    """Drop a document already present (same title and body) so two sub-queries
    that found the same report send it once. Workbook definition cards go
    first, then the figures, each group in its original order: the workbook
    definition governs, so nothing outranks it in the prompt."""
    seen: set = set()
    out = []
    for d in docs:
        key = (d["title"], d["body"])
        if key not in seen:
            seen.add(key)
            out.append(d)
    return ([d for d in out if d["title"].endswith(CARD_SUFFIX)]
            + [d for d in out if not d["title"].endswith(CARD_SUFFIX)])
