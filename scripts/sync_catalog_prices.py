#!/usr/bin/env python3
"""Синхронизатор каталожных цен gogolschool.ru (Bitrix, iblock 16).

Раз в сутки сверяет каталожную цену услуги с действующей ценовой парой
свойства PRICES и выравнивает её. Всё остальное (сломанный CHECKED, пары
без дат, нулевые цены) — только сообщает, не трогая.

Запуск:
    python3 sync_catalog_prices.py            # dry-run, ничего не пишет
    python3 sync_catalog_prices.py --apply    # чинит каталожные цены

Переменные окружения: BITRIX_API_URL, BITRIX_API_TOKEN,
                      TG_BOT_TOKEN, TG_CHAT_ID (опционально).
"""
import os
import sys
import json
import urllib.request
from datetime import datetime

API = os.environ["BITRIX_API_URL"]
TOKEN = os.environ["BITRIX_API_TOKEN"]
APPLY = "--apply" in sys.argv


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"X-Claude-Token": TOKEN, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["data"]


def parse(dt):
    """Даты приходят как «ДД.ММ.ГГГГ ЧЧ:ММ:СС» либо просто «ДД.ММ.ГГГГ»."""
    if not dt:
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(dt, fmt)
        except ValueError:
            continue
    return None


def active_rows(prices, now):
    """Строки, действующие на момент now."""
    out = []
    for r in prices:
        if not isinstance(r, dict):
            continue
        af, at = parse(r.get("ACTIVE_FROM")), parse(r.get("ACTIVE_TO"))
        if (af is None or af <= now) and (at is None or at > now):
            out.append(r)
    return out


def check(service, now):
    """Возвращает (правка_каталожной | None, [проблемы])."""
    sid, name = service["id"], service["name"]
    problems, fix = [], None

    if service.get("price") in (0, "0", None):
        problems.append(f"{sid} {name}: каталожная цена 0 — страница отдаёт «Услуга не найдена»")

    prices = [r for r in (service.get("prices") or []) if isinstance(r, dict)]
    if not prices:
        return fix, problems  # ОУ/МК без рассрочки — норма

    live = active_rows(prices, now)
    full = [r for r in live if r.get("CREDIT") != "Y"]
    credit = [r for r in live if r.get("CREDIT") == "Y"]

    if not full:
        problems.append(f"{sid} {name}: ни одна ценовая пара не действует сегодня")
        return fix, problems
    if len(full) > 1:
        sums = ", ".join(r["PRICE"] for r in full)
        problems.append(f"{sid} {name}: одновременно действуют несколько цен ({sums}) — проверить даты пар")
        return fix, problems

    row = full[0]
    if credit and any(r.get("CHECKED") != "2" for r in live):
        problems.append(
            f"{sid} {name}: у действующей пары CHECKED≠2 — рассрочка/Долями/Сплит скрыты с формы")

    want = int(row["PRICE"])
    have = int(service.get("price") or 0)
    if want != have:
        fix = (sid, name, have, want, row.get("NAME", ""))
    return fix, problems


def notify(lines):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (token and chat):
        return
    text = "\n".join(lines)[:4000]
    urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json"}))


def main():
    now = datetime.now()
    items = api("/services?limit=1000")["items"]
    fixes, problems = [], []

    for it in items:
        try:
            s = api(f"/services/{it['id']}")
        except Exception as e:                     # noqa: BLE001
            problems.append(f"{it['id']}: не удалось прочитать ({e})")
            continue
        if s.get("active") != "Y":
            continue
        at = parse(s.get("activeTo"))
        if at and at < now:                        # приём оплаты уже закрыт
            continue
        fix, probs = check(s, now)
        problems += probs
        if fix:
            fixes.append(fix)

    report = []
    for sid, name, have, want, label in fixes:
        if APPLY:
            api(f"/catalog/{sid}/prices", "PUT",
                {"prices": [{"catalogGroupId": 1, "price": want, "currency": "RUB"}]})
            report.append(f"✅ {sid} {name}: каталожная {have} → {want} ({label})")
        else:
            report.append(f"[dry-run] {sid} {name}: каталожная {have} → {want} ({label})")

    if problems:
        report.append("")
        report.append("⚠️ Требует рук:")
        report += [f"• {p}" for p in problems]

    if report:
        header = "Витрина: синхронизация цен" + ("" if APPLY else " (dry-run)")
        print("\n".join([header] + report))
        notify([header] + report)
    else:
        print("Витрина: расхождений нет")


if __name__ == "__main__":
    main()
