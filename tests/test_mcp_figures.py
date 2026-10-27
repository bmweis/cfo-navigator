"""Phase B2 (#738): the four figure tools over a real MCP server. Admin only;
invented data (tests/figures_fixtures.py)."""
import pytest

from linklib.db import Library
from tests import figures_fixtures as fx
from tests import test_mcp_library as _base
from tests.test_mcp_library import _call_tool, _dict_result, _error_text


@pytest.fixture
def live_server(monkeypatch):
    yield from _base.live_server.__wrapped__(monkeypatch)


def _seed(live_server):
    lib = Library(live_server.db_path)
    fx.seed_standard(lib)
    fx.add_digest(lib, "eb-2099", text="D" * 9000)
    fx.add_digest(lib, "saas-metric-burn-multiple", text="Burn multiple = net burn / net new ARR.")
    lib.close()


def test_find_figures_flags_the_adjacent_bands(live_server):
    _seed(live_server)
    r = _dict_result(_call_tool(live_server.base_url, live_server.admin, "find_figures",
                                {"metric": "NRR", "band": "$25M", "basis": "ARR", "statistic": "median"}))
    eb = [x for x in r["rows"] if x["report_slug"] == "eb-2099"]
    assert sorted(x["band"] for x in eb) == ["$10M-$20M", "$30M-$50M"]
    assert {x["match"] for x in eb} == {"adjacent_band"}


def test_coverage_and_definition(live_server):
    _seed(live_server)
    c = _dict_result(_call_tool(live_server.base_url, live_server.admin, "coverage", {"metric": "Net revenue retention"}))
    assert "median" in c["statistics"]
    d = _dict_result(_call_tool(live_server.base_url, live_server.admin, "metric_definition", {"name": "burn multiple"}))
    assert "net burn" in d["metrics"][0]["card"]["text"]


def test_get_digest_is_not_cut_off(live_server):
    _seed(live_server)
    d = _dict_result(_call_tool(live_server.base_url, live_server.admin, "get_digest", {"id_or_slug": "eb-2099"}))
    assert len(d["text"]) == 9000


def test_get_digest_unknown_is_a_tool_error(live_server):
    _seed(live_server)
    assert "no such digest" in _error_text(_call_tool(live_server.base_url, live_server.admin, "get_digest", {"id_or_slug": "zzz"}))


@pytest.mark.parametrize("tool,args", [
    ("find_figures", {"metric": "NRR"}), ("coverage", {}),
    ("get_digest", {"id_or_slug": "eb-2099"}), ("metric_definition", {"name": "NRR"}),
])
def test_every_tool_refuses_a_non_admin(live_server, tool, args):
    _seed(live_server)
    r = _call_tool(live_server.base_url, live_server.member, tool, args)
    assert r.isError


def test_every_tool_refuses_no_token(live_server):
    _seed(live_server)
    with pytest.raises(BaseException):
        _call_tool(live_server.base_url, "", "coverage", {})
