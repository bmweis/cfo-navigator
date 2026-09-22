#!/usr/bin/env python3
"""Empty-state visual QA pass (item 6, amended scope) — audits every tool/
community logo asset already on disk for undersized/poorly-cropped source
images, so Brian has a hand-replacement worklist for the existing manual
"Revert & re-fetch" / manual-override process (see CLAUDE.md's Manual logo
override bullet) — NOT a fetch/re-fetch/pipeline change of any kind. This
script never calls Brandfetch, never writes to the database, and never
touches a logo file; it only reads what's already on disk and reports.

WHY THIS SCRIPT EXISTS, RATHER THAN A CODE FIX: `_logo_box()` (webapp/app.py)
renders every logo with `object-fit:contain` at a fixed tile size (64px on
the directory, 56px on a profile header, 32px in the Competitors table) —
correct, upscale-capable CSS with no bug to fix there. A "tiny logo inside
its tile" (Airbase, Airwallex reported live) is a property of the SOURCE
ASSET, not the renderer: either a low-resolution raster (a favicon-derived
image with few real pixels to scale up from) or, more likely for a
wordmark-style brand mark, an asset with a lot of built-in transparent
padding around a small centered mark — `object-fit:contain` faithfully
preserves that padding, so the visible logo reads small even though the
image file itself is scaled correctly to fill the box. Confirming which is
true per-tool needs the actual file, which lives on the Railway volume
(`logos/tools/`, `logos/communities/`, beside library.db) — this script is
built to run there via `railway ssh`, mirroring every other production-only
diagnostic in this repo (see scripts/backfill_logos.py, scripts/
report_brandfetch_coverage.py).

NO NEW DEPENDENCY: rather than add Pillow just to read image dimensions
(the project has deliberately avoided image-processing dependencies before
— see CLAUDE.md's App screenshot Phase E note on choosing client-side
Cropper.js specifically to avoid one), this parses PNG/JPEG/GIF/WEBP/ICO
headers and SVG width/height/viewBox attributes by hand, using only the
stdlib. Good enough for a read-only audit; not a general-purpose image
library.

Two independent findings, reported per row:
  - undersized: the asset's own smaller dimension is below --min-px (a
    real ceiling on how crisp it can ever render at the largest on-site
    tile, 64px, even before accounting for a 2x-density display).
  - lopsided: the asset's aspect ratio falls outside [1/--max-ratio,
    --max-ratio] — the more likely explanation for "tiny logo in a square
    tile," since `object-fit:contain` shrinks a wide/tall image to fit
    the tile's limiting dimension, leaving the rest of the square empty.
  - unreadable: a file that exists but this script's header parser
    couldn't determine dimensions for (an SVG with neither a `width` nor a
    `viewBox`, a WEBP variant this parser doesn't handle, a corrupt file,
    ...) — flagged for manual inspection, not silently skipped.

SVGs are vector — "pixel dimensions" isn't quite the right question, but
the same lopsided-aspect-ratio check still applies (a wordmark SVG with a
1400x120 viewBox will still look tiny in a square tile), and a missing/
malformed viewBox is itself worth flagging (some renderers fall back to a
default intrinsic size in that case, which is its own source of tiny
logos).

Usage:
    python -m scripts.audit_tool_logo_dimensions --db /data/library.db
    python -m scripts.audit_tool_logo_dimensions --db /data/library.db --min-px 96 --max-ratio 2.0
    python -m scripts.audit_tool_logo_dimensions --db /data/library.db --csv logo_audit.csv

Read-only: no Brand API calls, no file writes, no DB writes.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

DEFAULT_MIN_PX = 128  # a safe floor for crisp rendering at the largest on-site
# tile (64px) on a 2x-density display — half that, and the browser is
# upscaling past what the source actually has.
DEFAULT_MAX_RATIO = 2.0  # aspect ratio (long side / short side) beyond which
# object-fit:contain leaves a visually obvious amount of empty tile around
# the logo mark.

_DIR_BY_KIND = {"tool": "tools", "community": "communities"}


def _logos_root(db_path: str) -> str:
    """Mirrors scripts/backfill_logos.py's own _logos_root exactly — the
    files this script reads are the ones that script (or a manual upload)
    already wrote there."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "logos")


# -- Pure-stdlib image dimension probing -----------------------------------

def _dims_png(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return (w, h)


def _dims_gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    w, h = struct.unpack("<HH", data[6:10])
    return (w, h)


def _dims_jpeg(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # Standalone markers with no length field.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        # SOF0-SOF15, excluding DHT(C4)/JPG(C8)/DAC(CC).
        if marker in range(0xC0, 0xD0) and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return (w, h)
        i += 2 + seg_len
    return None


def _dims_webp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        # 24-bit width-1 / height-1, little-endian, starting at byte 24.
        b = data[24:30]
        if len(b) < 6:
            return None
        w = 1 + (b[0] | (b[1] << 8) | (b[2] << 16))
        h = 1 + (b[3] | (b[4] << 8) | (b[5] << 16))
        return (w, h)
    if chunk == b"VP8 " and len(data) >= 30:
        # Lossy: 3-byte start code at 23, then 2 bytes width/height w/ 14-bit
        # values (top 2 bits are scale flags) at 26 and 28.
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return (w, h) if w and h else None
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        bits = int.from_bytes(b, "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return (w, h)
    return None


def _dims_ico(data: bytes) -> tuple[int, int] | None:
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        return None
    count = struct.unpack("<H", data[4:6])[0]
    best = None
    for idx in range(count):
        off = 6 + idx * 16
        if off + 16 > len(data):
            break
        w = data[off] or 256
        h = data[off + 1] or 256
        if best is None or (w * h) > (best[0] * best[1]):
            best = (w, h)
    return best


_SVG_DIM_RE = re.compile(r'(width|height)\s*=\s*["\']?\s*([\d.]+)')
_SVG_VIEWBOX_RE = re.compile(r'viewBox\s*=\s*["\']?\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)', re.I)


def _dims_svg(data: bytes) -> tuple[float, float] | None:
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return None
    dims = dict(_SVG_DIM_RE.findall(text[:4000]))
    if "width" in dims and "height" in dims:
        try:
            return (float(dims["width"]), float(dims["height"]))
        except ValueError:
            pass
    m = _SVG_VIEWBOX_RE.search(text[:4000])
    if m:
        try:
            return (float(m.group(1)), float(m.group(2)))
        except ValueError:
            pass
    return None


def probe_dimensions(path: str) -> tuple[float, float] | None:
    """Best-effort (width, height) for a PNG/JPEG/GIF/WEBP/ICO/SVG file.
    Returns None if the format isn't recognized or dimensions couldn't be
    parsed — callers should treat that as "unreadable", not "fine"."""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext == ".svg":
        return _dims_svg(head)
    for probe in (_dims_png, _dims_gif, _dims_jpeg, _dims_webp, _dims_ico):
        dims = probe(head)
        if dims:
            return dims
    return None


def _rows_to_audit(lib: Library) -> list[tuple[str, str, str]]:
    """(kind, name, logo_path) for every tool/community with a non-empty
    logo_path, approved or not — an unapproved row can still have a logo
    worth checking before it goes live."""
    out: list[tuple[str, str, str]] = []
    for t in lib.conn.execute("SELECT name, logo_path FROM tools WHERE logo_path != ''").fetchall():
        out.append(("tool", t["name"], t["logo_path"]))
    for c in lib.conn.execute("SELECT name, logo_path FROM communities WHERE logo_path != ''").fetchall():
        out.append(("community", c["name"], c["logo_path"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    ap.add_argument("--min-px", type=int, default=DEFAULT_MIN_PX,
                     help=f"flag any asset whose smaller dimension is below this (default {DEFAULT_MIN_PX})")
    ap.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO,
                     help=f"flag any asset whose long:short aspect ratio exceeds this (default {DEFAULT_MAX_RATIO})")
    ap.add_argument("--csv", default=None, help="also write the full report to this CSV path")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Auditing logos referenced by: {db_path}")
    lib = Library(db_path)
    root = _logos_root(db_path)

    rows = _rows_to_audit(lib)
    lib.close()
    print(f"{len(rows)} tool/community rows have a logo_path set. Logos root: {root}\n")

    report = []
    for kind, name, logo_path in rows:
        full = os.path.join(root, _DIR_BY_KIND[kind], os.path.basename(logo_path))
        exists = os.path.isfile(full)
        dims = probe_dimensions(full) if exists else None
        flags = []
        if not exists:
            flags.append("missing-file")
        elif dims is None:
            flags.append("unreadable")
        else:
            w, h = dims
            if min(w, h) < args.min_px:
                flags.append("undersized")
            ratio = (max(w, h) / min(w, h)) if min(w, h) else float("inf")
            if ratio > args.max_ratio:
                flags.append("lopsided")
        report.append({
            "kind": kind, "name": name, "path": logo_path,
            "width": dims[0] if dims else "", "height": dims[1] if dims else "",
            "flags": ",".join(flags),
        })

    flagged = [r for r in report if r["flags"]]
    flagged.sort(key=lambda r: (r["kind"], r["name"]))
    if flagged:
        print(f"{len(flagged)} of {len(report)} logos flagged (threshold: min-px={args.min_px}, max-ratio={args.max_ratio}):\n")
        for r in flagged:
            dims_str = f"{r['width']}x{r['height']}" if r["width"] != "" else "?"
            print(f"  [{r['flags']:<20}] {r['kind']:<9} {r['name']:<30} {dims_str:<12} {r['path']}")
    else:
        print("No flagged logos — every asset on disk clears both thresholds.")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["kind", "name", "path", "width", "height", "flags"])
            writer.writeheader()
            writer.writerows(report)
        print(f"\nFull report (including unflagged rows) written to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
