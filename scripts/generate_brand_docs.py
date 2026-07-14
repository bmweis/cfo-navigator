#!/usr/bin/env python3
"""Regenerate BRAND.md §7 (the CSS token reference) from the live :root block.

BRAND.md's palette table used to be a hand-copied parallel of webapp/app.py's
`_CSS` :root block, and it drifted (see PR #129's audit — a stale --font-read
token that was never actually in the live CSS). This makes the CSS the single
source: the generated section is BRAND.md's fenced ```css``` block for §7,
lifted verbatim from `linklib.brand_check.brand_root_block()`.

Usage:
    python -m scripts.generate_brand_docs          # regenerate BRAND.md in place
    python -m scripts.generate_brand_docs --check   # exit 1 if BRAND.md is stale (no write)
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.brand_check import brand_root_block

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_PY = ROOT / "webapp" / "app.py"
BRAND_MD = ROOT / "BRAND.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED TOKENS (scripts/generate_brand_docs.py) -->"
END_MARKER = "<!-- END GENERATED TOKENS -->"


def generated_section(app_src: str) -> str:
    """The full generated span for BRAND.md §7: markers, banner, fenced CSS."""
    return (
        f"{BEGIN_MARKER}\n"
        "> Generated from `webapp/app.py`'s `:root` block — don't hand-edit this table.\n"
        "> To change the brand, edit the CSS, then run `python -m scripts.generate_brand_docs`.\n\n"
        "```css\n" + brand_root_block(app_src) + "\n```\n"
        f"{END_MARKER}"
    )


def render(brand_md_src: str, app_src: str) -> str:
    """BRAND.md with the generated span swapped in for the current one."""
    start = brand_md_src.index(BEGIN_MARKER)
    end = brand_md_src.index(END_MARKER) + len(END_MARKER)
    return brand_md_src[:start] + generated_section(app_src) + brand_md_src[end:]


def stale(brand_md_src: str, app_src: str) -> bool:
    return render(brand_md_src, app_src) != brand_md_src


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="Exit 1 if BRAND.md §7 is out of date; don't write.")
    args = ap.parse_args()

    app_src = APP_PY.read_text(encoding="utf-8")
    brand_md_src = BRAND_MD.read_text(encoding="utf-8")
    updated = render(brand_md_src, app_src)

    if args.check:
        if updated != brand_md_src:
            print("BRAND.md §7 is out of date. Run `python -m scripts.generate_brand_docs` "
                  "and commit the diff.", file=sys.stderr)
            return 1
        print("BRAND.md §7 is up to date.")
        return 0

    if updated != brand_md_src:
        BRAND_MD.write_text(updated, encoding="utf-8")
        print("BRAND.md §7 regenerated.")
    else:
        print("BRAND.md §7 already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
