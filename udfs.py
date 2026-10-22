"""UDFs for the bakery order ledger: pure Python, no table imports."""

from __future__ import annotations

import datetime

import pixeltable as pxt

# An order picked up less than this many hours after it was placed is a rush order.
RUSH_HOURS = 24.0
# Ovens run in 2-hour slots; an order must come out of the oven 1 hour before pickup.
SLOT_HOURS = 2


@pxt.udf
def lead_hours(placed_at: datetime.datetime, pickup_at: datetime.datetime) -> float:
    """Hours between placing the order and picking it up."""
    return round((pickup_at - placed_at).total_seconds() / 3600.0, 2)


@pxt.udf
def oven_slot(pickup_hour: int) -> str:
    """The 2-hour oven slot the order has to be baked in, e.g. '06:00-08:00'.

    Takes the local pickup hour (from `pixeltable.functions.timestamp.hour`) rather than the timestamp,
    so the slot is computed in the configured time zone on insert and on update alike.
    """
    ready = pickup_hour - 1
    start = max((ready // SLOT_HOURS) * SLOT_HOURS - SLOT_HOURS, 0)
    return f'{start:02d}:00-{start + SLOT_HOURS:02d}:00'


@pxt.udf
def price_band(total: float) -> str:
    """Rough order size for the counter staff."""
    if total >= 100:
        return 'large'
    if total >= 30:
        return 'medium'
    return 'small'
