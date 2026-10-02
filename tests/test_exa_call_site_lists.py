"""The hand-maintained lists of Exa call sites must name every real one.

/admin/system/ai describes Exa call sites twice: the toggle card ("this one
toggle gates every real Exa call") and the read-only Call sites card. Both are
prose, so they drifted: the toggle card omitted Feature Taxonomy vendor
research, which the same toggle gates. A new module that calls Exa must be added
to EXA_MODULE_MARKERS here, which forces a decision about both lists.
"""
import os
import pathlib
import tempfile

import pytest

from linklib.db import Library

ROOT = pathlib.Path(__file__).resolve().parents[1]

# module that issues an Exa HTTP call -> phrase both lists must contain for it
EXA_MODULE_MARKERS = {
    "linklib/agent.py": "web tier",
    "linklib/domain_migration.py": "domain migration",
    "linklib/medium_platform.py": "medium-platform",
    "linklib/feature_scan.py": "feature taxonomy",
}
# enrich.py reaches Exa only through medium_platform.fetch_content_by_url, so it
# has no URL of its own, but its grounding fallback is a distinct billed site.
EXTRA_MARKERS = ["vendor profile drafting"]


def _exa_modules():
    found = set()
    for sub in ("linklib", "webapp"):
        for path in (ROOT / sub).rglob("*.py"):
            if "api.exa.ai" in path.read_text(encoding="utf-8"):
                found.add(path.relative_to(ROOT).as_posix())
    return found


def test_every_module_that_calls_exa_is_registered_here():
    assert _exa_modules() == set(EXA_MODULE_MARKERS), (
        "A module that calls api.exa.ai was added or removed. Update "
        "EXA_MODULE_MARKERS and both Exa lists on /admin/system/ai.")


@pytest.fixture
def appmod(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    lib = Library(db)
    lib.seed_voice_prompts()
    lib.close()
    import importlib
    import webapp.app as mod
    importlib.reload(mod)
    yield mod
    if os.path.exists(db):
        os.remove(db)


def _lower(html: str) -> str:
    return html.lower().replace("&amp;", "&").replace("-", " ")


def test_toggle_card_names_every_exa_call_site(appmod):
    text = _lower(appmod._ai_exa_config_html(True, True))
    for marker in list(EXA_MODULE_MARKERS.values()) + EXTRA_MARKERS:
        assert marker.replace("-", " ") in text, f"toggle card is missing: {marker}"


def test_call_sites_card_names_every_exa_call_site(appmod):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    client.post("/login", data={"username": "admin", "password": "adminpass"},
                follow_redirects=False)
    page = client.get("/admin/system/ai")
    assert page.status_code == 200
    text = _lower(page.text)
    for marker in list(EXA_MODULE_MARKERS.values()) + EXTRA_MARKERS:
        assert marker.replace("-", " ") in text, f"call sites card is missing: {marker}"
