"""Measure how often a model answer is cut off by max_tokens (2026-10).

Measurement only. Nothing here changes a max_tokens value, retries, or shows
anything to a visitor. Two small helpers:

- `stop_reason_of(resp)`: the API's `stop_reason` as a plain string ("" when
  absent), for the two answer paths that persist it (`ask_questions.stop_reason`,
  `matchmaker_questions.stop_reason`).
- `warn_if_max_tokens(resp, call_site)`: one WARNING log line per max_tokens
  stop, for the enrichment/generation call sites that have no table of their
  own. Count them in the Railway logs (or a `railway ssh` session's output) by
  searching for "stop_reason=max_tokens".

Never raises: a response object without a usable stop_reason is just "".
"""
from __future__ import annotations

import logging

_logger = logging.getLogger("linklib.stop_reason")


def stop_reason_of(resp) -> str:
    value = getattr(resp, "stop_reason", "")
    return value if isinstance(value, str) else ""


def warn_if_max_tokens(resp, call_site: str) -> None:
    if stop_reason_of(resp) == "max_tokens":
        _logger.warning("stop_reason=max_tokens call_site=%s", call_site)
