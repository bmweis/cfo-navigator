"""Unit coverage for scripts/audit_tool_logo_dimensions.py's pure-stdlib
image-header parser (item 6, empty-state visual QA pass). This can't be
verified against real production logo files from this session (no
filesystem/network access to the Railway volume they live on — see the
script's own module docstring and CLAUDE.md's Step 0 report for this PR),
so what's verified here is the parsing logic itself against small,
hand-built, byte-exact sample files in each supported format — proving the
mechanism works, even though its output against the real corpus is
necessarily unverified until Brian runs it via `railway ssh`.
"""
import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.audit_tool_logo_dimensions import probe_dimensions, _rows_to_audit  # noqa: E402


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _make_png(w, h):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _make_gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 100


def _make_jpeg(w, h):
    # SOI + a minimal SOF0 segment (length=8: precision + height + width +
    # 1 component byte, no scan data needed — the parser stops at SOF0).
    sof = struct.pack(">HBHHB", 8, 8, h, w, 0)
    return b"\xff\xd8\xff\xc0" + sof + b"\x00" * 4


def _make_webp_vp8x(w, h):
    payload = struct.pack("<I", 10) + b"\x00\x00\x00\x00" + \
        bytes([(w - 1) & 0xFF, ((w - 1) >> 8) & 0xFF, ((w - 1) >> 16) & 0xFF]) + \
        bytes([(h - 1) & 0xFF, ((h - 1) >> 8) & 0xFF, ((h - 1) >> 16) & 0xFF])
    chunk = b"VP8X" + payload
    riff_size = 4 + len(chunk)  # "WEBP" + chunk
    return b"RIFF" + struct.pack("<I", riff_size) + b"WEBP" + chunk


def _make_svg_with_viewbox(w, h):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><rect/></svg>'.encode()


def _make_svg_with_wh(w, h):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><rect/></svg>'.encode()


def test_png_dimensions(tmp_path):
    p = _write(tmp_path, "logo.png", _make_png(64, 128))
    assert probe_dimensions(p) == (64, 128)


def test_gif_dimensions(tmp_path):
    p = _write(tmp_path, "logo.gif", _make_gif(32, 32))
    assert probe_dimensions(p) == (32, 32)


def test_jpeg_dimensions(tmp_path):
    p = _write(tmp_path, "logo.jpg", _make_jpeg(200, 50))
    assert probe_dimensions(p) == (200, 50)


def test_webp_vp8x_dimensions(tmp_path):
    p = _write(tmp_path, "logo.webp", _make_webp_vp8x(300, 90))
    assert probe_dimensions(p) == (300, 90)


def test_svg_viewbox_dimensions(tmp_path):
    p = _write(tmp_path, "logo.svg", _make_svg_with_viewbox(1400, 120))
    assert probe_dimensions(p) == (1400.0, 120.0)


def test_svg_width_height_dimensions(tmp_path):
    p = _write(tmp_path, "logo.svg", _make_svg_with_wh(48, 48))
    assert probe_dimensions(p) == (48.0, 48.0)


def test_svg_with_neither_returns_none(tmp_path):
    p = _write(tmp_path, "logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    assert probe_dimensions(p) is None


def test_corrupt_file_returns_none(tmp_path):
    p = _write(tmp_path, "logo.png", b"not a real image")
    assert probe_dimensions(p) is None


def test_missing_file_returns_none(tmp_path):
    assert probe_dimensions(str(tmp_path / "nope.png")) is None


def test_rows_to_audit_only_includes_populated_logo_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLIB_DB", str(tmp_path / "t.db"))
    from linklib.db import Library
    lib = Library(str(tmp_path / "t.db"))
    a = lib.add_tool("Has Logo", "d", "https://haslogo.example.com", ["ERP"], approved=1)
    lib.add_tool("No Logo", "d", "https://nologo.example.com", ["ERP"], approved=1)
    lib.conn.execute("UPDATE tools SET logo_path=? WHERE id=?", ("logos/tools/haslogo.png", a))
    lib.conn.commit()

    rows = _rows_to_audit(lib)
    lib.close()
    assert ("tool", "Has Logo", "logos/tools/haslogo.png") in rows
    assert not any(name == "No Logo" for _, name, _ in rows)
