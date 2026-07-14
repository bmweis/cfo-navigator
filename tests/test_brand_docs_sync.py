"""Keep BRAND.md §7 in sync with the live :root block (see BRAND.md §7's banner).

The sync logic lives in ``webapp/checks.py`` (the single source, also used by
the /admin/checks dashboard) and ``scripts/generate_brand_docs.py`` (the
generator, run manually to fix a failure here).
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import checks


def test_brand_docs_in_sync():
    problems = checks.brand_docs_problems()
    assert not problems, "\n".join(problems)
