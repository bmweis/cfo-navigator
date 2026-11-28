"""The standard view of one stored benchmark figure (SV1, 2026-11).

A read-time mapping layer: `standard_view(row, idx)` says what a stored row is in the standard
vocabulary (which registry metric, which variant and form, which statistic, which cohort) without
ever changing the stored row, its value or its printed labels. Nothing reads the result yet
(consumers start in SV3); `find_figures` stamps it on each row as `row["std"]`.

Pure and deterministic: no I/O, no model call. `idx` is the index `figures._load_metric_index`
builds once per lookup (registry, aliases, safe name-map rows and their overrides, the name-key
family index), so this also works from the read-only audit script, which has no Library.

Rules:
- `metric` is the row's OWN stored name resolved to one registry canonical (canonical name, alias,
  safe name-map row, then the name-key family rule `figures.classify_metric_name` uses). Empty when
  unresolved or ambiguous; `resolved_by` says how it went.
- variant, form and cohort come only from the name-map row for that exact raw name.
- `statistic` is the stored statistic normalized onto the standard list where that is unambiguous
  (p75 reads top_quartile, p25 bottom_quartile). A name-map `statistic_std` fills it only when the
  stored one is empty or uninformative (other, value, reported); when the stored one is a real
  statistic that disagrees, the stored one is kept and `conflict` says so. Never chosen quietly.
- No value is converted: a form (retention or churn) is a label, never an inverted number.
"""
from __future__ import annotations

import re

# A stored statistic that says nothing, so a name-map statistic_std may fill it.
UNINFORMATIVE_STATISTICS = frozenset({"", "other", "value", "reported"})

_PCT = re.compile(r"^(?:p\s?(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)(?:\s+percentile)?|percentile\s+(\d{1,2}))$")
_EXACT = {
    "median": "median", "p50": "median", "50th": "median", "50th percentile": "median",
    "mean": "mean", "average": "mean", "avg": "mean",
    "top quartile": "top_quartile", "top_quartile": "top_quartile", "upper quartile": "top_quartile",
    "q3": "top_quartile", "75th": "top_quartile", "75th percentile": "top_quartile",
    "bottom quartile": "bottom_quartile", "bottom_quartile": "bottom_quartile",
    "lower quartile": "bottom_quartile", "q1": "bottom_quartile",
    "25th": "bottom_quartile", "25th percentile": "bottom_quartile",
    "top decile": "p90", "bottom decile": "p10",
}


def _lc(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def normalize_statistic(stored) -> str:
    """The stored statistic on the standard list where that is unambiguous, else the stored text
    unchanged (stripped). Empty stays empty."""
    raw = " ".join(str(stored or "").split())
    s = raw.casefold()
    if not s:
        return ""
    if s in _EXACT:
        return _EXACT[s]
    if re.search(r"\btargets?\b", s):
        return "target"
    m = _PCT.match(s)
    if m:
        n = int(next(g for g in m.groups() if g))
        if n == 50:
            return "median"
        if n == 75:
            return "top_quartile"
        if n == 25:
            return "bottom_quartile"
        if n == 90:
            return "p90"
        if n == 10:
            return "p10"
        return "other_percentile"
    return raw


def standard_view(row: dict, idx: dict) -> dict:
    """{metric, resolved_by, variant, form, statistic, cohort, conflict} for one stored figure row."""
    # Local import: figures stamps rows through this module, and this module reads figures' resolver.
    from . import figures as fg

    stored_name = row.get("metric") or ""
    via, cans = fg.classify_metric_name(None, stored_name, idx, idx.get("fam"))
    metric = cans[0] if via != "ambiguous_alias" and len(cans) == 1 else ""

    over = (idx.get("map_rows") or {}).get(_lc(stored_name)) or {}
    if over and over.get("canonical") != metric:
        over = {}          # an override only speaks for the metric its own map row names
    variant, form, cohort = over.get("variant", ""), over.get("form", ""), over.get("cohort", "")

    stored_stat = normalize_statistic(row.get("statistic"))
    std_stat = over.get("statistic_std", "")
    statistic, conflict = stored_stat, ""
    if std_stat:
        if _lc(stored_stat) in UNINFORMATIVE_STATISTICS:
            statistic = std_stat
        elif stored_stat != std_stat:
            conflict = (f"name map says {std_stat}, row prints {' '.join(str(row.get('statistic') or '').split())}; "
                        "the printed statistic is kept")
    return {"metric": metric, "resolved_by": via, "variant": variant, "form": form,
            "statistic": statistic, "cohort": cohort, "conflict": conflict}
