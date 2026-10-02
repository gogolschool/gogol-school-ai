#!/usr/bin/env python3
"""Новый B2B-продукт в Озме → задача Кате Романовой в «✏️ Документооборот и точка».

Детерминированно, без LLM: Ozma HTTP API (только чтение) + Notion REST API, состояние в STATE_FILE.
Запуск — systemd-таймер b2b-product-tasks.timer раз в 10 минут.
Спека: docs/superpowers/specs/2026-10-03-b2b-product-notion-task-design.md

CLI:
  python3 b2b_product_tasks.py            # обычный прогон
  python3 b2b_product_tasks.py --dry-run  # напечатать, что сделал бы; Notion и состояние не трогает
Нужны env: NOTION_TOKEN; BOT_TOKEN (алерт Бэлле), опционально TG_PROXY.
Креды Озмы — из ~/.claude.json (mcpServers.ozma.headers), как у ботов.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

TASKS_DB = "38940f6e6dc341d79ce55c0ef6db79bc"      # «✏️ Документооборот и точка»
KATYA = "d5b39214-66db-4761-8ef7-0447637286d0"      # Катя Романова — Notion ID из базы «Команда»
PROCESS = "b2b клиенты"
NEW_STATUS = "Не начато"
LINK_PROP = "Ссылка на озму - продукт"
PRODUCT_URL = "https://ozma.gogol.school/views/crm/b2b_product_form?id=%d"
SVOD_URL = "https://docs.google.com/spreadsheets/d/1cL7_8TivN_IpELC4d-_D-CvHP8dsNo2h7mAGbIHXN7s/edit?gid=0#gid=0"
CHECKLIST = [
    "Завести организацию в Озме по карточке",
    "Узнать, чья форма договора",
    "Подготовить договор",
    "Сделать счет по инструкции",
    "Согласовать с Бэллой договор и счет",
    "Согласовать с контрагентом договор и счет",
    "Получить оригиналы или подписаться в ЭДО",
    "Получить оплату",
    ("Добавить оплату в ", "Свод программ", SVOD_URL),
    "Сделать акт/УПД и согласовать с Бэллой",
    "Подписать акт/УПД с контрагентом",
    "Выгрузить все сканы документов/или доки из ЭДО на яндекс диск и прикрепить ссылку на папку",
]
SETTLE = datetime.timedelta(minutes=15)   # пауза, чтобы агенты дописали продукт
REFILL = datetime.timedelta(days=3)       # сколько дозаполняем Сумму / Ссылку Диск
DEADLINE_DAYS = 3

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
FAILS_FILE = os.path.join(HERE, "fails.json")
OWNER_CHAT = 197654998                    # личка Бэллы (как OWNER_CHAT у Тейлор)
FAIL_ALERT_AFTER = 3
MSK = datetime.timezone(datetime.timedelta(hours=3))

PRODUCTS_QUERY = """
SELECT a.id AS id, a.name AS name, a.is_deleted AS deleted, a.class_status=>name AS status,
  a.price AS price, a.b2b_disk_folder AS disk, a.customer_name AS customer,
  a.customer_contact AS contact, a.customer_contact_tg AS tg, a.description AS descr,
  a.pl_start_date AS start, a.gs_manager AS manager, a.b2b_deal AS deal,
  a.b2b_deal=>contact AS deal_contact, a.b2b_deal=>responsible AS deal_resp,
  a.b2b_client=>main_name AS client, a.b2b_deal=>client=>main_name AS deal_client,
  (SELECT min(l.action_datetime) FROM crm.actions AS l
    WHERE l.parent_action = a.id AND l.type = 'Занятие') AS first_lesson
FROM crm.actions AS a
WHERE a.type = 'Продукт' AND (a.is_b2b OR a.class_type = 23 OR a.b2b_deal IS NOT NULL)
ORDER BY a.id
"""


# ---------- чистые функции ----------

def _s(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def skip_reason(p):
    if p.get("deleted"):
        return "удалён"
    if p.get("status") == "Отменено":
        return "отменён"
    for f in ("name", "client", "deal_client", "deal"):
        if "тест" in _s(p.get(f)).lower():
            return "тестовый"
    return None


def _date(iso):
    """'2026-10-08T13:00:00Z' или '2026-10-29' → date (для datetime — в МСК)."""
    if not iso:
        return None
    if "T" in iso:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MSK).date()
    return datetime.date.fromisoformat(iso[:10])


def start_date(p):
    return _date(p.get("first_lesson")) or _date(p.get("start"))


def customer(p):
    return _s(p.get("customer")) or _s(p.get("client")) or _s(p.get("deal_client"))


def contact(p):
    return _s(p.get("contact")) or _s(p.get("deal_contact"))


def task_title(p):
    who = customer(p)
    if not who:
        return "%s - b2b" % _s(p.get("name"))
    d = start_date(p)
    parts = [who] + ([d.strftime("%d.%m.%y")] if d else []) + ([contact(p)] if contact(p) else [])
    return "/".join(parts) + " - b2b"


def _text(content, url=None, bold=False):
    t = {"type": "text", "text": {"content": content}}
    if url:
        t["text"]["link"] = {"url": url}
    if bold:
        t["annotations"] = {"bold": True}
    return t


def _para(*rich):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": list(rich)}}


def task_properties(p, today):
    props = {
        "Задачи": {"title": [_text(task_title(p)[:200])]},
        "Ответственный": {"people": [{"object": "user", "id": KATYA}]},
        "Процессы": {"multi_select": [{"name": PROCESS}]},
        "Status": {"status": {"name": NEW_STATUS}},
        "Дедлайн": {"date": {"start": (today + datetime.timedelta(days=DEADLINE_DAYS)).isoformat()}},
        LINK_PROP: {"url": PRODUCT_URL % p["id"]},
    }
    props.update(refill_properties(p, {}))
    if _s(p.get("tg")):
        props["Phone"] = {"phone_number": _s(p.get("tg"))}
    return props


def refill_properties(p, task_props):
    """Сумма / Ссылка Диск, которые есть в Озме и пусты в задаче."""
    out = {}
    if p.get("price") and (task_props.get("Сумма") or {}).get("number") is None:
        out["Сумма"] = {"number": p["price"]}
    if _s(p.get("disk")) and not (task_props.get("Ссылка Диск") or {}).get("url"):
        out["Ссылка Диск"] = {"url": _s(p.get("disk"))}
    return out


def task_blocks(p):
    lines = [("Продукт: ", _s(p.get("name")), PRODUCT_URL % p["id"])]
    for label, val in (("Сделка: ", p.get("deal")), ("Клиент: ", p.get("client") or p.get("deal_client")),
                       ("Заказчик: ", p.get("customer"))):
        if _s(val):
            lines.append((label, _s(val), None))
    c = " ".join(x for x in (contact(p), _s(p.get("tg"))) if x)
    if c:
        lines.append(("Контакт: ", c, None))
    acc = _s(p.get("manager")) or _s(p.get("deal_resp"))
    if acc:
        lines.append(("Аккаунт: ", acc, None))
    d = start_date(p)
    if d:
        lines.append(("Старт: ", d.strftime("%d.%m.%Y"), None))
    if p.get("price"):
        lines.append(("Сумма: ", "{:,} ₽".format(p["price"]).replace(",", " "), None))
    blocks = [_para(_text("Суть (из продукта в Озме %d, задача создана автоматически)" % p["id"], bold=True))]
    blocks += [_para(_text(label), _text(val, url)) for label, val, url in lines]
    descr = _s(p.get("descr"))
    if descr:
        cut = descr[:1500] + ("…" if len(descr) > 1500 else "")
        blocks.append({"object": "block", "type": "quote", "quote": {"rich_text": [_text(cut)]}})
    for item in CHECKLIST:
        rich = [_text(item)] if isinstance(item, str) else [_text(item[0]), _text(item[1], item[2])]
        blocks.append({"object": "block", "type": "to_do", "to_do": {"rich_text": rich, "checked": False}})
    return blocks


def links_to_product(url, pid):
    return bool(url) and re.search(r"[?&]id=%d(?!\d)" % pid, url) is not None


# ---------- Озма ----------

def ozma_creds(path=None):
    path = path or os.path.expanduser("~/.claude.json")
    with open(path) as f:
        cfg = json.load(f)
    return cfg["mcpServers"]["ozma"]["headers"]


def ozma_rows(query):
    h = ozma_creds()
    body = urllib.parse.urlencode({
        "grant_type": "password", "client_id": h["X-Ozma-Client-ID"], "client_secret": h["X-Ozma-Client-Secret"],
        "username": h["X-Ozma-Username"], "password": h["X-Ozma-Password"]}).encode()
    with urllib.request.urlopen(h["X-Ozma-Auth-URL"], body, timeout=60) as r:
        token = json.load(r)["access_token"]
    url = h["X-Ozma-URL"].rstrip("/") + "/views/anonymous/entries?" + urllib.parse.urlencode({"__query": query})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    names = [c["name"] for c in d["info"]["columns"]]
    return [{n: (v.get("pun") if v.get("pun") is not None else v.get("value"))
             for n, v in zip(names, row["values"])} for row in d["result"]["rows"]]


def list_products():
    return ozma_rows(PRODUCTS_QUERY)


# ---------- Notion ----------

class NotionError(Exception):
    def __init__(self, code, msg):
        super().__init__("Notion %s: %s" % (code, msg))
        self.code = code


def notion(method, path, body=None, tries=5):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(
            "https://api.notion.com/v1" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + os.environ["NOTION_TOKEN"],
                     "Notion-Version": "2022-06-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            raise NotionError(e.code, "%s %s → %s" % (method, path, e.read()[:300].decode("utf-8", "replace")))


def find_task(pid):
    """Уже заведённая (вручную) задача со ссылкой на продукт pid, иначе None."""
    d = notion("POST", "/databases/%s/query" % TASKS_DB, {
        "page_size": 100,
        "filter": {"property": LINK_PROP, "url": {"contains": "id=%d" % pid}}})
    for t in d.get("results", []):
        if links_to_product(((t.get("properties") or {}).get(LINK_PROP) or {}).get("url"), pid):
            return t["id"]
    return None


def live_task(task_id):
    try:
        task = notion("GET", "/pages/" + task_id)
    except NotionError as e:
        if e.code == 404:
            return None
        raise
    return None if task.get("archived") or task.get("in_trash") else task


# ---------- состояние ----------

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- исполнитель ----------

def run(dry=False, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.astimezone(MSK).date()
    products = list_products()
    stored = load_json(STATE_FILE)
    log = []

    def save():
        if not dry:
            save_json(STATE_FILE, state)

    if stored is None:
        state = {"seen": sorted(p["id"] for p in products), "pending": {}, "tasks": {}}
        save()
        return ["первый запуск: %d существующих B2B-продуктов помечено виденными без задач" % len(products)]
    state = stored
    seen = set(state["seen"])

    def mark(pid):
        seen.add(pid)
        state["seen"] = sorted(seen)
        state["pending"].pop(str(pid), None)
        save()

    by_id = {p["id"]: p for p in products}
    for p in products:
        pid = p["id"]
        if pid in seen:
            continue
        why = skip_reason(p)
        if why:
            log.append("пропуск %d «%s»: %s" % (pid, _s(p.get("name")), why))
            mark(pid)
            continue
        first = state["pending"].get(str(pid))
        if first is None:
            state["pending"][str(pid)] = now.isoformat()
            log.append("новый %d «%s» — задача через %d мин" % (pid, _s(p.get("name")), SETTLE.seconds // 60))
            save()
            continue
        if now - datetime.datetime.fromisoformat(first) < SETTLE:
            continue
        existing = find_task(pid)
        if existing:
            log.append("%d: задача уже есть в Notion (%s) — не дублирую" % (pid, existing))
            state["tasks"][str(pid)] = {"task": existing, "created": now.isoformat(), "adopted": True}
            mark(pid)
            continue
        log.append("создать задачу «%s» (продукт %d)" % (task_title(p), pid))
        if not dry:
            created = notion("POST", "/pages", {
                "parent": {"database_id": TASKS_DB}, "icon": {"type": "emoji", "emoji": "⭐"},
                "properties": task_properties(p, today), "children": task_blocks(p)})
            state["tasks"][str(pid)] = {"task": created["id"], "created": now.isoformat()}
        mark(pid)

    for key, t in list(state["tasks"].items()):
        p = by_id.get(int(key))
        if not p or now - datetime.datetime.fromisoformat(t["created"]) > REFILL:
            continue
        if not (p.get("price") or _s(p.get("disk"))):
            continue
        task = live_task(t["task"])
        if not task:
            continue
        props = refill_properties(p, task.get("properties") or {})
        if props:
            log.append("дозаполнить задачу продукта %s: %s" % (key, ", ".join(props)))
            if not dry:
                notion("PATCH", "/pages/" + task["id"], {"properties": props})
    save()
    return log


# ---------- CLI и алерт ----------

def alert(text):
    token = os.environ.get("BOT_TOKEN")
    if not token:
        return
    proxy = os.environ.get("TG_PROXY", "")
    opener = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []))
    data = urllib.parse.urlencode({"chat_id": OWNER_CHAT, "text": text}).encode()
    opener.open("https://api.telegram.org/bot%s/sendMessage" % token, data, timeout=30)


def record_failure(err):
    fails = (load_json(FAILS_FILE) or {}).get("fails", 0) + 1
    save_json(FAILS_FILE, {"fails": fails})
    if fails == FAIL_ALERT_AFTER:
        try:
            alert("⚠️ b2b-product-tasks: %d прогона подряд с ошибкой, задачи по новым B2B-продуктам "
                  "не создаются.\n%s" % (fails, err[:500]))
        except Exception:
            traceback.print_exc()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        log = run(dry=a.dry_run)
    except Exception as e:
        traceback.print_exc()
        if not a.dry_run:
            record_failure(str(e))
        return 1
    for line in log:
        print(("[dry-run] " if a.dry_run else "") + line)
    if not a.dry_run:
        save_json(FAILS_FILE, {"fails": 0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
