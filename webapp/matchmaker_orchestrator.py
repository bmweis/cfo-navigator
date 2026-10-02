"""Matchmaker's shared orchestration (Software + Communities) — cap check,
conversation-history rebuild, the `answer_software_question()`/
`answer_communities_question()` call, and `matchmaker_questions` recording
— used by both `POST /tools/software/find/chat` / `POST /tools/communities/
find/chat` (webapp/app.py) and the `ask_matchmaker` MCP tool
(webapp/mcp_qa.py, MCP Phase 5).

Extracted from the two nearly-identical HTTP routes verbatim (same order of
operations, same return shapes, same cap/history/recording logic — the two
routes differed only in `kind`, which wrapper function to call, and the
"browse the directory" URL in the capped-budget message) rather than
rewritten. tests/test_software_matchmaker.py and
tests/test_communities_matchmaker.py exercise this code through the two
HTTP routes with zero changes to their own assertions — a regression here
shows up as a route-level test failure.

Deliberately decoupled from FastAPI, same reasoning as ask_orchestrator.py:
`run_matchmaker()` takes an already-resolved `user_id` (`int | None`) and
`session_id` (the caller's own notion of "who is this," never a `Request`).
The HTTP routes pass their `cfo_visitor` cookie value as `session_id`; the
MCP tool has no cookie to read (an MCP caller is always a real, resolved
user — never anonymous), so it synthesizes a stable per-user session key —
see mcp_qa.py for that mapping.
"""
from __future__ import annotations

from linklib.db import Library
from linklib.stop_reason import stop_reason_of

_BROWSE_URLS = {
    "software": "/tools/software",
    "community": "/tools/communities",
}


class UnknownConversationError(Exception):
    """`conversation_id` doesn't match any recorded matchmaker_questions rows."""


class ForbiddenConversationError(Exception):
    """`conversation_id` belongs to a different session/user than the caller."""


def run_matchmaker(
    lib: Library,
    kind: str,
    user_id: int | None,
    session_id: str,
    question: str,
    *,
    conversation_id: str = "",
) -> dict:
    """Answer one matchmaker turn, exactly as the two `POST /tools/{software,
    communities}/find/chat` routes do. `kind` is `"software"` or
    `"community"` (matching `matchmaker_questions.kind` and
    `linklib.matchmaker`'s own vocabulary — not "communities", note the
    singular, mirroring the existing routes' literal string).

    `question` must already be stripped/validated non-empty — a
    payload-shape concern the caller owns.

    Raises `UnknownConversationError`/`ForbiddenConversationError` for a
    bad `conversation_id`. Otherwise always returns a dict — either the
    `{"capped": True, "answer": ...}` shape (dollar cap or follow-up-count
    cap hit) or the full answer shape (answer/conversation_id/
    followups_left) — matching the two routes' JSON response contract
    field-for-field. Matchmaker has no citations mechanism (see
    linklib.matchmaker's own module docstring), so neither shape carries
    one.
    """
    from linklib.matchmaker import (
        answer_communities_question,
        answer_software_question,
        MAX_FOLLOWUPS,
    )

    if kind not in _BROWSE_URLS:
        raise ValueError(f"unknown matchmaker kind: {kind!r}")
    answer_fn = answer_software_question if kind == "software" else answer_communities_question
    browse_url = _BROWSE_URLS[kind]

    # Follow-up turn: rebuild history from the conversation's recorded rows,
    # same server-side-source-of-truth pattern as ask_orchestrator.run_ask.
    # Ownership requires BOTH session_id and (when set) user_id to match —
    # the session is the primary key since this feature needs no login, but
    # a signed-in conversation additionally can't be picked up by a
    # different signed-in user sharing the same session.
    history: list[dict] = []
    prior_questions = 0
    if conversation_id:
        turns = lib.list_matchmaker_conversation_turns(conversation_id)
        if not turns:
            raise UnknownConversationError(conversation_id)
        if turns[0]["session_id"] != session_id or turns[0]["user_id"] != user_id:
            raise ForbiddenConversationError(conversation_id)
        prior_questions = len(turns)
        if prior_questions >= 1 + MAX_FOLLOWUPS:
            return {
                "capped": True,
                "answer": "We've reached the limit for this conversation. "
                          "Start a new question to keep going.",
            }
        for t in turns:
            history.append({"role": "user", "content": t["question"]})
            history.append({"role": "assistant", "content": t["answer"]})

    # Dollar-based rate limit, mirroring ask_orchestrator.run_ask — but this
    # feature needs no login, so the common (web) case has no user_id to key
    # off of; anonymous spend is tracked (and capped) by session_id instead.
    # A signed-in caller (web or MCP) always gets their own per-user cap.
    if user_id is not None:
        cap = lib.get_effective_matchmaker_cap(user_id)
        spent = lib.matchmaker_cost_this_month(user_id)
    else:
        cap = lib.get_default_matchmaker_cap()
        spent = lib.matchmaker_cost_this_month_session(session_id)
    if spent >= cap:
        return {
            "capped": True,
            "answer": (f"We've used ${spent:.2f} of this month's ${cap:.2f} matchmaker "
                       f"budget. It resets at the start of next month—in the meantime, "
                       f"browse the full directory at {browse_url}."),
        }

    ans = answer_fn(lib, question, history=history)

    row_id = lib.record_matchmaker_question(
        session_id, kind, question, ans.text, ans.model,
        user_id=user_id, conversation_id=conversation_id, turn_index=prior_questions,
        input_tokens=ans.input_tokens, output_tokens=ans.output_tokens,
        cache_creation_tokens=ans.cache_creation_tokens, cache_read_tokens=ans.cache_read_tokens,
        cost_usd=ans.cost_usd,
        stop_reason=stop_reason_of(ans),
    )
    new_conversation_id = conversation_id or str(row_id)
    followups_left = max(0, MAX_FOLLOWUPS - prior_questions)

    return {
        "answer": ans.text,
        "conversation_id": new_conversation_id,
        "followups_left": followups_left,
    }
