"""A test run can never overwrite the git-tracked preferred_sites.opml.

The tracked file feeds the cookie-domain registry and FP&A Buddy's allowlist.
A test that points LINKLIB_DB at a temp database without also setting
LINKLIB_SITES_OPML used to regenerate it from throwaway feeds.
"""
import pathlib

import pytest

from linklib.db import Library

TRACKED = pathlib.Path(__file__).resolve().parents[1] / "preferred_sites.opml"


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def _add_feed(lib):
    sec = lib.add_feed_section("Scratch")
    lib.add_feed(name="Scratch feed", xml_url="https://scratch.example/feed",
                 html_url="https://scratch.example", section_id=sec)


def test_write_opml_refuses_the_tracked_file_under_pytest(lib):
    before = TRACKED.read_bytes()
    _add_feed(lib)
    try:
        with pytest.raises(RuntimeError, match="tracked preferred_sites.opml"):
            lib.write_opml(str(TRACKED))
    finally:
        TRACKED.write_bytes(before)  # restore, so a failing run can't leave it dirty
    assert TRACKED.read_bytes() == before


def test_write_opml_still_writes_a_temp_path(lib, tmp_path):
    _add_feed(lib)
    out = tmp_path / "feeds.opml"
    assert lib.write_opml(str(out)) is True
    assert "scratch.example" in out.read_text(encoding="utf-8")
