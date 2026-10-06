#!/usr/bin/env python3
"""Фиксирует «Платных на день занятия» (crm.actions.paid_requests_on_day) на прошедших занятиях
лаб/интенсивов/курсов с 01.09.2026. Число считает SQL-функция paid_requests_on.
Запуск — systemd-таймер b2c-paid-snapshot.timer раз в сутки ночью.
Спека: docs/superpowers/specs/2026-10-06-b2c-master-rates-design.md

CLI:
  python3 b2c_paid_snapshot.py            # записать
  python3 b2c_paid_snapshot.py --dry-run  # напечатать, что записал бы
Креды Озмы — из ~/.claude.json (mcpServers.ozma.headers), как у b2b_product_tasks.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

LESSONS_QUERY = """
SELECT l.id AS id,
       paid_requests_on(l.parent_action, (l.action_datetime + '3 hours'::interval)::date) AS n
FROM crm.actions AS l
WHERE l.type = 'Занятие' AND NOT l.is_deleted
  AND l.paid_requests_on_day IS NULL
  AND l.action_datetime >= '2026-08-31T21:00:00Z'::datetime
  AND (l.action_datetime + '3 hours'::interval)::date < ($$transaction_time + '3 hours'::interval)::date
  AND l.parent_action=>class_type=>name IN ('Лаборатория', 'Интенсив', 'Интенсив YNG', 'Интенсив СПБ', 'Курс')
ORDER BY l.action_datetime
"""


def operations(rows):
    return [{"type": "update", "entity": {"schema": "crm", "name": "actions"}, "id": r["id"],
             "entries": {"paid_requests_on_day": int(r["n"])}}
            for r in rows if r.get("n") is not None]


def ozma_creds(path=None):
    path = path or os.path.expanduser("~/.claude.json")
    with open(path) as f:
        return json.load(f)["mcpServers"]["ozma"]["headers"]


def ozma_token(h):
    body = urllib.parse.urlencode({
        "grant_type": "password", "client_id": h["X-Ozma-Client-ID"], "client_secret": h["X-Ozma-Client-Secret"],
        "username": h["X-Ozma-Username"], "password": h["X-Ozma-Password"]}).encode()
    with urllib.request.urlopen(h["X-Ozma-Auth-URL"], body, timeout=60) as r:
        return json.load(r)["access_token"]


def ozma_rows(h, token, query):
    url = h["X-Ozma-URL"].rstrip("/") + "/views/anonymous/entries?" + urllib.parse.urlencode({"__query": query})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    names = [c["name"] for c in d["info"]["columns"]]
    return [{n: v.get("value") for n, v in zip(names, row["values"])} for row in d["result"]["rows"]]


def ozma_transaction(h, token, ops):
    url = h["X-Ozma-URL"].rstrip("/") + "/transaction"
    req = urllib.request.Request(url, data=json.dumps({"operations": ops}).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    h = ozma_creds()
    token = ozma_token(h)
    ops = operations(ozma_rows(h, token, LESSONS_QUERY))
    for op in ops:
        print(op["id"], op["entries"]["paid_requests_on_day"])
    if ops and not args.dry_run:
        ozma_transaction(h, token, ops)
    print("занятий: %d%s" % (len(ops), " (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
