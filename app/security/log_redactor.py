"""
Log Secret Redaction Filter (`app/security/log_redactor.py`).

Prevents cleartext API keys, OAuth client secrets, and scraper credentials from leaking
into console or file logs (including `httpx` / `httpcore` URL objects in `LogRecord.args`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

_SECRET_NAMES = (
    r"devpassword|sspassword|devid|ssid|api_key|apikey|"
    r"client_secret|access_token|password|pass"
)

_QUERY_SECRET_RE = re.compile(
    rf"(?i)(?<![a-z0-9_])({_SECRET_NAMES})=([^&\s\"'<>]+)"
)
_JSON_DQ_SECRET_RE = re.compile(
    rf'(?i)"({_SECRET_NAMES})"\s*:\s*"([^"]*)"'
)
_JSON_SQ_SECRET_RE = re.compile(
    rf"(?i)'({_SECRET_NAMES})'\s*:\s*'([^']*)'"
)
_BEARER_TOKEN_RE = re.compile(
    r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]{8,})"
)


def redact_secrets_in_text(text: str) -> str:
    """Scrub query-string, JSON/dict, and Bearer token secrets from a string."""
    if not text:
        return text
    scrubbed = _QUERY_SECRET_RE.sub(r"\1=***", text)
    scrubbed = _JSON_DQ_SECRET_RE.sub(r'"\1": "***"', scrubbed)
    scrubbed = _JSON_SQ_SECRET_RE.sub(r"'\1': '***'", scrubbed)
    scrubbed = _BEARER_TOKEN_RE.sub(r"\1 ***", scrubbed)
    return scrubbed


def _scrub_arg(value: Any) -> Any:
    """Scrub string or URL-like `LogRecord` arguments while preserving numeric format types."""
    if isinstance(value, (int, float, bool, type(None))):
        return value
    if isinstance(value, str):
        return redact_secrets_in_text(value)
    if isinstance(value, tuple):
        return tuple(_scrub_arg(v) for v in value)
    if isinstance(value, list):
        return [_scrub_arg(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_arg(v) for k, v in value.items()}
    try:
        rendered = str(value)
    except Exception:
        return value
    redacted = redact_secrets_in_text(rendered)
    return redacted if redacted != rendered else value


class SecretRedactingLogFilter(logging.Filter):
    """Logging filter scrubbing credential parameters across `record.msg` and `record.args`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets_in_text(record.msg)
        elif record.msg is not None:
            record.msg = _scrub_arg(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_scrub_arg(a) for a in record.args)
            else:
                record.args = _scrub_arg(record.args)
        return True


_GLOBAL_SECRET_FILTER = SecretRedactingLogFilter()


def _ensure_filter_attached(target: Any) -> None:
    filters = getattr(target, "filters", None)
    if isinstance(filters, list) and not any(
        isinstance(f, SecretRedactingLogFilter) for f in filters
    ):
        target.addFilter(_GLOBAL_SECRET_FILTER)


def install_secret_log_redaction() -> SecretRedactingLogFilter:
    """
    Attach `SecretRedactingLogFilter` to root logger/handlers and clamp `httpx`/`httpcore`
    loggers to `WARNING` so full request URLs with query credentials are never emitted at INFO.
    """
    root_logger = logging.getLogger()
    _ensure_filter_attached(root_logger)
    for handler in list(root_logger.handlers):
        _ensure_filter_attached(handler)

    for noisy_http_logger_name in ("httpx", "httpcore"):
        http_lg = logging.getLogger(noisy_http_logger_name)
        http_lg.setLevel(logging.WARNING)
        _ensure_filter_attached(http_lg)
        for handler in list(http_lg.handlers):
            _ensure_filter_attached(handler)

    return _GLOBAL_SECRET_FILTER
