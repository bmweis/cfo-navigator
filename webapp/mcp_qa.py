"""MCP server, Phase 5: FP&A Buddy & Matchmaker proxy tools.

Two tools — `ask_fpa_buddy` and `ask_matchmaker` — registered onto the same
FastMCP instance every other /mcp module registers onto (mounted at `/mcp`
inside webapp/app.py). Kept in its own module, same reasoning as
mcp_toolbox.py/mcp_library.py: this is pure Q&A-pipeline domain content,
reusing mcp_server.py's auth/host-security plumbing rather than duplicating
it.

Why these call the orchestration helpers in-process instead of the HTTP
routes
-----------------------------------------------------------------------
The original MCP Phase 0 investigation (and this phase's own Step 0 report,
see CLAUDE.md's MCP pointer bullet) found `/ask`'s auth (`_require_member`)
and the matchmaker routes' user_id resolution (`_current_user_id`) both
work ONLY from a signed cookie session or the flat `X-Save-Token` header —
neither has any notion of an MCP bearer token. An HTTP self-call to `/ask`
or the matchmaker endpoints would therefore run as `user_id=None`
(token-only / anonymous behavior): no dollar cap, no `ask_questions`/
`matchmaker_questions` row, no conversation continuity — silently
unmetered and unaudited, the opposite of what an MCP proxy tool should do.

So these tools call `webapp.ask_orchestrator.run_ask()` /
`webapp.matchmaker_orchestrator.run_matchmaker()` directly, in-process,
passing the MCP-resolved `user_id` (from `verify_api_token` via
`require_caller`) explicitly — the exact same orchestration `POST /ask`
and the two `.../find/chat` routes now call themselves (extracted from
those routes verbatim as part of this phase, see those two orchestrator
modules' own docstrings and CLAUDE.md's MCP Phase 5 pointer bullet for the
behavior-identical-refactor discipline that extraction was held to). Caps,
history, and conversation continuity all apply under the caller's real
identity, exactly as they would on the web.

Auth model — a deliberate departure from BOTH existing tiers
--------------------------------------------------------------
Unlike mcp_server.py's three introspection tools (admin-role only) or
mcp_toolbox.py's/mcp_library.py's existing six-plus-four tools (Toolbox:
any valid token, any role, mirroring fully public pages; Library/Feed:
admin-role only, mirroring an admin-only `/read`), these two tools use
`require_caller` (any valid, active token, no role restriction) — same
mechanism as mcp_toolbox.py, reused rather than duplicated (Step 0 item 5:
no `require_member`-equivalent exists or is needed in the MCP layer; any
valid token already IS "a real, active user," which is the bar `/ask`'s
own `_require_member` sets for a signed-in member). This is a deliberate
choice, confirmed with Brian before building: `/ask` itself only requires
any signed-in member, not an admin, and gating these two tools to
admin-only would lock out a future, more limited non-admin MCP tier Brian
has said may exist later. A caller with no valid token at all still gets
nothing, per the standing "non-logged-in users never get MCP access" rule
— this is only about whether the token additionally must be `role=="admin"`.

Capped result — a normal JSON dict, not a ToolError
------------------------------------------------------
Matching `/ask`'s own long-standing design (Step 0 item 2/3, approved):
being over budget is a normal HTTP-200 outcome on the web, never an error
response — so both tools return the orchestrators' `{"capped": true, ...}`
dict verbatim as their JSON result. Raising a `ToolError` for this would
misrepresent an expected, non-exceptional state as a failure.

Two kinds, one tool for the matchmaker
-----------------------------------------
`ask_matchmaker(kind, ...)` is one tool with a `kind` parameter
(`"tools"`|`"communities"`), not two separate tools — Step 0 item 6,
approved. `linklib.matchmaker._answer()` is already one shared function
differentiated only by an internal `kind` string (`"software"`/
`"community"`); mirroring that with one MCP tool matches the code it wraps
more closely than inventing two near-duplicate tool definitions for what's
really one enum value. This is a deliberate departure from Phase 3's own
"separate tools per entity type" precedent (`search_software`/
`search_communities`, etc.) — that precedent applies where the underlying
implementation and return shape genuinely differ per entity type; here
they don't. `"tools"`/`"communities"` (matching this tool's own
public vocabulary, and Phase 3's `search_software`/`search_communities`
naming) map internally to `linklib.matchmaker`'s own `"software"`/
`"community"` kind strings.

Conversation continuity: a real, disclosed asymmetry between the two tools
------------------------------------------------------------------------
`ask_fpa_buddy`'s conversation ownership is keyed purely on `user_id`
(`ask_questions.user_id` — see `run_ask`), so a conversation started on
the web and continued via this MCP tool (or vice versa) works seamlessly
for the same signed-in user — no session concept involved at all.

`ask_matchmaker`'s ownership check requires BOTH `session_id` and
`user_id` to match (`matchmaker_questions.session_id`, the `cfo_visitor`
cookie on the web) — pre-existing behavior, unrelated to MCP, that already
means the same signed-in user on two different browsers/devices can't
resume one matchmaker conversation from the other. Since an MCP caller has
no cookie, this tool synthesizes a stable per-user session key
(`f"mcp:user:{user_id}"`) so a caller CAN resume a conversation they
started via MCP, across separate tool calls — but a matchmaker
conversation started on the web can't be resumed via MCP (and vice versa),
same as it can't between two different browsers today. This is a genuine,
disclosed limitation inherited from the existing session-keyed design, not
a new one introduced by this phase — flagged here and in each tool's own
docstring rather than silently accepted.
"""
from __future__ import annotations

from typing import Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from linklib.db import Library

from webapp.ask_orchestrator import (
    ForbiddenConversationError as _AskForbiddenConversationError,
    UnknownConversationError as _AskUnknownConversationError,
    run_ask,
)
from webapp.matchmaker_orchestrator import (
    ForbiddenConversationError as _MatchmakerForbiddenConversationError,
    UnknownConversationError as _MatchmakerUnknownConversationError,
    run_matchmaker,
)
from webapp.mcp_server import require_caller

_VALID_EFFORTS = ("quick", "standard", "deep")
_VALID_SOURCES = ("library", "feed", "web")
_DEFAULT_SOURCES = ("library", "web")

# ask_matchmaker's public `kind` vocabulary (matching Phase 3's
# search_software/search_communities naming) mapped to linklib.matchmaker's
# own internal kind strings ("software"/"community").
_MATCHMAKER_KINDS = {"tools": "software", "communities": "community"}


def register_qa_tools(mcp: FastMCP, lib_factory: Callable[[], Library], opml_path: str) -> None:
    """Register the two Q&A proxy tools onto an already-built FastMCP
    instance (webapp.app calls this right after `register_toolbox_tools`/
    `register_library_tools`). `opml_path` is passed in explicitly, same
    convention as `mcp_library.register_library_tools` — `run_ask` needs it
    for the web/feed retrieval tiers."""

    @mcp.tool()
    async def ask_fpa_buddy(ctx: Context, question: str, conversation_id: str = "",
                             sources: list[str] | None = None,
                             effort: str = "standard",
                             open_web: bool = False) -> dict:
        """Ask FP&A Buddy a question — the same pipeline `/tools/fpa-buddy`
        uses (hybrid library retrieval + current feed + optional open web
        search, a cited answer), run in-process under the calling token's own identity so
        the caller's real dollar cap, conversation history, and
        `ask_questions` audit row all apply exactly as they would on the
        web (see this module's own docstring for why this can't be a
        simple HTTP self-call to `/ask`).

        `sources` is a subset of `["library", "feed", "web"]` (default
        `["library", "web"]`) — which retrieval tiers to use. `"feed"` and
        `"web"` both mean the site's Current feed: recent RSS items plus a
        web search restricted to the sites Brian trusts. Neither is ever an
        unrestricted search. To search the whole web, set `open_web=true`
        (default `false`); that is the only way to get an unrestricted
        search, and it replaces the trusted-only search with one open call.
        `effort` is `"quick"`, `"standard"`
        (default), or `"deep"` — the same three tiers `/tools/fpa-buddy`'s
        UI offers, trading answer depth for cost/latency.

        Pass `conversation_id` (from a prior call's response) to continue
        that conversation as a follow-up — history is rebuilt server-side
        from the recorded turns, never trusted from the caller. A
        conversation belonging to a different user, or one that's reached
        its follow-up limit, is refused (a conversation_id from someone
        else raises a tool error; a follow-up-limit or dollar-cap hit
        returns a normal `{"capped": true, "answer": "..."}` result, not
        an error — being over budget is an expected outcome, not a
        failure).

        Returns `{"answer", "citations", "sources", "feed_sources",
        "web_sources", "followups_left", "conversation_id", "turn_id",
        "usage", "capped"}` — the same shape `/ask`'s own JSON response
        carries. A cited library source that's one of Brian's own
        published pieces carries `"own_content": true` in its citation
        entry, unmodified from what `answer_question()` returns.

        Any valid token, any user role — no admin requirement (matching
        `/ask`'s own "any signed-in member" bar, not this server's
        admin-only tiers)."""
        caller = require_caller(ctx, lib_factory)
        user_id = caller["user_id"]

        question = (question or "").strip()
        if not question:
            raise ToolError("question is required")
        if effort not in _VALID_EFFORTS:
            raise ToolError(f"invalid effort {effort!r}: expected one of {_VALID_EFFORTS}")
        src = list(sources) if sources else list(_DEFAULT_SOURCES)
        bad = [s for s in src if s not in _VALID_SOURCES]
        if bad:
            raise ToolError(f"invalid sources {bad!r}: expected a subset of {_VALID_SOURCES}")

        lib = lib_factory()
        try:
            try:
                return run_ask(
                    lib, user_id, question,
                    effort=effort,
                    use_library="library" in src,
                    # "feed" and "web" both mean Current feed (RSS plus the
                    # trusted-domain search); only open_web=True is unrestricted.
                    use_feed=("feed" in src) or ("web" in src),
                    use_web=bool(open_web),
                    conversation_id=(conversation_id or "").strip(),
                    opml_path=opml_path,
                )
            except _AskUnknownConversationError:
                raise ToolError(f"unknown conversation_id: {conversation_id!r}")
            except _AskForbiddenConversationError:
                raise ToolError("that conversation belongs to a different user")
        finally:
            lib.close()

    @mcp.tool()
    async def ask_matchmaker(ctx: Context, kind: str, question: str,
                              conversation_id: str = "") -> dict:
        """Ask the CFO Toolbox Chat Matchmaker a question — the same
        pipeline `/tools/software/find` (kind="tools") and
        `/tools/communities/find` (kind="communities") use, run in-process
        under the calling token's own identity (see this module's own
        docstring for why, and for conversation-continuity behavior that's
        deliberately keyed by session as well as user — a conversation
        started via this tool can be resumed via this tool, but not from a
        web session or a different tool call chain).

        `kind` must be `"tools"` (Software matchmaker) or `"communities"`
        (Communities matchmaker) — the two share one conversational
        pipeline and one monthly dollar budget (spend in one counts
        against the same cap as the other, matching the web routes'
        design), differentiated only by which catalog it searches.

        Pass `conversation_id` (from a prior call's response, made with
        the same `kind`) to continue that conversation as a follow-up.
        Unlike `ask_fpa_buddy`, this has no citations mechanism (see
        `linklib.matchmaker`'s own module docstring) — the result never
        carries a `citations` key.

        Returns `{"answer", "conversation_id", "followups_left",
        "capped"}` — the same shape the two `.../find/chat` web routes'
        JSON response carries. A follow-up-limit or dollar-cap hit returns
        a normal `{"capped": true, "answer": "..."}` result, not an error.

        Any valid token, any user role — no admin requirement, same as
        `ask_fpa_buddy`."""
        caller = require_caller(ctx, lib_factory)
        user_id = caller["user_id"]

        if kind not in _MATCHMAKER_KINDS:
            raise ToolError(f"invalid kind {kind!r}: expected one of {tuple(_MATCHMAKER_KINDS)}")
        question = (question or "").strip()
        if not question:
            raise ToolError("question is required")

        # No cookie to read for an MCP caller — a stable per-user key so a
        # conversation started via this tool can be resumed via this tool
        # (see the module docstring for the resulting, disclosed asymmetry
        # with a web-started conversation).
        session_id = f"mcp:user:{user_id}"

        lib = lib_factory()
        try:
            try:
                return run_matchmaker(
                    lib, _MATCHMAKER_KINDS[kind], user_id, session_id, question,
                    conversation_id=(conversation_id or "").strip(),
                )
            except _MatchmakerUnknownConversationError:
                raise ToolError(f"unknown conversation_id: {conversation_id!r}")
            except _MatchmakerForbiddenConversationError:
                raise ToolError("that conversation belongs to a different user or session")
        finally:
            lib.close()
