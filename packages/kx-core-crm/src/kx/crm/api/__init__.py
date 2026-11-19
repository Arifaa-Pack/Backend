"""Staff CRM HTTP API (thin routers over kx.crm.services).

Wire in the app:
    app.include_router(build_router(databases, deps), prefix="/v1")
    install_problem_handlers(app)          # kx.core.errors -> RFC 9457 problem+json
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kx.core.db import Databases
from kx.crm.api import admin, assignment, change_requests, deal_actions, deals, quotes, screens
from kx.crm.api.context import ApiContext
from kx.crm.auth import router as auth
from kx.crm.auth import users_router as users
from kx.crm.auth.session_guard import SessionGuard
from kx.crm.catalogue import router as catalogue_workbook
from kx.crm.company import router as company
from kx.crm.deps import CrmDeps
from kx.crm.operations import router as operations
from kx.crm.records import router as records
from kx.crm.reports import router as reports
from kx.crm.texts import router as texts


def build_router(databases: Databases, deps: CrmDeps) -> APIRouter:
    ctx = ApiContext(databases, deps)
    # L1 / L7: every staff route checks the token's session is alive (and the one-time password changed)
    root = APIRouter(dependencies=[Depends(SessionGuard(databases, deps.now).dependency())])
    for module in (
        auth,
        users,
        deals,
        deal_actions,
        assignment,
        quotes,
        change_requests,
        screens,
        catalogue_workbook,
        admin,
        company,
        operations,
        records,
        reports,
        texts,
    ):
        root.include_router(module.router(ctx))
    return root


__all__ = ["ApiContext", "build_router"]
