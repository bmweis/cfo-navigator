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
CLOSE_MATCHES = ("exact", "adjacent_band")
RELATED_CAP = 8
CARD_SUFFIX = " (workbook definition)"
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
    r"\b(trend|over time|year[- ]over[- ]year|yoy|historical|history|has .{0,30} (changed|improved|declined)|"
    r"changed? (since|from)|compared (to|with) (last|prior|previous) year|evolv|over the (last|past) \w+ years)\b", re.I)
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
    segment_dropped: str = ""


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
                      "year": s.year, "figure_rows": s.rows, "cards": s.cards,
                      "library_hits": s.library_hits, "figure_rows_left_out": s.truncated_rows,
                      "illustrative_left_out": s.illustrative_left_out, "segment_dropped": s.segment_dropped,
                      "match_mix": s.match_mix} for s in self.subs],
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
        for n in fg.detect_stored_metric_names(lib, masked):
            if _lc_text(n) in covered or n in terms:
                continue
            k = _name_key(n)
            # A variant of a metric already named ("Gross Margin %") is reached through that
            # metric's lookup, and two variants of one stored metric are one lookup.
            if k and k in known_keys:
                continue
            known_keys.add(k)
            extra.append(n)
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
            "definition": definition, "mixed": mixed, "rest_words": len(rest_words), "split_reasons": reasons,
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
    if r["gaps"]:
        tag += " (" + "; ".join(r["gaps"]) + ")"
    return " | ".join(parts) + " | " + tag


def _figure_docs(rows: list[dict], budget: int, reports: dict) -> tuple[list[dict], int]:
    """One Citations-API document per report, rows in match order, inside a
    character budget. Returns (docs, rows_left_out). Left-out rows are named in
    the last document, never dropped silently."""
    by_report: dict[str, list[dict]] = {}
    for r in rows:
        by_report.setdefault(r["report_slug"], []).append(r)
    docs: list[dict] = []
    used = 0
    left_out = 0
    for slug, grp in by_report.items():
        head = grp[0]
        lines = [f"Metric: {', '.join(sorted({g['metric'] for g in grp}))}"]
        kept = 0
        for g in grp:
            line = _row_line(g)
            if used + sum(len(x) for x in lines) + len(line) > budget and kept:
                break
            lines.append(line)
            kept += 1
        left_out += len(grp) - kept
        if len(grp) > kept:
            lines.append(f"({len(grp) - kept} more figures in this report matched and were left out for length.)")
        rep = reports.get(slug, {})
        title = f"Figures: {head['report']} ({head['report_year']})" if head["report_year"] else f"Figures: {head['report']}"
        if not head["link"]:
            lines.insert(0, "No link on file for this report. Cite it by name.")
        body = "\n".join(lines)
        used += len(body)
        sent = {"title": title, "url": head["link"], "type": "library"}
        if rep.get("article_id"):
            sent["article_id"] = rep["article_id"]
        docs.append({"title": title, "body": body, "sent": sent})
        if used >= budget:
            rest = [g for s2, g2 in list(by_report.items())[len(docs):] for g in g2]
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


def lookup(lib, sub: Sub, tier: str, reports: dict, *, over_time: bool = False,
           include_illustrative: bool = False) -> None:
    """Fill sub.docs, sub.notes and counts. Reads only."""
    names = list(sub.metrics)
    rows: list[dict] = []
    seen_keys: set = set()
    # "SaaS", "B2B SaaS", "software" and "company" are not segments (every report is SaaS), and a
    # segment no stored figure prints narrows to nothing: search without it.
    segment = fg.usable_segment(lib, sub.segment)
    if sub.segment and not segment:
        sub.segment_dropped = sub.segment
    for m in names:
        res = fg.find_figures(lib, m, band=sub.band or None, segment=segment or None,
                              year=(sub.year if sub.year not in ("", "all") else None),
                              all_editions=(sub.year == "all" or over_time), limit=FIGURE_FETCH_LIMIT,
                              include_illustrative=include_illustrative)
        sub.illustrative_ids |= set(res.get("illustrative_ids", ()))
        sub.illustrative_left_out = len(sub.illustrative_ids)
        sub.notes += res["notes"]
        if not res["rows"]:
            sub.notes.append(f"No stored figure matches '{m}'"
                             + (f" at {sub.band}" if sub.band else "") + ". Say so plainly; do not estimate it.")
        for r in res["rows"]:
            key = (r["report_slug"], r["figure_key"])
            if key not in seen_keys:
                seen_keys.add(key)
                rows.append(r)
    # Closest match first, then newest edition first across ALL the sub-query's metrics
    # (oldest first when the question is about change over time). Sorting only on match
    # left a newer row of a later metric behind older rows of an earlier one, so a long
    # run of old rows could use up the character budget before it.
    sign = 1 if (over_time or sub.year == "all") else -1
    rows.sort(key=lambda r: (fg._MATCH_RANK[r["match"]], fg._has_sample_size(r), sign * fg._year_num(r["report_year"])))
    # Noise rule: exact and adjacent-band rows are always sent. Less-close rows (another
    # segment, statistic, basis or a related metric) are sent only when nothing closer
    # exists, at most RELATED_CAP, and the notes say so.
    close = [r for r in rows if r["match"] in CLOSE_MATCHES]
    if close and len(close) < len(rows):
        sub.notes.append(f"{len(rows) - len(close)} less-close stored figures (another segment, statistic, "
                         "basis or a related metric) were not sent because closer matches exist.")
        rows = close
    elif rows and not close:
        sub.notes.append("There is no exact or adjacent-band match for this. The figures below are the closest stored "
                         "ones, each labelled with how it differs. Say plainly that there is no exact match.")
        if len(rows) > RELATED_CAP:
            sub.notes.append(f"{len(rows) - RELATED_CAP} further less-close figures were not sent.")
            rows = rows[:RELATED_CAP]
    sub.rows = len(rows)
    for r in rows:
        sub.match_mix[r["match"]] = sub.match_mix.get(r["match"], 0) + 1
    if rows:
        docs, left = _figure_docs(rows, FIGURES_CHARS.get(tier, 9000), reports)
        sub.docs += docs
        sub.truncated_rows = left
        if left:
            sub.notes.append(f"{left} matching figures were left out for length; ask a narrower question for them.")
    if sub.intent == "definition":
        cards = _card_docs(lib, names, CARD_CHARS.get(tier, 6000))
        sub.cards = len(cards)
        sub.docs += cards
        if names and not cards:
            sub.notes.append("No workbook definition card is stored for this metric.")


PRESTEP_RULES = (
    "\n\nBenchmark figures (only when documents titled \"Figures: ...\" are present):\n"
    "- Each \"Figures:\" document holds exact numbers from one published report. State a figure only as "
    "the document prints it, with its band, basis, statistic and sample size when given, and cite it.\n"
    "- Every figure line ends with a match tag. When it is not \"match: exact\", say in plain words how it "
    "differs from what the user asked (the band, basis, statistic, segment or definition), using the text in "
    "brackets after the tag. Never present a close figure as an exact one.\n"
    "- Default to the newest edition of each publisher's report. When a Figures document for a newer edition "
    "of the same publisher is present, do not use an older edition's number for the same thing. For a question "
    "about change over time, show every edition in one small table, oldest to newest.\n"
    "- If the lookup notes say nothing close was found, say so plainly. Do not estimate a benchmark from "
    "memory and do not stretch a loosely related figure.\n"
    "- Do not average or combine figures from different reports or bases yourself. A number you derive must "
    "be labelled \"derived, not in report\".\n"
    "- A document titled \"... (workbook definition)\" governs. Give that definition first. If other sources "
    "define the metric differently, say so after it, newest source first.\n"
    "- The question may mix a benchmark ask with a broader one. Answer both: the benchmark part from the "
    "figures, the rest normally.\n"
    "- A report whose document says \"No link on file\" is cited by name only.\n"
    "\nAnswer format when figures are present:\n"
    "- Synthesize, then point to the source. Give the headline figures and a short read of them, not every "
    "row a report holds. The page adds a \"Go deeper\" line naming each report you cited, so do not write one.\n"
    "- Put the figures first in one compact table per topic with the columns, in this order: metric, band as "
    "printed, statistic, value, report and year, match note. At most 8 rows per table unless the user asked "
    "for every edition. Newest report first. Do not restate the same numbers in prose after the table.\n"
    "- The match note is a short phrase of a few words. A caveat that applies to every row (for example, "
    "\"band wider than asked\") is stated once above the table and never repeated in each row.\n"
    "- Then keep the prose short: what the numbers say, how close they are to the ask, and what is missing. "
    "Always end on a full sentence.\n"
    "- When two publishers' newest editions disagree, show both with their dates. Do not average them or pick one.\n"
    "\nRules for numbers and claims:\n"
    "- State a period exactly as the figures give it (the years printed), and compute a percent change or "
    "difference only from numbers shown in the answer. Count the years between two dates correctly.\n"
    "- Do not state a cause or reason for a change unless a cited source states it. If a source does, "
    "attribute it to that source by name; otherwise leave the cause out. Good: \"Example Benchmarks says the "
    "dip followed hiring freezes [2].\" Bad: \"The dip reflects the overhiring era.\" when no source says so.\n"
    "- Label every derived number \"derived, not in report\" inline and show the inputs next to it. Good: "
    "\"up 12% (derived, not in report: 150 to 168)\". Bad: \"up 12%\". When a stored "
    "figure exists for the same thing (for example an implied total employees row), use the stored figure "
    "and cite it instead of deriving one.\n"
    "- Medians do not add. Never present a sum or a \"stack\" of medians. List each ratio with its own band, "
    "year and source, and combine numbers only within one source, one band and one year. Good: three separate "
    "rows, each with its own band and report. Bad: a \"typical cost stack\" that adds medians from different "
    "reports or bands.\n"
    "- Say nothing about the future or about years the data does not cover. Good: \"The 2025 edition is the "
    "newest on file.\" Bad: \"Likely similar or slightly lower next year.\"\n"
    "- Give no recommendation or \"practical read\" unless the question asks for one. Good: end with what the "
    "numbers say and what is missing. Bad: \"Sitting below the median means you are behind.\"\n"
    "- Give the year of every source you take a number from. Say when a source is more than three years old."
)


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
                subs = res.subs
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
    for s in ps.subs:
        if s.intent != "other" or s.metrics:
            lookup(lib, s, tier, reports, over_time=sig["over_time"] and not ps.split_ran)
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
