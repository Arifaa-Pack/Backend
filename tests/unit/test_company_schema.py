"""19 Nov live re-test (tester Postman run): a company with a customer type outside the database's CHECK list
answered 500. The API accepts exactly the DDL's values and refuses others with 422."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from kx.crm.records.router import CompanyIn, CustomerType

DDL = Path(__file__).resolve().parents[2] / "db" / "ddl" / "01_crm.sql"


def test_customer_types_are_the_ddl_check_values() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    match = re.search(r"customer_type\s+text CHECK \(customer_type IN\s*\(([^)]*)\)\)", ddl)
    assert match is not None
    allowed = {v.strip().strip("'") for v in match.group(1).split(",")}
    assert set(typing.get_args(CustomerType)) == allowed


def test_an_unknown_customer_type_is_refused_before_the_database() -> None:
    assert CompanyIn(name="Acme", customer_type="restaurant_chain").customer_type == "restaurant_chain"
    assert CompanyIn(name="Acme").customer_type is None
    with pytest.raises(ValidationError):
        CompanyIn(name="Acme", customer_type="restaurant")
