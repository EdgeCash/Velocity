"""Redact credentials echoed back inside a provider payload.

Written after BettingPros echoed the full request URL — partner key and user id
included — in ``_pagination.self``, and the collector banked it verbatim into
Actions artifacts that outlive the run by a month (docs/DATA_PROVIDERS.md).

This lives in its own module rather than beside one provider's client because
the hazard is not BettingPros': it belongs to **any** API whose credential
travels in the URL, since that is the string an API echoes back. A sweep of the
clients found exactly two shapes worth covering — BettingPros' ``key=``/``user=``
and The Odds API's ``apiKey=`` — and the scrubber missing the second while
looking like general protection is worse than no scrubber at all, because a
collector that calls it reads as safe. Hence :data:`SECRET_QUERY_PARAMS` is
matched case-insensitively and spelled out in every casing style a provider is
likely to use.

Scrub at the **banking boundary** — the moment a payload is written somewhere
that outlives the process. Normalizers are unaffected either way: the redaction
only ever rewrites a URL-shaped string, never structure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Credential-carrying query parameter names, matched case-insensitively — so
# ``apikey`` covers The Odds API's ``apiKey`` and ``APIKEY`` alike. Spelling
# variants are listed separately because the match is anchored to the whole
# parameter name: ``api_key`` does NOT cover ``apiKey``, which is precisely the
# gap this module was created to close.
SECRET_QUERY_PARAMS = (
    "key",
    "user",
    "auth",
    "api_key",
    "apikey",
    "api-key",
    "access_token",
    "token",
    "secret",
    "password",
)
_SECRET_RE = re.compile(
    r"([?&](?:" + "|".join(SECRET_QUERY_PARAMS) + r")=)[^&\s\"']+",
    re.IGNORECASE,
)


def scrub_secrets(value: Any) -> Any:
    """Recursively redact credential query parameters from a payload.

    Walks dicts, lists and strings, rewriting ``key=abc`` to ``key=REDACTED``
    wherever it appears in a URL-shaped string. Structure and every other value
    are preserved exactly, so a scrubbed payload still normalizes identically —
    the redaction touches only the echoed request URL.
    """
    if isinstance(value, str):
        return _SECRET_RE.sub(r"\1REDACTED", value)
    if isinstance(value, Mapping):
        return {k: scrub_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_secrets(v) for v in value]
    return value
