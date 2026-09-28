"""JS-render grounding fix, fetch-error follow-up (2026-09, see CLAUDE.md's
"JS-rendered vendor pages, grounding fetch defect" entry). The original fix
(_fetch_grounding_page) only ever tried the Exa fallback when the direct
fetch actually LOADED but wasn't usable (assess_extraction_quality said no)
— a fetch_error (the request itself never got a response body at all) was
always treated as "failed," full stop, no Exa attempt. That left the one
case the Exa/Medium-platform tier was originally built for unreachable: a
Cloudflare-style WAF commonly returns a non-2xx status (raising before
assess_extraction_quality is ever reached), not a 200-with-thin-shell.

Fixed by classifying fetch_error strings (extract.is_likely_bot_block_error)
into "looks like active blocking" (HTTP 403/429/503, a timeout) vs. "looks
like a genuinely dead/wrong URL" (404, DNS, connection refused, SSL, any
other 5xx) — only the former gets an Exa retry. Covers both the pure
classifier and _fetch_grounding_page's own branching, with explicit
call-tracking on the "should NOT try Exa" cases so a test can't pass by
coincidence (Exa returning "" for an unrelated reason would look identical
to Exa never being called at all unless the mock itself proves it wasn't
invoked)."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import enrich, extract


# -- extract.is_likely_bot_block_error (pure classifier) ----------------------

@pytest.mark.parametrize("fetch_error,expected", [
    ("HTTP 403", True),
    ("HTTP 429", True),
    ("HTTP 503", True),
    ("timeout", True),
    ("HTTP 404", False),
    ("HTTP 401", False),
    ("HTTP 500", False),
    ("HTTP 502", False),
    ("HTTP 504", False),
    ("connection error: [Errno -2] Name or service not known", False),
    ("SSL error: [SSL] some handshake failure", False),
    ("", False),
    ("HTTP ?", False),   # _describe_fetch_error's own "no response object" fallback shape
])
def test_is_likely_bot_block_error_classification(fetch_error, expected):
    assert extract.is_likely_bot_block_error(fetch_error) is expected


# -- _fetch_grounding_page: block-shaped fetch_error tries Exa ---------------

LONG_PAGE_CONTENT = (
    "Runway is a financial planning platform for finance teams at growth-stage companies. "
    "It consolidates budgeting, forecasting, and headcount planning into one collaborative "
    "workspace built for FP&A analysts and controllers who need to model scenarios quickly. "
    "Teams connect their general ledger and payroll systems, then build driver-based models "
    "that update automatically as actuals come in from month to month. The platform is used "
    "by finance leaders who need to answer board questions about runway, burn, and hiring "
    "plans without waiting on a spreadsheet rebuild every single time a number changes."
)


def _mock_fetch_page_error(monkeypatch, fetch_error):
    import types
    monkeypatch.setattr(extract, "fetch_page", lambda url, **kw: types.SimpleNamespace(
        content="", raw_html="", blocked=False, fetch_error=fetch_error))


def _mock_exa_call_tracking(monkeypatch, text="", cost=0.0):
    """Returns a list that records every (url,) call made — lets a test
    assert Exa was never invoked, not just that the end result happens to
    match what "Exa also found nothing" would look like."""
    from linklib import medium_platform
    calls = []

    def _fake(lib, url):
        calls.append(url)
        return (text, cost)

    monkeypatch.setattr(medium_platform, "fetch_content_by_url", _fake)
    return calls


def test_403_tries_exa_and_succeeds(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "HTTP 403")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT, cost=0.007)

    result = enrich._fetch_grounding_page("https://blocked-vendor.example")
    assert calls == ["https://blocked-vendor.example"]
    assert result.ok is True
    assert result.status == "fetched_via_exa"
    assert result.content == LONG_PAGE_CONTENT
    assert result.exa_cost_usd == 0.007


def test_429_tries_exa(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "HTTP 429")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    result = enrich._fetch_grounding_page("https://rate-limited.example")
    assert calls == ["https://rate-limited.example"]
    assert result.ok is True


def test_503_tries_exa(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "HTTP 503")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    result = enrich._fetch_grounding_page("https://under-attack-mode.example")
    assert calls == ["https://under-attack-mode.example"]
    assert result.ok is True


def test_timeout_tries_exa(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "timeout")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    result = enrich._fetch_grounding_page("https://slow-handshake.example")
    assert calls == ["https://slow-handshake.example"]
    assert result.ok is True


def test_403_still_failed_when_exa_also_empty(monkeypatch):
    """Exa is tried (proving the fix reaches this branch) but comes back
    empty too — GroundingFetch.status's own docstring says this stays
    "failed" (never "unreadable"), with the original fetch_error as the
    reason, since the direct fetch never loaded at all either way."""
    _mock_fetch_page_error(monkeypatch, "HTTP 403")
    calls = _mock_exa_call_tracking(monkeypatch, "", cost=0.003)
    result = enrich._fetch_grounding_page("https://still-blocked.example")
    assert calls == ["https://still-blocked.example"]
    assert result.ok is False
    assert result.status == "failed"
    assert result.reason == "HTTP 403"
    assert result.exa_cost_usd == 0.003


# -- _fetch_grounding_page: dead-URL-shaped fetch_error skips Exa ------------

def test_404_never_tries_exa(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "HTTP 404")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)   # would succeed if called — proves it wasn't
    result = enrich._fetch_grounding_page("https://gone.example")
    assert calls == []
    assert result.ok is False
    assert result.status == "failed"
    assert result.reason == "HTTP 404"


def test_dns_connection_error_never_tries_exa(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "connection error: [Errno -2] Name or service not known")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    result = enrich._fetch_grounding_page("https://doesnt-resolve.example")
    assert calls == []
    assert result.ok is False
    assert result.status == "failed"


def test_other_5xx_never_tries_exa(monkeypatch):
    _mock_fetch_page_error(monkeypatch, "HTTP 502")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    result = enrich._fetch_grounding_page("https://gateway-error.example")
    assert calls == []
    assert result.ok is False


def test_exa_disabled_skips_fallback_even_for_a_block_shaped_error(monkeypatch):
    """exa_enabled=False (the site's Exa kill switch, /admin/system/ai) is
    checked before the block-shaped classification, same as it already was
    for the thin-content path — a disabled Exa never fires regardless of
    what the fetch_error looks like."""
    _mock_fetch_page_error(monkeypatch, "HTTP 403")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    result = enrich._fetch_grounding_page("https://blocked-but-exa-off.example", exa_enabled=False)
    assert calls == []
    assert result.ok is False
    assert result.status == "failed"
    assert result.reason == "HTTP 403"


# -- end-to-end: one real caller benefits, not just the shared helper -------

def _mock_anthropic_citing(monkeypatch, blocks):
    import types
    def _create(**kw):
        class _Citation:
            def __init__(self, idx):
                self.document_index = idx

        class _Block:
            def __init__(self, text, cited_indexes):
                self.type = "text"
                self.text = text
                self.citations = [_Citation(i) for i in cited_indexes]

        content = [_Block(text, cited) for text, cited in blocks]
        usage = types.SimpleNamespace(
            input_tokens=200, output_tokens=150,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        return types.SimpleNamespace(content=content, usage=usage)
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: _create(**kw))))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


DESC_BODY = "Runway is a financial planning platform for finance teams at growth-stage companies."
DESC_TAIL = (
    "\n\nSUMMARY: Runway is an FP&A platform for growth-stage finance teams.\n\n"
    "CONFIDENT: true"
)


def test_generate_tool_description_recovers_from_a_cloudflare_style_block(monkeypatch):
    """The actual regression this follow-up fixes: a vendor page blocked by
    a WAF (HTTP 403, not a 200-with-thin-shell) used to refuse outright
    with no Exa attempt at all — confirmed by reverting the fix locally and
    re-running this exact test, which failed with GroundingUnavailable
    against the pre-fix code. Now the Exa fallback recovers it, same as
    the JS-shell case already did."""
    _mock_fetch_page_error(monkeypatch, "HTTP 403")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT, cost=0.007)
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    draft = enrich.generate_tool_description("Runway", "https://blocked-vendor.example", voice_core="Test voice guide.")
    assert calls == ["https://blocked-vendor.example"]
    assert draft is not None
    assert draft.low_confidence is True
    assert draft.exa_cost_usd == 0.007
    assert len(draft.citations) == 1


def test_generate_tool_description_still_refuses_on_a_genuine_404(monkeypatch):
    """The other half of the same fix, proven end to end: a genuinely dead
    URL still refuses to draft, and still never spends an Exa call getting
    there."""
    _mock_fetch_page_error(monkeypatch, "HTTP 404")
    calls = _mock_exa_call_tracking(monkeypatch, LONG_PAGE_CONTENT)
    _mock_anthropic_citing(monkeypatch, [(DESC_BODY, [0]), (DESC_TAIL, [])])

    with pytest.raises(enrich.GroundingUnavailable) as exc_info:
        enrich.generate_tool_description("Gone Co", "https://gone.example", voice_core="Test voice guide.")
    assert calls == []
    assert exc_info.value.reason == "HTTP 404"
