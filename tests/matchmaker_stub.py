"""A stand-in for `linklib.matchmaker._answer` that returns a successful answer
without any API call. A missing ANTHROPIC_API_KEY is a failed turn now (it used
to read as answer text), so tests that need a recorded answer patch this in."""
from linklib.matchmaker import MatchAnswer


def ok_answer(lib, kind, question, history=None):
    return MatchAnswer(text="stub answer", model="stub-model")
