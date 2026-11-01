"""Part E (2026-11): the FP&A Buddy report and its CSV show times in an admin-set zone (default
America/New_York), with the zone abbreviation. Display only; stored times stay UTC."""
import csv
import io
import os
import tempfile

import pytest

from linklib import display_time as dt
from linklib.db import Library


def test_a_late_evening_eastern_run_shows_its_own_date():
    # 2026-11-01 00:11 UTC is 8:11 pm on 10-31 in New York (daylight time still on).
    assert dt.local_stamp("2026-11-01T00:11:23+00:00", "America/New_York") == "2026-10-31 20:11 EDT"
    assert dt.local_stamp("2026-11-02T00:11:23+00:00", "America/New_York") == "2026-11-01 19:11 EST"   # DST ended 11-01 at 2 am


def test_default_zone_utc_and_unreadable_values():
    assert dt.local_stamp("2026-11-01T00:11:23+00:00") == "2026-10-31 20:11 EDT"
    assert dt.local_stamp("2026-11-01T00:11:23+00:00", "UTC") == "2026-11-01 00:11 UTC"
    assert dt.local_stamp("2026-11-01T00:11:23", "UTC") == "2026-11-01 00:11 UTC"          # naive is read as UTC
    assert dt.local_stamp("not a date", "UTC") == "not a date"
    assert dt.zone_for("Mars/Base")[1] == dt.DEFAULT_ZONE


def test_is_valid_zone():
    assert dt.is_valid_zone("America/Chicago") and not dt.is_valid_zone("Nowhere/City") and not dt.is_valid_zone("")


@pytest.fixture
def env(monkeypatch):
    db = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("LINKLIB_DB", db)
    monkeypatch.setenv("LINKLIB_PASSWORD", "adminpass")
    monkeypatch.setenv("LINKLIB_SECRET_KEY", "k")
    import importlib
    import webapp.app as appmod
    importlib.reload(appmod)
    lib = Library(db)
    uid = lib.create_user("asker", "supersecret", role="user")
    qid = lib.record_ask_question(uid, "Late evening question", "a1", "claude-sonnet-4-6", "standard", True, False, True, cost_usd=0.01)
    lib.conn.execute("UPDATE ask_questions SET created_at='2026-11-01T00:11:23+00:00' WHERE id=?", (qid,))
    lib.conn.commit()
    lib.close()
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/login", data={"username": "admin", "password": "adminpass"}, follow_redirects=False)
    yield c, db
    if os.path.exists(db):
        os.remove(db)


def test_report_shows_eastern_time_with_the_abbreviation(env):
    c, _ = env
    html = c.get("/admin/fpa-buddy/report").text
    assert "2026-10-31 20:11 EDT" in html and ">Date and time<" in html
    assert "2026-11-01</td>" not in html


def test_csv_adds_one_local_time_column_and_keeps_the_utc_date(env):
    c, _ = env
    rows = list(csv.reader(io.StringIO(c.get("/admin/fpa-buddy/report/export.csv").text)))
    assert rows[0][0] == "date" and rows[0][-1] == "local_time" and rows[0].count("local_time") == 1
    assert rows[1][0].startswith("2026-11-01T00:11") and rows[1][-1] == "2026-10-31 20:11 EDT"


def test_the_zone_can_be_changed_and_a_bad_name_is_refused(env):
    c, db = env
    r = c.post("/admin/fpa-buddy/report/timezone", data={"timezone": "America/Los_Angeles"}, follow_redirects=False)
    assert r.status_code == 303
    assert "2026-10-31 17:11 PDT" in c.get("/admin/fpa-buddy/report").text
    bad = c.post("/admin/fpa-buddy/report/timezone", data={"timezone": "Nowhere/City"}, follow_redirects=False)
    assert bad.status_code == 400
    lib = Library(db)
    assert lib.get_setting(dt.SETTING_KEY) == "America/Los_Angeles"
    lib.close()


def test_the_zone_route_needs_an_admin_session(env):
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    anon = TestClient(appmod.app)
    r = anon.post("/admin/fpa-buddy/report/timezone", data={"timezone": "UTC"}, follow_redirects=False)
    assert r.status_code in (302, 303, 401) and "/login" in r.headers.get("location", "/login")
