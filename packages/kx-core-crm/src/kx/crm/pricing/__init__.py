"""Pricing. The maths (`engine`, `price_list`, `types`) is pure: no I/O, no ORM; callers pass value types.

Around it (ADR-0001 slice): `service` assembles the pricing inputs from CRM data, `price_list_service`
maintains the price list (§10, D6), `fx` the daily FX rates (D38), `proposals` the raw-material price
proposals (T12).
"""
