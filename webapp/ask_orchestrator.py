"""FP&A Buddy's shared orchestration — cap check, conversation-history
rebuild, the `answer_question()` call, and `ask_questions` recording — used
by both `POST /ask` (webapp/app.py) and the `ask_fpa_buddy` MCP tool
(webapp/mcp_qa.py, MCP Phase 5).

Extracted from `POST /ask` verbatim (same order of operations, same return
shapes, same cap/history/recording logic) rather than rewritten — the two
callers need byte-identical behavior, and `POST /ask`'s own test suite
(tests/test_ask_conversations.py) is the proof: it exercises this code
through the HTTP route with zero changes to its own assertions, so a
regression here would show up as a route-level test failure, not just an
MCP one.

Deliberately decoupled from FastAPI: `run_ask()` takes an already-resolved
`user_id` (an `int | None`), never a `Request` or a session — the caller
(the HTTP route, using its cookie-session lookup; the MCP tool, using its
verified bearer-token lookup) is responsible for resolving identity however
its own transport works. This is what makes the same function callable
from both places: Phase 0's investigation found `answer_question()` itself
already had no FastAPI coupling, but the cap/history/recording orchestration
*around* it was inline in the route and needed extracting before an
in-process, non-HTTP caller could reach it.

Two custom exceptions carry the "unknown"/"foreign" conversation cases
instead of raising `HTTPException` directly — a library-layer module has
no business raising a FastAPI-specific error, and an MCP tool wants to
turn the same condition into a `ToolError`, not a 404/403. Each caller
translates these into whatever its own transport's error convention is.

The "capped" condition is NOT one of these exceptions — matching `/ask`'s
own long-standing design, a capped turn is a normal (HTTP 200) outcome,
not an error, and `run_ask()` returns the same `{"capped": True, ...}`
dict shape a successful call's caller already knows how to handle. The
MCP tool returns this dict verbatim as its JSON result (Step 0 item 3 /
Brian's approval), rather than raising a `ToolError` for it.
"""
from __future__ import annotations

from linklib.db import Library
from linklib.stop_reason import stop_reason_of


class UnknownConversationError(Exception):
    """`conversation_id` doesn't match any recorded ask_questions rows."""


class ForbiddenConversationError(Exception):
    """`conversation_id` belongs to a different user than the caller."""


def run_ask(
    lib: Library,
    user_id: int | None,
    question: str,
    *,
    model: str = "",
    effort: str = "standard",
    use_library: bool = True,
    use_feed: bool = True,
    use_web: bool = False,
    conversation_id: str = "",
    opml_path: str | None = None,
    is_private: bool = False,
) -> dict:
    """Answer one FP&A Buddy turn, exactly as `POST /ask` does.

    `question` must already be stripped/validated non-empty — that's a
    payload-shape concern the caller owns (an HTTP 400 for the route, a
    `ToolError` for the MCP tool), not this function's job.

    Raises `UnknownConversationError`/`ForbiddenConversationError` for a
    bad `conversation_id`. Otherwise always returns a dict — either the
    `{"capped": True, ...}` shape (dollar cap or follow-up-count cap hit)
    or the full answer shape (answer/citations/sources/feed_sources/
    web_sources/followups_left/conversation_id/turn_id/usage) — matching
    `POST /ask`'s JSON response contract field-for-field.
    """
    from linklib.agent import answer_question, MAX_FOLLOWUPS

    # Follow-up turn: rebuild history from the conversation's recorded rows
    # (the server-side source of truth) — never from anything the caller
    # supplies directly, so a fabricated history is impossible. Token-only
    # / break-glass access (user_id None) never has recorded turns, so it
    # can't continue any conversation — each of its questions is one-shot.
    history: list[dict] = []
    prior_questions = 0
    if conversation_id:
        turns = lib.list_conversation_turns(conversation_id)
        if not turns:
            raise UnknownConversationError(conversation_id)
        if user_id is None or turns[0]["user_id"] != user_id:
            raise ForbiddenConversationError(conversation_id)
        # The follow-up cap counts recorded rows, never client-supplied
        # turns (invisible cost guard) — a capped conversation never
        # reaches the API.
        prior_questions = len(turns)
        # A follow-up always inherits the conversation's privacy; only the
        # first turn takes the caller's choice.
        is_private = bool(turns[0].get("is_private"))
        if prior_questions >= 1 + MAX_FOLLOWUPS:
            return {
                "capped": True,
                "answer": "We've reached the limit for this conversation. "
                          "Start a new conversation to keep going.",
                "sources": [], "feed_sources": [], "web_sources": [],
            }
        for t in turns:
            history.append({"role": "user", "content": t["question"]})
            history.append({"role": "assistant", "content": t["answer"]})

    # Dollar-based rate limit — real spend this calendar month vs. the
    # user's effective cap (per-user override, else the global default).
    # Skipped for token-only access and the break-glass admin login with
    # no matching `users` row (no user_id to attribute cost to), matching
    # today's unrestricted behavior for those cases.
    if user_id is not None:
        cap = lib.get_effective_ask_cap(user_id)
        spent = lib.ask_cost_this_month(user_id)
        if spent >= cap:
            return {
                "capped": True,
                "answer": (f"You've used ${spent:.2f} of your ${cap:.2f} FP&A Buddy budget "
                           "for this month. It resets at the start of next month."),
                "sources": [], "feed_sources": [], "web_sources": [],
            }

    ans = answer_question(
        lib, question,
        model=model,
        effort=effort,
        use_library=use_library,
        use_feed=use_feed,
        use_web=use_web,
        opml_path=opml_path if (use_feed or use_web) else None,
        history=history,
    )

    # None for token-only / break-glass access: nothing was recorded, so
    # there is no conversation to continue (the guards above already
    # reject any conversation_id those callers send).
    new_conversation_id = None
    usage_line = None
    turn_id = None
    if user_id is not None:
        row_id = lib.record_ask_question(
            # ans.model is the resolved canonical model actually used —
            # not the raw request field, which can be an alias or blank.
            user_id, question, ans.text, ans.model, effort,
            use_library, use_feed, use_web,
            conversation_id=conversation_id, turn_index=prior_questions,
            input_tokens=ans.input_tokens, output_tokens=ans.output_tokens,
            cache_creation_tokens=ans.cache_creation_tokens,
            cache_read_tokens=ans.cache_read_tokens,
            # ans.cost_usd is the turn total (answer + follow-up query
            # rewrite + query-time embedding for hybrid retrieval + any Exa
            # web-search call), so the monthly-cap SUM sees all of it.
            cost_usd=ans.cost_usd,
            rewrite_input_tokens=ans.rewrite_input_tokens,
            rewrite_output_tokens=ans.rewrite_output_tokens,
            rewrite_cost_usd=ans.rewrite_cost_usd,
            embed_input_tokens=ans.embed_input_tokens,
            embed_cost_usd=ans.embed_cost_usd,
            # Exa cost tracking, pre-dashboard foundation (2026-09):
            # previously computed on Answer but never passed through here —
            # ans.exa_cost_usd is already inside ans.cost_usd above, same as
            # embed_cost_usd/rewrite_cost_usd; this just makes its own share
            # visible on the row.
            exa_result_count=ans.exa_result_count,
            exa_cost_usd=ans.exa_cost_usd,
            # Persisted snapshot of what this answer actually cited, so a
            # later feedback flag stays inspectable with its sources.
            citations=ans.citations,
            stop_reason=stop_reason_of(ans),
            is_private=is_private,
            web_scope=ans.web_scope,
        )
        new_conversation_id = conversation_id or str(row_id)
        turn_id = row_id
        cap = lib.get_effective_ask_cap(user_id)
        spent = lib.ask_cost_this_month(user_id)
        usage_line = {"spent": round(spent, 2), "cap": round(cap, 2)}

    followups_left = max(0, MAX_FOLLOWUPS - prior_questions)
    return {
        "answer": ans.text,
        # API-verified citations only — what the answer's [n] markers map
        # to. own_content (when present) rides through unmodified.
        "citations": ans.citations,
        "sources":      [{"title": s["title"], "url": s["url"]} for s in ans.sources],
        "feed_sources": [{"title": s["title"], "url": s["url"]} for s in ans.feed_sources],
        "web_sources":  [{"title": s["title"], "url": s["url"]} for s in ans.web_sources],
        "followups_left": followups_left,
        "conversation_id": new_conversation_id,
        # The recorded ask_questions row id for this turn — what the
        # feedback controls rate. None when the turn wasn't recorded
        # (token-only or break-glass access with no users row).
        "turn_id": turn_id,
        "usage": usage_line,
        "is_private": bool(is_private),
    }
