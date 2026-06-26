#!/usr/bin/env python3
"""Review written content against Brian's voice.

Mechanical rules (banned words, filler, performative phrases) are checked
deterministically; tone is judged by Claude when ANTHROPIC_API_KEY is set.

Usage:
    python -m scripts.voice_review path/to/file.md
    pbpaste | python -m scripts.voice_review        # read stdin
    python -m scripts.voice_review --db library.db  # use saved custom voice
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib.voice_review import review_text


def main() -> int:
    ap = argparse.ArgumentParser(description="Review content against Brian's voice.")
    ap.add_argument("file", nargs="?", help="File to review (omit to read stdin).")
    ap.add_argument("--db", default=os.environ.get("LINKLIB_DB", "library.db"),
                    help="Library DB to read the saved custom voice from (if any).")
    args = ap.parse_args()

    text = (open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()).strip()
    if not text:
        print("No content to review.", file=sys.stderr)
        return 2

    custom_voice = None
    if os.path.exists(args.db):
        try:
            from linklib.db import Library
            lib = Library(args.db)
            try:
                custom_voice = lib.get_setting("voice_prompt")
            finally:
                lib.close()
        except Exception:
            pass

    result = review_text(text, voice_prompt=custom_voice or None)

    mech = result["mechanical"]
    print("== Mechanical check ==")
    if mech:
        for rule, phrase in mech:
            print(f"  ✗ {rule}: {phrase!r}")
    else:
        print("  ✓ no mechanical violations")
    print("\n== Tone review ==")
    print(result["review"])
    # Non-zero exit if there are hard mechanical violations — useful for scripting.
    return 1 if mech else 0


if __name__ == "__main__":
    raise SystemExit(main())
