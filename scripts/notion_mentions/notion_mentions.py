#!/usr/bin/env python3
"""Теги Кати / Бэллы в комментариях «🗂️ Таблицы с продуктами» → задачи в «✏️ Документооборот и точка».

Детерминированно, без LLM: Notion REST API, состояние в STATE_FILE. Запуск — systemd-таймер
notion-mentions.timer раз в 10 минут. Спека: docs/superpowers/specs/2026-09-29-notion-mentions-to-tasks-design.md

CLI:
  python3 notion_mentions.py                    # обычный прогон
  python3 notion_mentions.py --dry-run          # напечатать, что сделал бы; Notion и состояние не трогает
  python3 notion_mentions.py --backfill-days N  # разобрать теги за N дней (в т.ч. при первом запуске)
Нужны env: NOTION_TOKEN; BOT_TOKEN (алерт Бэлле), опционально TG_PROXY.
"""
import argparse
import datetime
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

PRODUCTS_DB = "1d8612c762af80519c25e6c7b07a19f8"   # «🗂️ Таблица с продуктами»
TASKS_DB = "38940f6e6dc341d79ce55c0ef6db79bc"      # «✏️ Документооборот и точка»
WATCH = {                                           # Notion ID — из базы «Команда»
    "d5b39214-66db-4761-8ef7-0447637286d0": "Катя Романова",
    "40cc4df7-607d-40cb-9ab3-f3d2004a633a": "Бэлла Фатт",
}
TITLE_PROP = "Название программы (внутр)"
PROCESS = "Продукты"
CLOSED_PRODUCT = ("Проведено", "Отменено")          # такие карточки не сканируем
REOPEN = ("Готово", "Проверка")                      # повторный тег возвращает в «Не начато»
NEW_STATUS = "Не начато"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
FAILS_FILE = os.path.join(HERE, "fails.json")
OWNER_CHAT = 197654998                              # личка Бэллы (как OWNER_CHAT у Тейлор)
FAIL_ALERT_AFTER = 3
MSK = datetime.timezone(datetime.timedelta(hours=3))


# ---------- чистые функции ----------

def watched_mentions(comment, watch=WATCH):
    out = []
    for x in comment.get("rich_text") or []:
        m = x.get("mention") or {}
        if x.get("type") == "mention" and m.get("type") == "user":
            uid = (m.get("user") or {}).get("id")
            if uid in watch and uid not in out:
                out.append(uid)
    return out


def _parse(iso):
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def msk_date(iso):
    return _parse(iso).astimezone(MSK).date().isoformat()


def comment_link(page_id, discussion_id):
    return "https://app.notion.com/p/%s?d=%s" % (page_id.replace("-", ""), discussion_id.replace("-", ""))


def product_title(page):
    prop = (page.get("properties") or {}).get(TITLE_PROP) or {}
    title = "".join(x.get("plain_text", "") for x in prop.get("title") or []).strip()
    return title or "Без названия"


def _text(content, url=None):
    t = {"type": "text", "text": {"content": content}}
    if url:
        t["text"]["link"] = {"url": url}
    return t


def task_properties(title, uid, date):
    return {
        "Задачи": {"title": [_text(title[:200])]},
        "Ответственный": {"people": [{"object": "user", "id": uid}]},
        "Процессы": {"multi_select": [{"name": PROCESS}]},
        "Дедлайн": {"date": {"start": date}},
        "Status": {"status": {"name": NEW_STATUS}},
    }


def mention_blocks(comment, author):
    when = _parse(comment["created_time"]).astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    link = comment_link(comment["parent"]["page_id"], comment["discussion_id"])
    body = "".join(x.get("plain_text", "") for x in comment.get("rich_text") or []).strip()
    quote = [_text(body[i:i + 2000]) for i in range(0, len(body), 2000)] or [_text("—")]
    return [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            _text("%s · %s · " % (when, author)), _text("открыть обсуждение", link)]}},
        {"object": "block", "type": "quote", "quote": {"rich_text": quote}},
    ]


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
            if e.code in (429, 502, 503) and attempt < tries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            raise NotionError(e.code, "%s %s → %s" % (method, path, e.read()[:300].decode("utf-8", "replace")))


def list_products():
    out, cursor = [], None
    flt = {"and": [{"property": "Статус", "select": {"does_not_equal": s}} for s in CLOSED_PRODUCT]}
    while True:
        body = {"page_size": 100, "filter": flt}
        if cursor:
            body["start_cursor"] = cursor
        d = notion("POST", "/databases/%s/query" % PRODUCTS_DB, body)
        out.extend(d.get("results", []))
        if not d.get("has_more"):
            return out
        cursor = d.get("next_cursor")


def list_comments(page_id):
    out, cursor = [], None
    while True:
        q = {"block_id": page_id, "page_size": 100}
        if cursor:
            q["start_cursor"] = cursor
        d = notion("GET", "/comments?" + urllib.parse.urlencode(q))
        out.extend(d.get("results", []))
        if not d.get("has_more"):
            return sorted(out, key=lambda c: c["created_time"])
        cursor = d.get("next_cursor")


def author_name(uid, cache):
    if uid in WATCH:
        return WATCH[uid]
    if uid not in cache:
        try:
            cache[uid] = notion("GET", "/users/" + uid).get("name") or "кто-то"
        except NotionError:
            cache[uid] = "кто-то"
    return cache[uid]


def live_task(task_id):
    if not task_id:
        return None
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

def handle(state, product, comment, uid, names, dry):
    key = "%s:%s" % (product["id"], uid)
    title = product_title(product)
    date = msk_date(comment["created_time"])
    blocks = mention_blocks(comment, author_name(comment["created_by"]["id"], names))
    task = live_task(state["tasks"].get(key))
    who = WATCH[uid]
    if task:
        status = ((task.get("properties", {}).get("Status") or {}).get("status") or {}).get("name")
        props = {"Дедлайн": {"date": {"start": date}}}
        if status in REOPEN:
            props["Status"] = {"status": {"name": NEW_STATUS}}
        line = "дописать задачу «%s» (%s), дедлайн %s%s" % (
            title, who, date, ", снова «Не начато»" if "Status" in props else "")
        if not dry:
            notion("PATCH", "/blocks/%s/children" % task["id"], {"children": blocks})
            notion("PATCH", "/pages/" + task["id"], {"properties": props})
        return line
    line = "создать задачу «%s» (%s), дедлайн %s" % (title, who, date)
    if not dry:
        head = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            _text("Карточка продукта: "), _text(title, product.get("url"))]}}]
        created = notion("POST", "/pages", {"parent": {"database_id": TASKS_DB},
                                            "properties": task_properties(title, uid, date),
                                            "children": head + blocks})
        state["tasks"][key] = created["id"]
    return line


def run(dry=False, backfill_days=None, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stored = load_json(STATE_FILE)
    first = stored is None
    state = stored or {"seen": [], "tasks": {}}
    seen = set(state["seen"])
    cutoff = now - datetime.timedelta(days=backfill_days) if backfill_days is not None else None
    names, log, skipped = {}, [], 0

    def mark(cid):
        seen.add(cid)
        state["seen"] = sorted(seen)
        if not dry:
            save_json(STATE_FILE, state)

    for product in list_products():
        for c in list_comments(product["id"]):
            if c["id"] in seen:
                continue
            fresh = cutoff is None or _parse(c["created_time"]) >= cutoff
            if first and cutoff is None or not fresh:
                skipped += 1
            else:
                for uid in watched_mentions(c):
                    log.append(handle(state, product, c, uid, names, dry))
            mark(c["id"])
    if not dry:
        save_json(STATE_FILE, state)
    if skipped:
        log.append("старых комментариев помечено виденными без задач: %d" % skipped)
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
            alert("⚠️ notion-mentions: %d прогона подряд с ошибкой, задачи по тегам не создаются.\n%s"
                  % (fails, err[:500]))
        except Exception:
            traceback.print_exc()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill-days", type=int)
    a = ap.parse_args(argv)
    try:
        log = run(dry=a.dry_run, backfill_days=a.backfill_days)
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
