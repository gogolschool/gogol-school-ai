#!/usr/bin/env python3
"""Синхронизатор каталожных цен gogolschool.ru (Bitrix, iblock 16).

Раз в сутки сверяет каталожную цену услуги с действующей ценовой парой
свойства PRICES и выравнивает её. Всё остальное (сломанный CHECKED, пары
без дат, нулевые цены) — только сообщает, не трогая.

Запуск:
    python3 sync_catalog_prices.py                    # dry-run, ничего не пишет
    python3 sync_catalog_prices.py --apply            # чинит каталожные цены
    python3 sync_catalog_prices.py --apply --quiet    # чинит молча, без уведомлений

Переменные окружения: BITRIX_API_URL, BITRIX_API_TOKEN,
                      TG_BOT_TOKEN, TG_CHAT_ID (опционально).
"""
import os
import sys
import json
import urllib.error
import urllib.request
from datetime import datetime

API = os.environ["BITRIX_API_URL"]
TOKEN = os.environ["BITRIX_API_TOKEN"]
APPLY = "--apply" in sys.argv
QUIET = "--quiet" in sys.argv   # чинит, но не пишет в Telegram (ночной прогон)


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"X-Claude-Token": TOKEN, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["data"]


def all_services():
    """Все услуги постранично.

    У /services параметр limit молча режется до 50 (сколько ни проси), поэтому
    единственный способ увидеть весь каталог — идти по page. Раньше здесь стоял
    limit=1000 без пагинации: сверялись первые 50 услуг из 1145.
    """
    items, page, seen = [], 1, set()
    while True:
        batch = api(f"/services?limit=50&page={page}")["items"]
        fresh = [i for i in batch if i["id"] not in seen]
        if not fresh:
            break
        seen.update(i["id"] for i in fresh)
        items += fresh
        page += 1
        if page > 200:                             # предохранитель от зацикливания
            break
    return items


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


def page_broken(sid):
    """Правда ли страница оплаты услуги недоступна клиенту.

    Дёргаем только для услуг с нулевой ценой — их единицы, на прогон не влияет.
    Признак поломки — HTTP 404: у недоступной услуги страница просто не отдаётся.
    Фразы «Услуга не найдена» в HTML при этом НЕТ (там обычная 404-я), так что
    искать её текстом бесполезно — проверено 08.09.2026.
    Сеть подвела — считаем страницу целой и молчим: ложная тревога хуже пропуска.
    """
    try:
        req = urllib.request.Request(
            f"https://gogolschool.ru/payment/{sid}/",
            headers={"User-Agent": "catalog-price-sync"})
        resp = urllib.request.urlopen(req, timeout=20)
        return resp.getcode() != 200
    except urllib.error.HTTPError as e:
        return e.code == 404
    except Exception:                              # noqa: BLE001
        return False                               # сеть подвела — молчим


def num(v):
    """Цена как целое; None, если поле пустое или не число («», «—», мусор)."""
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
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

    # Нулевая цена сама по себе не поломка: у бесплатных МК так и должно быть
    # (страница рендерится, просто без способов оплаты). Ругаемся, только если
    # страница действительно отдаёт «Услуга не найдена» — это проверяем, а не
    # предполагаем: раньше фраза дописывалась к любому нулю и вводила в заблуждение.
    if service.get("price") in (0, "0", None) and page_broken(sid):
        problems.append(f"{sid} {name}: каталожная цена 0 и страница отдаёт «Услуга не найдена»")

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
        sums = ", ".join(str(r.get("PRICE") or "—") for r in full)
        problems.append(f"{sid} {name}: одновременно действуют несколько цен ({sums}) — проверить даты пар")
        return fix, problems

    row = full[0]
    if credit and any(r.get("CHECKED") != "2" for r in live):
        problems.append(
            f"{sid} {name}: у действующей пары CHECKED≠2 — рассрочка/Долями/Сплит скрыты с формы")

    # Пустая/нечисловая цена в действующей паре — это данные, а не повод падать:
    # раньше скрипт видел лишь 50 услуг и до таких строк просто не доходил.
    want, have = num(row.get("PRICE")), num(service.get("price")) or 0
    if want is None:
        problems.append(
            f"{sid} {name}: у действующей пары пустая или нечисловая цена "
            f"({row.get('PRICE')!r}) — каталожную не трогаю")
        return fix, problems
    if want != have:
        fix = (sid, name, have, want, row.get("NAME", ""))
    return fix, problems


def notify(lines):
    if QUIET:
        return
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
    items = all_services()
    fixes, problems = [], []

    for it in items:
        try:
            s = api(f"/services/{it['id']}")
        except Exception as e:                     # noqa: BLE001
            problems.append(f"{it['id']}: не удалось прочитать ({e})")
            continue
        if s.get("active") != "Y":
            continue
        if str(s.get("available")) == "0":          # снята с продажи (прошлые сезоны)
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
