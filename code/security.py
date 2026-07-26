"""Secret-safe formatting for network errors and request URLs.

Requests exceptions often include the prepared URL.  That is useful until an
API puts a credential in its query string (PriceCharting uses ``?t=...``) or
path (Telegram uses ``/bot<TOKEN>/...``).  Logging the raw exception would
then persist the credential in scan.log.
"""
from __future__ import annotations

import re


_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:t|token|api[_-]?key|apikey|access[_-]?token|"
    r"client[_-]?secret|bot[_-]?token|key)=)([^&#\s)]+)"
)
_TELEGRAM_TOKEN_RE = re.compile(r"(?i)(api\.telegram\.org/bot)[^/\s?]+")
_AUTH_RE = re.compile(r"(?i)\b(Authorization\s*:\s*(?:Bearer|Basic)\s+)\S+")
_NAMED_SECRET_RE = re.compile(
    r"""(?ix)
    (["']?(?:api[_-]?key|access[_-]?token|bot[_-]?token|
       client[_-]?secret|password)["']?\s*[:=]\s*["']?)
    ([^"',\s}]+)
    """
)


def redact_text(value: object) -> str:
    """Return log-safe text with common credential shapes removed."""
    text = str(value)
    text = _QUERY_SECRET_RE.sub(r"\1<redacted>", text)
    text = _TELEGRAM_TOKEN_RE.sub(r"\1<redacted>", text)
    text = _AUTH_RE.sub(r"\1<redacted>", text)
    return _NAMED_SECRET_RE.sub(r"\1<redacted>", text)


def redact_url(value: object) -> str:
    """Semantic alias documenting that a URL may contain credentials."""
    return redact_text(value)
