"""What every router factory needs: the CRM session dependency and the service deps."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core.auth import Principal, Role, require_human_mfa, require_role
from kx.core.db import Databases
from kx.core.settings import LogicalDb
from kx.crm.deps import CrmDeps

STAFF = (
    Role.SALES,
    Role.MANAGER,
    Role.ADMIN,
)  # Principal.has() lets the MD through wherever MANAGER is listed
MANAGERS = (Role.MANAGER,)

# review §3.1: the role aliases every router used to define for itself (one callable each: FastAPI also runs a
# shared dependency once per request). Module-specific aliases (SettingsReader, PriceEditor ...) stay put.
Staff = Annotated[Principal, Depends(require_role(*STAFF))]
Manager = Annotated[Principal, Depends(require_role(*MANAGERS))]
HumanManager = Annotated[Principal, Depends(require_human_mfa(Role.MANAGER))]
HumanStaff = Annotated[Principal, Depends(require_human_mfa(Role.SALES, Role.MANAGER))]
MdOnly = Annotated[Principal, Depends(require_role(Role.MD))]


@dataclass(frozen=True, slots=True)
class ApiContext:
    databases: Databases
    deps: CrmDeps
    session: Callable[[], AsyncIterator[AsyncSession]] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session", self.databases.dependency(LogicalDb.CRM))
