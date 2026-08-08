"""Every shared inline <script> block in webapp/app.py parses as valid JS
(node --check), run against the actual resolved Python string values—not a
regex over app.py's raw source text.

2026-08 lesson (see CLAUDE.md): a prior fix validated raw source bytes and
reported success, but the *_JS constants below are plain (non-f-string)
Python strings, so Python resolves their escape sequences (\\', \\\\, etc.)
between source and the value actually embedded in the page. A regex over
app.py's text checks a different string than the one the browser receives.
Reading each constant via `webapp.app`'s already-imported attributes (as
`webapp.checks._js_constants()` does) is what makes this test catch that
class of bug instead of repeating it.

Skips (doesn't fail) when Node isn't on PATH—see webapp/checks.py's
`script_syntax_problems` docstring for why. GitHub-hosted Actions runners
ship Node by default, so this always runs there.
"""
import os
import shutil
import tempfile

import pytest

os.environ.setdefault("LINKLIB_DB", tempfile.mktemp(suffix=".db"))

from webapp import checks

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH in this environment"
)


def test_every_js_constant_is_discovered():
    # A minimum-count sanity check, not an exhaustive list—so this test fails
    # loudly (telling you to look) rather than silently covering fewer blocks
    # than intended if a future refactor renames/removes one of these without
    # noticing it dropped out of the *_JS naming convention.
    names = set(checks._js_constants())
    expected_min = {
        "_SDR_JS", "_MARK_AI_DRAFTED_JS", "_GENERATE_DESC_JS",
        "_GENERATE_PROFILE_JS", "_GENERATE_LISTING_JS",
        "_ADMIN_BULK_EDIT_JS", "_ADMIN_SORT_FILTER_JS",
    }
    missing = expected_min - names
    assert not missing, f"expected _JS constants not found (renamed or removed?): {missing}"


def test_concatenated_script_tag_pairs_are_discovered():
    pairs = checks._concatenated_script_tags()
    assert ("_ADMIN_BULK_EDIT_JS", "_ADMIN_SORT_FILTER_JS") in pairs


def test_all_shared_script_blocks_parse_as_valid_js():
    problems = checks.script_syntax_problems()
    assert problems is not None, "node should be on PATH per the module skip above"
    assert not problems, "\n".join(problems)
