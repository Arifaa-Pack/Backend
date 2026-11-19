"""Public surface of the CRM package (ADR-0001, rule 3): the names an app (`arifaa_pack`) imports.

Everything else is internal to its feature slice (`kx/crm/<slice>/`). Other packages never import `kx.crm`
at all: they talk to it through `kx.core.contracts` and events, which `CrmGatewayImpl` and `jobs` implement.

The names are resolved lazily (PEP 562), so importing one CRM module does not load the whole package.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kx.crm import jobs
    from kx.crm._shared.journal import Actor
    from kx.crm._shared.settings import SettingsService
    from kx.crm.api import build_router
    from kx.crm.auth.service import create_user
    from kx.crm.auth.session_guard import SessionGuard
    from kx.crm.auth.throttle import MemoryThrottle
    from kx.crm.company.service import letterhead as company_letterhead
    from kx.crm.deps import CrmDeps
    from kx.crm.gateway import ApprovalStatusReaderImpl, CrmGatewayImpl, GrantConsumerImpl
    from kx.crm.quotes.pdf import (
        ChromiumEngine,
        Letterhead,
        PdfEngine,
        QuoteRenderer,
        S3PdfUploader,
        WeasyPrintEngine,
    )
    from kx.crm.retention.service import plumbing_rules as retention_rules
    from kx.crm.texts.service import load_edits as load_text_edits
    from kx.crm.texts.service import save_translation as save_text_translation

# public name -> (module, attribute); an empty attribute means the module itself
_EXPORTS: dict[str, tuple[str, str]] = {
    # wiring
    "CrmDeps": ("kx.crm.deps", "CrmDeps"),
    "SettingsService": ("kx.crm._shared.settings", "SettingsService"),
    "CrmGatewayImpl": ("kx.crm.gateway", "CrmGatewayImpl"),
    "ApprovalStatusReaderImpl": ("kx.crm.gateway", "ApprovalStatusReaderImpl"),
    "GrantConsumerImpl": ("kx.crm.gateway", "GrantConsumerImpl"),
    "jobs": ("kx.crm.jobs", ""),
    "retention_rules": ("kx.crm.retention.service", "plumbing_rules"),
    # HTTP
    "build_router": ("kx.crm.api", "build_router"),
    "SessionGuard": ("kx.crm.auth.session_guard", "SessionGuard"),
    "MemoryThrottle": ("kx.crm.auth.throttle", "MemoryThrottle"),
    # quote PDFs
    "PdfEngine": ("kx.crm.quotes.pdf", "PdfEngine"),
    "ChromiumEngine": ("kx.crm.quotes.pdf", "ChromiumEngine"),
    "WeasyPrintEngine": ("kx.crm.quotes.pdf", "WeasyPrintEngine"),
    "QuoteRenderer": ("kx.crm.quotes.pdf", "QuoteRenderer"),
    "S3PdfUploader": ("kx.crm.quotes.pdf", "S3PdfUploader"),
    "Letterhead": ("kx.crm.quotes.pdf", "Letterhead"),
    # CLI and the texts sync
    "Actor": ("kx.crm._shared.journal", "Actor"),
    "create_user": ("kx.crm.auth.service", "create_user"),
    "company_letterhead": ("kx.crm.company.service", "letterhead"),
    "load_text_edits": ("kx.crm.texts.service", "load_edits"),
    "save_text_translation": ("kx.crm.texts.service", "save_translation"),
}

__all__ = [
    "Actor",
    "ApprovalStatusReaderImpl",
    "ChromiumEngine",
    "CrmDeps",
    "CrmGatewayImpl",
    "GrantConsumerImpl",
    "Letterhead",
    "MemoryThrottle",
    "PdfEngine",
    "QuoteRenderer",
    "S3PdfUploader",
    "SessionGuard",
    "SettingsService",
    "WeasyPrintEngine",
    "build_router",
    "company_letterhead",
    "create_user",
    "jobs",
    "load_text_edits",
    "retention_rules",
    "save_text_translation",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value: Any = importlib.import_module(module_name)
    if attr:
        value = getattr(value, attr)
    globals()[name] = value
    return value
