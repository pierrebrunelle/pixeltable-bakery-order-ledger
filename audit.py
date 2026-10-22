"""Audit CLI for the bakery order ledger: seed, edit, history, time travel, diff, snapshot, revert.

    python audit.py seed                    # 12 orders
    python audit.py day                     # a day of edits, one table version each (+ a bad bulk edit rolled back)
    python audit.py history                 # version timeline of bakery/orders
    python audit.py as-of 2                 # the ledger exactly as it was at version 2
    python audit.py diff 1 6                # what changed between two versions, by order_id
    python audit.py restore ord-0007 4      # bring one order back from version 4 (e.g. after a delete)
    python audit.py snapshot eod-2026-10-22 # freeze an end-of-day copy under bakery_snapshots/
    python audit.py fingerprint --out a.json  # per-version row count + revenue, to check a catalog after an upgrade
    python audit.py fingerprint --against a.json

Every command takes --target (default 'bakery'); use pxt://<org>:<db>/bakery for a cloud database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pixeltable as pxt

FIELDS = ['customer', 'item', 'qty', 'unit_price', 'pickup_at', 'status', 'note']
BASE = dt.datetime(2026, 10, 22, 6, 0)

ITEMS = [('croissant', 3.5), ('sourdough loaf', 8.0), ('birthday cake', 64.0), ('baguette', 3.0),
         ('cinnamon roll', 4.25), ('rye loaf', 7.5)]
CUSTOMERS = ['Ana', 'Ben', 'Chloe', 'Dev', 'Eli', 'Fatima', 'Gus', 'Hana', 'Ivo', 'Jun', 'Kemal', 'Lea']


def orders(target: str):
    return pxt.get_table(f'{target}/orders')


def version_of(t) -> int:
    return t.get_versions()[0]['version']


def log(target: str, actor: str, action: str, reason: str | None = None) -> int:
    v = version_of(orders(target))
    pxt.get_table(f'{target}/change_log').insert(
        [{'orders_version': v, 'actor': actor, 'action': action, 'reason': reason, 'at': dt.datetime.now()}]
    )
    print(f'v{v:<3} {actor:<6} {action}' + (f'  ({reason})' if reason else ''))
    return v


def cmd_seed(a) -> None:
    rows = []
    for i, cust in enumerate(CUSTOMERS):
        item, price = ITEMS[i % len(ITEMS)]
        placed = BASE - dt.timedelta(hours=6 + 7 * i)
        pickup = BASE + dt.timedelta(hours=1 + (i % 9), minutes=15 * (i % 4))
        rows.append({'order_id': f'ord-{i + 1:04d}', 'customer': cust, 'item': item, 'qty': 1 + (i * 5) % 12,
                     'unit_price': price, 'placed_at': placed, 'pickup_at': pickup, 'status': 'placed', 'note': None})
    orders(a.target).insert(rows)
    log(a.target, 'web', 'insert 12 orders', 'online order export')


def cmd_day(a) -> None:
    t = orders(a.target)
    t.update({'pickup_at': BASE + dt.timedelta(hours=10)}, where=t.order_id == 'ord-0003')
    log(a.target, 'ana', 'move pickup ord-0003 to 16:00', 'customer called')
    t.update({'unit_price': 3.75}, where=t.item == 'croissant')
    log(a.target, 'ben', 'croissant price 3.50 -> 3.75', 'butter price increase')
    t.batch_update([{'order_id': 'ord-0005', 'status': 'baking'}, {'order_id': 'ord-0006', 'status': 'baking'},
                    {'order_id': 'ord-0002', 'status': 'ready', 'note': 'shelf B'}])
    log(a.target, 'oven', 'batch status update (3 orders)')
    t.update({'status': 'cancelled', 'note': 'no-show deposit kept'}, where=t.order_id == 'ord-0009')
    log(a.target, 'ana', 'cancel ord-0009', 'customer cancelled')
    t.delete(where=t.order_id == 'ord-0007')
    log(a.target, 'ben', 'delete ord-0007', 'entered twice by mistake (it was not)')
    # A bad bulk edit: everything marked picked up. Undo it with revert().
    t.update({'status': 'picked_up'})
    bad = log(a.target, 'ben', 'mark ALL orders picked_up', 'wrong filter')
    t.revert()
    pxt.get_table(f'{a.target}/change_log').delete(where=pxt.get_table(f'{a.target}/change_log').orders_version == bad)
    log(a.target, 'ana', f'revert v{bad}', 'undo bad bulk edit')


def cmd_history(a) -> None:
    t = orders(a.target)
    print(t.history())
    print((lambda c: c.order_by(c.at).select(c.orders_version, c.actor, c.action, c.reason).collect())(pxt.get_table(f'{a.target}/change_log')))


def rows_at(target: str, version: int | None) -> dict[str, dict]:
    path = f'{target}/orders' if version is None else f'{target}/orders:{version}'
    t = pxt.get_table(path)
    res = t.select(t.order_id, *[getattr(t, f) for f in FIELDS]).collect()
    return {r['order_id']: r for r in res}


def cmd_as_of(a) -> None:
    t = pxt.get_table(f'{a.target}/orders:{a.version}')
    print(t.order_by(t.order_id).select(t.order_id, t.customer, t.item, t.qty, t.unit_price, t.total, t.status,
                                        t.pickup_day, t.slot).collect())


def diff(target: str, v1: int, v2: int | None) -> list[str]:
    old, new = rows_at(target, v1), rows_at(target, v2)
    out = []
    for oid in sorted(set(old) | set(new)):
        if oid not in new:
            out.append(f'- {oid} deleted ({old[oid]["customer"]}, {old[oid]["item"]})')
        elif oid not in old:
            out.append(f'+ {oid} added')
        else:
            ch = [f'{f}: {old[oid][f]!r} -> {new[oid][f]!r}' for f in FIELDS if old[oid][f] != new[oid][f]]
            if ch:
                out.append(f'~ {oid} ' + '; '.join(ch))
    return out


def cmd_diff(a) -> None:
    for line in diff(a.target, a.v1, a.v2):
        print(line)


def cmd_restore(a) -> None:
    then = rows_at(a.target, a.version)
    if a.order_id not in then:
        sys.exit(f'{a.order_id} did not exist at version {a.version}')
    row = {'order_id': a.order_id, **{f: then[a.order_id][f] for f in FIELDS}}
    full = pxt.get_table(f'{a.target}/orders:{a.version}')
    row['placed_at'] = full.where(full.order_id == a.order_id).select(full.placed_at).collect()[0]['placed_at']
    t = orders(a.target)
    if a.order_id in rows_at(a.target, None):
        t.batch_update([row])
    else:
        t.insert([row])
    log(a.target, 'audit', f'restore {a.order_id} from v{a.version}')


def cmd_snapshot(a) -> None:
    # Snapshots live next to the schema target, not inside it: `pxt schema prune app.py bakery` drops every
    # table under bakery/ that app.py does not define, and that includes snapshots.
    pxt.create_dir(f'{a.target}_snapshots', if_exists='ignore')
    snap = pxt.create_snapshot(f'{a.target}_snapshots/{a.name}', orders(a.target), if_exists='replace_force')
    print(f'snapshot {a.target}_snapshots/{a.name}: {snap.count()} rows at orders v{version_of(orders(a.target))}')


def fingerprint(target: str) -> dict:
    t = orders(target)
    fp = {}
    for v in sorted(x['version'] for x in t.get_versions()):
        tv = pxt.get_table(f'{target}/orders:{v}')
        try:
            r = tv.select(n=pxt.functions.count(tv.order_id), revenue=pxt.functions.sum(tv.total)).collect()[0]
            fp[str(v)] = {'rows': r['n'], 'revenue': round(r['revenue'] or 0.0, 2)}
        except Exception as e:  # version 0 has no `total` values yet, etc.
            fp[str(v)] = {'error': type(e).__name__}
    return fp


def cmd_fingerprint(a) -> None:
    fp = fingerprint(a.target)
    if a.out:
        json.dump(fp, open(a.out, 'w'), indent=1)
    if a.against:
        ref = json.load(open(a.against))
        bad = {k: (ref.get(k), fp.get(k)) for k in set(ref) | set(fp) if ref.get(k) != fp.get(k)}
        print('fingerprint OK' if not bad else f'fingerprint MISMATCH: {bad}')
        sys.exit(1 if bad else 0)
    print(json.dumps(fp))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--target', default='bakery')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('seed').set_defaults(fn=cmd_seed)
    sub.add_parser('day').set_defaults(fn=cmd_day)
    sub.add_parser('history').set_defaults(fn=cmd_history)
    s = sub.add_parser('as-of'); s.add_argument('version', type=int); s.set_defaults(fn=cmd_as_of)
    s = sub.add_parser('diff'); s.add_argument('v1', type=int); s.add_argument('v2', type=int, nargs='?'); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser('restore'); s.add_argument('order_id'); s.add_argument('version', type=int); s.set_defaults(fn=cmd_restore)
    s = sub.add_parser('snapshot'); s.add_argument('name'); s.set_defaults(fn=cmd_snapshot)
    s = sub.add_parser('fingerprint'); s.add_argument('--out'); s.add_argument('--against'); s.set_defaults(fn=cmd_fingerprint)
    a = p.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
