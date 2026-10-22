"""Bakery order ledger with a time-travel audit trail, built with Pixeltable.

Orders change all day: customers move pickup times, staff fix prices, orders get cancelled. Pixeltable
versions every insert, update, delete and schema change, so the ledger is its own audit trail:
`audit.py` reads any past version (`pxt.get_table('bakery/orders:N')`), diffs two versions by
primary key, freezes end-of-day snapshots and rolls back a bad bulk edit with `revert()`.
Timestamp functions (`pixeltable.functions.timestamp`) derive the pickup day, hour and oven slot.

Layout: UDFs live in udfs.py; this file holds table models and @pxt.query functions.

    pxt schema update app.py bakery
    python audit.py seed
    python audit.py day        # a day of edits, each one a new table version
    python audit.py history
"""

from __future__ import annotations

import pixeltable as pxt
import pixeltable.functions as pxtf

from udfs import RUSH_HOURS, lead_hours, oven_slot, price_band

TableModel = pxt.model_base()


class Orders(TableModel, name='orders'):
    """One customer order. Every change to it is a new table version."""

    order_id = pxt.Column(type=pxt.String, primary_key=True)
    customer: pxt.String
    item: pxt.String
    qty: pxt.Int
    unit_price: pxt.Float
    placed_at: pxt.Timestamp
    pickup_at: pxt.Timestamp
    status: pxt.String
    note: pxt.String | None

    total = qty * unit_price
    band = price_band(total)
    pickup_day = pxtf.timestamp.strftime(pickup_at, '%a %Y-%m-%d')
    pickup_hour = pxtf.timestamp.hour(pickup_at)
    pickup_weekday = pxtf.timestamp.isoweekday(pickup_at)
    lead = lead_hours(placed_at, pickup_at)
    slot = oven_slot(pickup_hour)

    __indexes__ = [pxt.BtreeIndex(status), pxt.BtreeIndex(pickup_at)]


class ChangeLog(TableModel, name='change_log'):
    """Who did what, keyed to the Orders version the change produced."""

    orders_version: pxt.Int
    actor: pxt.String
    action: pxt.String
    reason: pxt.String | None
    at: pxt.Timestamp

    day = pxtf.timestamp.strftime(at, '%Y-%m-%d')


class RushOrders(TableModel, name='rush_orders', base=Orders.where(Orders.lead < RUSH_HOURS)):
    """Orders picked up less than a day after they were placed."""

    rush_slot = Orders.slot


@pxt.query
def orders_for_pickup_day(day: str):
    """Open orders for one pickup day (format 'Thu 2026-10-22'), earliest first."""
    return (
        Orders.where((Orders.pickup_day == day) & (Orders.status != 'cancelled'))
        .order_by(Orders.pickup_at)
        .select(Orders.order_id, Orders.customer, Orders.item, Orders.qty, Orders.slot, Orders.status)
    )


@pxt.query
def changes_by(actor: str):
    """Every logged change by one staff member."""
    return ChangeLog.where(ChangeLog.actor == actor).order_by(ChangeLog.at).select(
        ChangeLog.orders_version, ChangeLog.action, ChangeLog.reason, ChangeLog.at
    )
