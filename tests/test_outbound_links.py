"""Outbound links open in a new tab (BRAND.md §3.3).

Two-sided on purpose, the same discipline as the typography lint and the
coral-discipline tests: asserting only that the live source passes would be
satisfied by a checker that never finds anything, so every rule here also has
a test proving it FAILS on a real violation of exactly the shape it catches.
"""
import io
import pathlib

import pytest

from linklib.brand_check import (
    INTERNAL_LINK_HOSTS,
    findings,
    outbound_link_problems,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "webapp" / "app.py"


def _app_src() -> str:
    return io.open(APP, encoding="utf-8").read()


# --- the live source is clean -------------------------------------------------

def test_app_source_has_no_outbound_links_missing_target():
    problems = outbound_link_problems(_app_src())
    assert problems == [], (
        "Outbound link(s) missing target=\"_blank\": " + "; ".join(problems))


def test_the_logo_dev_footer_link_opens_in_a_new_tab():
    """The one real offender this rule's own sweep found (PR 35). Pinned by
    href rather than by line number so it survives unrelated edits above it."""
    src = _app_src()
    assert '<a href="https://logo.dev" target="_blank" rel="noopener">' in src


# --- the checker can actually fail --------------------------------------------

def test_flags_an_outbound_link_without_target():
    src = '<a href="https://example.com">Example</a>'
    problems = outbound_link_problems(src)
    assert len(problems) == 1
    assert "https://example.com" in problems[0]


def test_passes_the_same_link_once_target_is_added():
    """The near-miss beside the violation above — same href, compliant tag."""
    src = '<a href="https://example.com" target="_blank" rel="noopener">Example</a>'
    assert outbound_link_problems(src) == []


@pytest.mark.parametrize("href", ["/tools", "#where-ai-shows-up", "mailto:x@y.com"])
def test_internal_and_non_http_links_are_never_flagged(href):
    assert outbound_link_problems(f'<a href="{href}">x</a>') == []


@pytest.mark.parametrize("host", sorted(INTERNAL_LINK_HOSTS))
def test_our_own_absolute_urls_are_not_outbound(host):
    assert outbound_link_problems(f'<a href="https://{host}/tools">x</a>') == []


def test_a_multi_line_anchor_split_across_string_literals_is_read_as_one_tag():
    """The false-positive shape that made a naive same-line grep report five
    offenders when there was really one: an anchor whose target attribute sits
    on a later source line than its href."""
    src = (
        '    doc_link = (\'<a href="https://github.com/bmweis/cfo-navigator"\'\n'
        '                \' target="_blank" rel="noopener">docs</a>\')\n'
    )
    assert outbound_link_problems(src) == []


def test_reports_the_line_number_of_the_offending_tag():
    src = "line one\nline two\n" + '<a href="https://example.com">x</a>'
    (problem,) = outbound_link_problems(src)
    assert problem.startswith("line 3:")


# --- wiring -------------------------------------------------------------------

def test_brand_findings_does_not_double_report_outbound_links():
    """findings() is the palette/typeface check and owns its own /admin/checks
    row; the outbound rule has a separate row, so it must not also surface
    here or the same violation would be reported twice."""
    src = '<a href="https://example.com">Example</a>'
    assert not any("target" in f for f in findings(src))


def test_outbound_check_is_wired_into_run_all():
    from webapp import checks
    names = [r["name"] for r in checks.run_all()]
    assert "Outbound links open in a new tab" in names


def test_run_all_reports_the_outbound_check_as_passing():
    from webapp import checks
    row = next(r for r in checks.run_all()
               if r["name"] == "Outbound links open in a new tab")
    assert row["ok"] is True, row["detail"]


def test_www_host_is_internal_pin():
    """PR 2a.2 pin (passes today, no fail-first claim). A report read
    INTERNAL_LINK_HOSTS on GitHub as holding a markdown-link string where
    "www.bmweis.com" belongs; the file is correct, and this keeps it so: an
    anchor to the www host is internal and needs no target="_blank"."""
    assert INTERNAL_LINK_HOSTS == {"bmweis.com", "www.bmweis.com", "mcp.bmweis.com"}
    assert all("](" not in h and "[" not in h for h in INTERNAL_LINK_HOSTS)
    snippet = '<a href="https://www.bmweis.com/contact">Contact</a>'
    assert outbound_link_problems(snippet) == []
    other = '<a href="https://example.com/x">Elsewhere</a>'
    assert outbound_link_problems(other) != []
