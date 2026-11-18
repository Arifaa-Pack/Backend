"""Structured JSON logging with trace ids and PII masking (phones, emails) before logs leave the process."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(r"(\+?\d{1,3})[\d\s\-]{6,}(\d{2})")
# never phone numbers, but digit runs the phone pattern took for one (found 17 Nov in SigNoz: deal ids and
# dates were half-masked, so a deal could not be looked up from its log lines)
_NOT_PII = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    # ISO date / time with a real year, month and day, so a phone typed 0501-23-4567 is still masked
    r"|(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)


def _mask(part: str) -> str:
    return _PHONE.sub(r"\1*****\2", _EMAIL.sub(r"\1***@\2", part))


def mask_pii(value: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _NOT_PII.finditer(value):
        out += [_mask(value[pos : m.start()]), m.group()]
        pos = m.end()
    out.append(_mask(value[pos:]))
    return "".join(out)


def _mask_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = mask_pii(value)
    return event_dict


def _trace_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    if (tid := trace_id_var.get()) is not None:
        event_dict.setdefault("trace_id", tid)
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _trace_processor,
            _mask_processor,
            # after the mask: an ISO time ("2026-11-17T13:19") looks like a phone number to it (found 17 Nov)
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # tracebacks as data for JSON only: the console renderer formats them itself and crashed on the
            # list (every log.exception killed the request when KX_LOG_JSON=false; found 17 Nov)
            *(
                (structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer())
                if json
                else (structlog.dev.ConsoleRenderer(),)
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
