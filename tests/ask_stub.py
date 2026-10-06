"""A stand-in for `linklib.agent.answer_question` that returns a successful
answer without any API call. A missing ANTHROPIC_API_KEY is a failed turn now
(it used to read as answer text), so tests that need a recorded answer patch this in."""
from linklib.agent import Answer, web_scope_for


def ok_answer_question(lib, question, model="", effort="standard", use_library=True,
                       use_feed=True, use_web=False, opml_path=None, history=None):
    return Answer(text="stub answer", model="stub-model", web_scope=web_scope_for(use_feed, use_web))
