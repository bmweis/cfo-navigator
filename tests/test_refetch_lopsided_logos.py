"""Unit coverage for scripts/refetch_lopsided_logos.py — the Logo Tile Fit
fix's run-once re-fetch script. Covers selection logic (lopsided-only,
excludes manual overrides, excludes the Cube/Kintsugi defensive skip list)
against a temp DB + hand-built sample PNGs, and the preview/apply flow
end-to-end with fetch_logo_asset/download_asset monkeypatched — never a
real network call, never real quota spend.
"""
import os
import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library
from scripts import refetch_lopsided_logos as script


def _make_png(w, h):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _setup(tmp_path):
    db_path = str(tmp_path / "library.db")
    lib = Library(db_path)
    logos_dir = tmp_path / "logos" / "tools"
    logos_dir.mkdir(parents=True)

    def add(name, domain, w, h, manual=False):
        tid = lib.add_tool(name, "desc", f"https://www.{domain}", ["ERP"], approved=1)
        slug = lib.get_tool(tid)["slug"]
        (logos_dir / f"{slug}.png").write_bytes(_make_png(w, h))
        rel = f"logos/tools/{slug}.png"
        if manual:
            lib.set_tool_logo_manual(tid, rel)
        else:
            lib.conn.execute("UPDATE tools SET logo_path=? WHERE id=?", (rel, tid))
            lib.conn.commit()
        return tid

    return lib, db_path, add


# --- selection (_candidates) ------------------------------------------------

def test_candidates_selects_only_lopsided_assets(tmp_path):
    lib, db_path, add = _setup(tmp_path)
    wide_id = add("WideCo", "wideco.com", 400, 40)
    add("SquareCo", "squareco.com", 100, 100)
    root = script._logos_root(db_path)
    candidates = script._candidates(lib, root, script.DEFAULT_MAX_RATIO)
    lib.close()
    assert [row["id"] for _, row, _, _ in candidates] == [wide_id]


def test_candidates_excludes_manual_override_rows(tmp_path):
    lib, db_path, add = _setup(tmp_path)
    add("ManualCo", "manualco.com", 400, 40, manual=True)
    root = script._logos_root(db_path)
    candidates = script._candidates(lib, root, script.DEFAULT_MAX_RATIO)
    lib.close()
    assert candidates == []


def test_candidates_skips_cube_and_kintsugi_by_name_even_if_lopsided(tmp_path):
    lib, db_path, add = _setup(tmp_path)
    add("Cube", "cube.com", 400, 40)
    add("Kintsugi", "kintsugi.com", 400, 40)
    add("cube", "cube-lower.com", 400, 40)  # case-insensitive match too
    root = script._logos_root(db_path)
    candidates = script._candidates(lib, root, script.DEFAULT_MAX_RATIO)
    lib.close()
    assert candidates == []


def test_candidates_ignores_missing_file(tmp_path):
    lib, db_path, add = _setup(tmp_path)
    tid = add("GoneCo", "goneco.com", 400, 40)
    os.remove(str(tmp_path / "logos" / "tools" / f"{lib.get_tool(tid)['slug']}.png"))
    root = script._logos_root(db_path)
    candidates = script._candidates(lib, root, script.DEFAULT_MAX_RATIO)
    lib.close()
    assert candidates == []


def test_candidates_scans_communities_too(tmp_path):
    lib, db_path, add = _setup(tmp_path)
    # communities live in a sibling logos/communities/ dir
    cdir = tmp_path / "logos" / "communities"
    cdir.mkdir(parents=True)
    cid = lib.add_community("WideCommunity", "https://www.widecommunity.com", "desc", "Free",
                             ["Peer Group"], approved=1)
    slug = lib.get_community(cid)["slug"]
    (cdir / f"{slug}.png").write_bytes(_make_png(400, 40))
    lib.conn.execute("UPDATE communities SET logo_path=? WHERE id=?", (f"logos/communities/{slug}.png", cid))
    lib.conn.commit()
    root = script._logos_root(db_path)
    candidates = script._candidates(lib, root, script.DEFAULT_MAX_RATIO)
    lib.close()
    assert [(kind, row["id"]) for kind, row, _, _ in candidates] == [("community", cid)]


# --- preview mode: zero API calls ------------------------------------------

def test_preview_mode_makes_zero_brandfetch_calls(tmp_path, monkeypatch, capsys):
    lib, db_path, add = _setup(tmp_path)
    add("WideCo", "wideco.com", 400, 40)
    lib.close()

    calls = []
    monkeypatch.setattr(script, "fetch_logo_asset", lambda *a, **k: calls.append(1) or (None, "should not be called"))
    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path])
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == []
    assert "PREVIEW ONLY" in out
    assert "WideCo" in out


def test_preview_mode_with_no_candidates_reports_nothing_to_do(tmp_path, monkeypatch, capsys):
    lib, db_path, add = _setup(tmp_path)
    add("SquareCo", "squareco.com", 100, 100)
    lib.close()
    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path])
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to do" in out.lower()


# --- apply mode --------------------------------------------------------------

def test_apply_mode_requires_api_key(tmp_path, monkeypatch, capsys):
    lib, db_path, add = _setup(tmp_path)
    add("WideCo", "wideco.com", 400, 40)
    lib.close()
    monkeypatch.delenv("BRANDFETCH_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path, "--apply"])
    rc = script.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert "BRANDFETCH_API_KEY" in err


def test_apply_mode_switches_to_square_asset_and_writes(tmp_path, monkeypatch, capsys):
    lib, db_path, add = _setup(tmp_path)
    tid = add("WideCo", "wideco.com", 400, 40)
    lib.close()

    def fake_fetch(domain, api_key, session=None):
        return ("https://cdn.example/icon.png", "png", "icon"), None

    def fake_download(src_url, dest_path, session=None):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(_make_png(80, 80))

    monkeypatch.setenv("BRANDFETCH_API_KEY", "fake-key")
    monkeypatch.setattr(script, "fetch_logo_asset", fake_fetch)
    monkeypatch.setattr(script, "download_asset", fake_download)
    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path, "--apply"])
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 switched to a square asset" in out
    assert "[OK]" in out

    lib2 = Library(db_path)
    row = lib2.get_tool(tid)
    lib2.close()
    assert row["logo_path"] == f"logos/tools/{row['slug']}.png"


def test_apply_mode_leaves_wordmark_only_result_unchanged(tmp_path, monkeypatch, capsys):
    """When Brandfetch still has no icon/symbol asset for a brand, the
    script must NOT overwrite the existing (still-wordmark) file, and must
    report it as a residual manual-override case."""
    lib, db_path, add = _setup(tmp_path)
    tid = add("OnlyWordmarkCo", "onlywordmark.com", 400, 40)
    original_row = lib.get_tool(tid)
    lib.close()

    def fake_fetch(domain, api_key, session=None):
        return ("https://cdn.example/logo.svg", "svg", "logo"), None  # still a wordmark

    download_calls = []

    def fake_download(src_url, dest_path, session=None):
        download_calls.append(dest_path)

    monkeypatch.setenv("BRANDFETCH_API_KEY", "fake-key")
    monkeypatch.setattr(script, "fetch_logo_asset", fake_fetch)
    monkeypatch.setattr(script, "download_asset", fake_download)
    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path, "--apply"])
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert download_calls == []  # never re-downloaded the same-shaped asset
    assert "STILL WORDMARK-ONLY" in out
    assert "manual logo-override worklist" in out
    assert "OnlyWordmarkCo" in out

    lib2 = Library(db_path)
    row_after = lib2.get_tool(tid)
    lib2.close()
    assert row_after["logo_path"] == original_row["logo_path"]  # untouched


def test_apply_mode_stops_on_quota(tmp_path, monkeypatch, capsys):
    lib, db_path, add = _setup(tmp_path)
    add("FirstCo", "firstco.com", 400, 40)
    add("SecondCo", "secondco.com", 400, 40)
    lib.close()

    calls = []

    def fake_fetch(domain, api_key, session=None):
        calls.append(domain)
        return None, "QUOTA: 429 rate-limited/quota-exceeded — stopping the run, not just this record"

    monkeypatch.setenv("BRANDFETCH_API_KEY", "fake-key")
    monkeypatch.setattr(script, "fetch_logo_asset", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path, "--apply"])
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped early on quota" in out
    assert len(calls) == 1  # stopped after the first QUOTA response, never tried the second


def test_apply_mode_never_writes_over_manual_override(tmp_path, monkeypatch, capsys):
    """Defense in depth: even if a manual-override row somehow reached the
    candidate list, set_tool_logo must refuse the write."""
    lib, db_path, add = _setup(tmp_path)
    tid = add("ManualRaceCo", "manualrace.com", 400, 40, manual=True)
    original_row = lib.get_tool(tid)

    def fake_fetch(domain, api_key, session=None):
        return ("https://cdn.example/icon.png", "png", "icon"), None

    def fake_download(src_url, dest_path, session=None):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(_make_png(80, 80))

    monkeypatch.setenv("BRANDFETCH_API_KEY", "fake-key")
    monkeypatch.setattr(script, "fetch_logo_asset", fake_fetch)
    monkeypatch.setattr(script, "download_asset", fake_download)

    # Force this manual-override row into the candidate list directly,
    # bypassing _candidates' own exclusion, to prove set_tool_logo itself
    # is the real backstop.
    fake_candidates = [("tool", dict(original_row), (400, 40), 10.0)]
    monkeypatch.setattr(script, "_candidates", lambda lib, root, max_ratio: fake_candidates)
    lib.close()

    monkeypatch.setattr(sys, "argv", ["refetch_lopsided_logos.py", "--db", db_path, "--apply"])
    rc = script.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "manual logo override is active" in out

    lib2 = Library(db_path)
    row_after = lib2.get_tool(tid)
    lib2.close()
    assert row_after["logo_path"] == original_row["logo_path"]
