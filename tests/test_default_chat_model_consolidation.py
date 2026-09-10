"""Coverage for the model-config consolidation (default-model literal, not
behavior): agent.py/matchmaker.py's identical `LINKLIB_CHAT_MODEL`
fallback now resolves to one shared `linklib.models.DEFAULT_CHAT_MODEL`
constant instead of separately-typed literals; dedupe.py keeps its own
extra `LINKLIB_DEDUPE_MODEL` override layer, with only its innermost literal
now pointing at that same constant.

(linklib.suggest and its QUEUE_ENRICH_MODEL/DEFAULT_MODEL pairing were
retired along with the Archive Queue — see PR 3's retirement of
linklib/queue.py and linklib/suggest.py — so both were dropped from the
module tuples below rather than left to ImportError.)

Every assertion here is about the *resolved value*, run in a subprocess with a
controlled environment — importing these modules in-process wouldn't actually
exercise `os.environ.get`'s fallback branch, since Python caches the module
the first time anything imports it in this test session.
"""
from __future__ import annotations

import subprocess
import sys

from linklib.models import DEFAULT_CHAT_MODEL


def _resolve(module: str, attr: str, env: dict[str, str] | None = None) -> str:
    code = f"import os; import {module} as m; print(m.{attr})"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, check=True,
    )
    return result.stdout.strip()


def test_shared_constant_is_the_confirmed_default():
    assert DEFAULT_CHAT_MODEL == "claude-sonnet-4-6"


def test_agent_matchmaker_default_to_the_shared_constant_with_no_env():
    import os
    env = {"PATH": os.environ.get("PATH", "")}
    for module in ("linklib.agent", "linklib.matchmaker"):
        assert _resolve(module, "DEFAULT_MODEL", env=env) == DEFAULT_CHAT_MODEL


def test_agent_matchmaker_still_honor_linklib_chat_model_override():
    import os
    env = {"PATH": os.environ.get("PATH", ""), "LINKLIB_CHAT_MODEL": "custom-chat-model"}
    for module in ("linklib.agent", "linklib.matchmaker"):
        assert _resolve(module, "DEFAULT_MODEL", env=env) == "custom-chat-model"


def test_dedupe_still_falls_back_to_the_shared_constant_with_no_env():
    import os
    env = {"PATH": os.environ.get("PATH", "")}
    assert _resolve("linklib.dedupe", "_VERIFY_MODEL", env=env) == DEFAULT_CHAT_MODEL


def test_dedupe_still_honors_linklib_chat_model_when_dedupe_model_unset():
    import os
    env = {"PATH": os.environ.get("PATH", ""), "LINKLIB_CHAT_MODEL": "custom-chat-model"}
    assert _resolve("linklib.dedupe", "_VERIFY_MODEL", env=env) == "custom-chat-model"


def test_dedupe_own_override_still_wins_over_linklib_chat_model():
    """The two-level fallback chain is deliberate, not drift — confirm it's
    still intact after the innermost literal moved to the shared constant."""
    import os
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LINKLIB_CHAT_MODEL": "custom-chat-model",
        "LINKLIB_DEDUPE_MODEL": "custom-dedupe-model",
    }
    assert _resolve("linklib.dedupe", "_VERIFY_MODEL", env=env) == "custom-dedupe-model"
