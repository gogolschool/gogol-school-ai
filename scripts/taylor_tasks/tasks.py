#!/usr/bin/env python3
"""Тейлор · входящие задачи из Telegram → Notion «🧷 Задачи».

Человек пишет / надиктовывает / пересылает Тейлор в личку — задача ложится в общую
базу «🧷 Задачи» с ним как исполнителем. Правки — ответом на сообщение бота
(«срок на среду», «готово», «удали»), по будням в 10:00 — сводка.

Без агента: разбор — один вызов haiku без инструментов, запись — Notion REST API.
Спека: docs/superpowers/specs/2026-10-14-taylor-tasks-inbox-design.md

Состояние живёт в state.json Тейлор (ключ "tasks"), модуль получает dict и колбэк сохранения:
  msgs    — message_id сообщения бота → страницы, на которые ссылаются его номера строк;
  pending — пачки, которые не удалось записать (Notion недоступен), дописывает retry_pending.

CLI (ручная проверка на сервере, нужны NOTION_TOKEN и CLAUDE_CODE_OAUTH_TOKEN):
  python3 tasks.py parse "текст"           # только разбор, без записи
  python3 tasks.py digest NOTION_USER_ID   # текст сводки, без отправки
"""
import os
import re
import sys
import json
import html
import time
import datetime
import subprocess
import urllib.error
import urllib.request

TASKS_DB = "aefa741ea95f49da9381e9cbaba55c81"     # «🧷 Задачи»
OPEN_STATUSES = ["Не начато", "на паузе", "согласовано", "В работе", "На согласовании"]
NEW_STATUS = "Не начато"
PRIORITIES = ["Низкий", "Средний", "Высокий", "Срочно"]
MSK = datetime.timezone(datetime.timedelta(hours=3))
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WD_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

CLAUDE = "/home/agentbot/.nvm/versions/node/v20.20.2/bin/claude"
NODE_BIN = "/home/agentbot/.nvm/versions/node/v20.20.2/bin"
LLM_TIMEOUT = 60
TITLE_MAX = 80
SOON_DAYS = 3            # «ближайшие N дней» в сводке
MSG_KEEP_DAYS = 30       # сколько помним, какие страницы стоят за номерами в сообщении бота
FULL_LIST_MAX = 40       # строк в «что у меня»

EDIT_HELP = ("Можно так: «срок на среду», «готово», «удали», «переименуй в …», «срочно». "
             "Если в сообщении несколько задач — с номером: «2 готово».")


# ---------- даты ----------

def now_msk():
    return datetime.datetime.now(MSK)


def fmt_date(iso):
    """'2026-10-16' → 'пт 16.10'; '2026-10-16T15:00:00+03:00' → 'пт 16.10 15:00'.
    Не текущий год — с годом: 'пт 18.10.24' (в сводке бывают старые просрочки)."""
    if not iso:
        return ""
    d = datetime.date.fromisoformat(iso[:10])
    s = "%s %s" % (WD[d.weekday()], d.strftime("%d.%m" if d.year == now_msk().year else "%d.%m.%y"))
    if len(iso) > 10 and "T" in iso:
        s += " " + iso[11:16]
    return s


def calendar_hint(today, days=14):
    """Подсказка модели: ближайшие даты с днями недели (сама она в них путается)."""
    out = []
    for i in range(days):
        d = today + datetime.timedelta(days=i)
        tag = " (сегодня)" if i == 0 else (" (завтра)" if i == 1 else "")
        out.append("%s %s = %s%s" % (WD_FULL[d.weekday()], d.strftime("%d.%m"), d.isoformat(), tag))
    return "\n".join(out)


# ---------- пользователи ----------

def parse_users(raw):
    """TASK_USERS='tg_id:notion_user_id[:board_page_id],…' → {tg_id: {"notion", "board"}}."""
    users = {}
    for part in (raw or "").replace(";", ",").split(","):
        bits = [b.strip() for b in part.strip().split(":")]
        if len(bits) < 2 or not bits[0].lstrip("-").isdigit() or not bits[1]:
            continue
        users[int(bits[0])] = {"notion": bits[1], "board": bits[2] if len(bits) > 2 and bits[2] else None}
    return users


# ---------- LLM (haiku без инструментов) ----------

def llm(prompt):
    """Один короткий вызов haiku. Возвращает stdout или кидает исключение."""
    env = dict(os.environ)
    env["HOME"] = "/home/agentbot"
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    env.pop("BOT_TOKEN", None)
    proxy = env.get("TG_PROXY", "")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if proxy:
            env[k] = proxy
        else:
            env.pop(k, None)
    cmd = [CLAUDE, "-p", prompt, "--model", "haiku", "--tools", "", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence"]
    r = subprocess.run(cmd, cwd="/tmp", env=env, capture_output=True, text=True, timeout=LLM_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError("claude rc=%s: %s" % (r.returncode, (r.stderr or r.stdout or "")[:300]))
    return r.stdout or ""


def extract_json(text):
    """Первый JSON-массив/объект из ответа модели (она любит обёртки ```json)."""
    text = text or ""
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise ValueError("нет JSON в ответе")
    start = min(starts)
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start:end + 1])


PARSE_PROMPT = """Ты помощник, который превращает заметки сотрудника школы в задачи для таск-трекера.
Сегодня {today} ({weekday}), часовой пояс Москва. Ближайшие даты:
{calendar}

Ниже — сообщения, которые сотрудник отправил себе как задачи (может быть пересланное чужое сообщение
с пометкой «Переслано от», расшифровка голосового, несколько сообщений подряд). Выдели задачи.

Верни ТОЛЬКО JSON-массив, без пояснений. Каждый элемент:
{{"title": "коротко, как действие: «Позвонить …», «Отправить …»; до 80 символов; без срока и дат",
  "deadline": "YYYY-MM-DD" или null,
  "time": "HH:MM" или null,
  "from": "кто поставил задачу" или null,
  "priority": "Срочно" | "Высокий" | null}}

Правила:
- несколько независимых дел — несколько элементов; одно дело с подробностями — один элемент;
- срок только если он назван: «завтра», «в пятницу», «до 20-го», «к концу недели» (= ближайшая пятница);
  «до пятницы» = ближайшая пятница, если сегодня пятница — сегодня; «на следующей неделе» без дня = понедельник;
  ничего не сказано про срок — null, не выдумывай;
- time — только если названо время;
- from — имя из текста («Катя попросила…») или из пометки «Переслано от»; иначе null;
- priority «Срочно» — если «срочно», «горит», «asap», «прямо сейчас»; «Высокий» — если «важно», «в приоритете»; иначе null;
- пиши по-русски, сохраняй имена и названия как в тексте.

Сообщения:
<<<
{text}
>>>"""


def _valid_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def normalize_task(d):
    if not isinstance(d, dict):
        return None
    title = re.sub(r"\s+", " ", str(d.get("title") or "")).strip()
    if not title:
        return None
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX - 1].rstrip() + "…"
    deadline = d.get("deadline") if _valid_date(d.get("deadline")) else None
    tm = d.get("time")
    tm = tm if deadline and isinstance(tm, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", tm) else None
    pr = d.get("priority") if d.get("priority") in PRIORITIES else None
    frm = re.sub(r"\s+", " ", str(d.get("from") or "")).strip() or None
    return {"title": title, "deadline": deadline, "time": tm, "from": frm, "priority": pr}


def fallback_task(text):
    first = re.sub(r"\s+", " ", (text or "").strip())
    first = re.sub(r"^\[[^\]]*\]\s*", "", first)  # служебные пометки вида [голосовое]
    title = first[:TITLE_MAX - 1].rstrip() + "…" if len(first) > TITLE_MAX else first
    return {"title": title or "Задача без текста", "deadline": None, "time": None,
            "from": None, "priority": None}


def parse_tasks(text, today=None):
    """Текст пачки → список задач. Не падает: при любой ошибке — одна задача из начала текста."""
    today = today or now_msk().date()
    prompt = PARSE_PROMPT.format(today=today.isoformat(), weekday=WD_FULL[today.weekday()],
                                 calendar=calendar_hint(today), text=text)
    try:
        data = extract_json(llm(prompt))
        if isinstance(data, dict):
            data = [data]
        tasks = [t for t in (normalize_task(x) for x in data) if t]
        if tasks:
            return tasks
        print("tasks parse: пустой результат — фолбэк", flush=True)
    except Exception as e:
        print("tasks parse err:", type(e).__name__, str(e)[:200], flush=True)
    return [fallback_task(text)]


# ---------- Notion ----------

def notion(method, path, body=None, tries=3):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(
            "https://api.notion.com/v1" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + os.environ["NOTION_TOKEN"],
                     "Notion-Version": "2022-06-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError("notion %s %s: %s %s" % (method, path, e.code, e.read()[:300]))
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def deadline_value(task):
    if not task.get("deadline"):
        return None
    if task.get("time"):
        return {"start": "%sT%s:00+03:00" % (task["deadline"], task["time"])}
    return {"start": task["deadline"]}


def page_properties(task, notion_user):
    props = {
        "Набор": {"title": [{"text": {"content": task["title"]}}]},
        "Исполнитель": {"people": [{"id": notion_user}]},
        "Статус": {"status": {"name": NEW_STATUS}},
    }
    dv = deadline_value(task)
    if dv:
        props["Дедлайн"] = {"date": dv}
    if task.get("priority"):
        props["Приоритет"] = {"select": {"name": task["priority"]}}
    return props


def _para(text, bold=False, italic=False):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "text": {"content": text},
         "annotations": {"bold": bold, "italic": italic}}]}}


def page_children(task, source_text, written_at):
    blocks = []
    if task.get("from"):
        blocks.append(_para("От кого: " + task["from"]))
    blocks.append(_para("Исходный текст", bold=True))
    src = (source_text or "").strip() or "—"
    for i in range(0, min(len(src), 6000), 1900):   # лимит Notion — 2000 символов на кусок
        blocks.append(_para(src[i:i + 1900]))
    blocks.append(_para("Записано через Тейлор, " + written_at.strftime("%d.%m.%Y %H:%M"), italic=True))
    return blocks


def create_page(task, notion_user, source_text, written_at):
    page = notion("POST", "/pages", {
        "parent": {"database_id": TASKS_DB},
        "properties": page_properties(task, notion_user),
        "children": page_children(task, source_text, written_at),
    })
    return {"id": page["id"], "url": page.get("url") or "", "title": task["title"],
            "deadline": deadline_value(task)["start"] if deadline_value(task) else None}


def update_page(page_id, props):
    return notion("PATCH", "/pages/" + page_id, {"properties": props})


def query_open(notion_user):
    """Открытые задачи человека во всей базе (не только записанные ботом)."""
    out, cursor = [], None
    while True:
        body = {"page_size": 100,
                "filter": {"and": [
                    {"property": "Исполнитель", "people": {"contains": notion_user}},
                    {"or": [{"property": "Статус", "status": {"equals": s}} for s in OPEN_STATUSES]},
                ]},
                "sorts": [{"property": "Дедлайн", "direction": "ascending"}]}
        if cursor:
            body["start_cursor"] = cursor
        r = notion("POST", "/databases/%s/query" % TASKS_DB, body)
        for p in r.get("results", []):
            pr = p.get("properties", {})
            title = "".join(x.get("plain_text", "") for x in (pr.get("Набор", {}).get("title") or []))
            dl = ((pr.get("Дедлайн") or {}).get("date") or {}).get("start")
            out.append({"id": p["id"], "url": p.get("url") or "",
                        "title": title.strip() or "Без названия", "deadline": dl})
        if not r.get("has_more"):
            return out
        cursor = r.get("next_cursor")


# ---------- тексты для Telegram (HTML) ----------

def _link(item):
    t = html.escape(item["title"], quote=False)
    return '<a href="%s">%s</a>' % (html.escape(item["url"]), t) if item.get("url") else t


def _when(item, prefix="до "):
    return (prefix + fmt_date(item["deadline"])) if item.get("deadline") else "без срока"


def confirm_text(items):
    lines = ["Записала ✨"]
    many = len(items) > 1
    for i, it in enumerate(items, 1):
        lines.append("%s %s — %s" % ("%d." % i if many else "•", _link(it), _when(it)))
    return "\n".join(lines)


def digest_text(items, today, board=None, full=False):
    """→ (html, items в порядке номеров). full=True — «что у меня»: все открытые задачи."""
    overdue, today_l, soon, later, nodate = [], [], [], [], []
    for it in items:
        if not it.get("deadline"):
            nodate.append(it)
            continue
        d = datetime.date.fromisoformat(it["deadline"][:10])
        if d < today:
            overdue.append(it)
        elif d == today:
            today_l.append(it)
        elif d <= today + datetime.timedelta(days=SOON_DAYS):
            soon.append(it)
        else:
            later.append(it)
    blocks = [("🔴 Просрочено", overdue, "было до "), ("📌 Сегодня", today_l, None),
              ("🗓 Ближайшие %d дня" % SOON_DAYS, soon, "")]
    if full:
        blocks += [("Дальше", later, ""), ("Без срока", nodate, None)]
    numbered, lines, n = [], ["Твои задачи ✨" if full else "Доброе утро ✨"], 0
    for head, lst, pref in blocks:
        if not lst:
            continue
        lines.append("")
        lines.append("<b>%s</b>" % head)
        for it in lst:
            if full and n >= FULL_LIST_MAX:
                break
            n += 1
            numbered.append(it)
            tail = "" if pref is None else " — " + pref + fmt_date(it["deadline"])
            lines.append("%d. %s%s" % (n, _link(it), tail))
    if not numbered:
        lines = ["Открытых задач нет ✨" if full else
                 "На сегодня задач со сроком нет ✨ Без срока: %d" % len(nodate)]
    elif not full:
        rest = []
        if later:
            rest.append("позже: %d" % len(later))
        if nodate:
            rest.append("без срока: %d" % len(nodate))
        if rest:
            lines.append("")
            lines.append("Ещё " + ", ".join(rest) + " — «что у меня» покажет все")
    elif len(items) > n:
        lines.append("")
        lines.append("…и ещё %d — на доске" % (len(items) - n))
    if numbered:
        lines.append("")
        lines.append("Ответь на это сообщение, чтобы поправить: «2 готово», «1 срок на пятницу».")
    if board:
        lines.append("")
        lines.append('<a href="https://www.notion.so/%s">Доска в Notion</a>' % board.replace("-", ""))
    return "\n".join(lines), numbered


# ---------- правки ответом ----------

EDIT_PROMPT = """Сотрудник ответил на сообщение бота со списком задач и просит что-то поменять.
Сегодня {today} ({weekday}), часовой пояс Москва. Ближайшие даты:
{calendar}

Задачи в сообщении (номер. название — срок):
{items}

Ответ сотрудника:
<<<
{text}
>>>

Верни ТОЛЬКО JSON-объект:
{{"targets": [номера задач] — пусто, если номер не назван и по смыслу не понять какая,
  "action": "deadline" | "done" | "archive" | "rename" | "priority" | "unknown",
  "value": для deadline — "YYYY-MM-DD" или "YYYY-MM-DDTHH:MM"; для rename — новое название;
           для priority — "Срочно" | "Высокий" | "Средний" | "Низкий"; иначе null}}
Правила: «готово/сделала/закрой» = done; «удали/не надо/отмена/в архив» = archive;
«перенеси/срок/на среду/до 20-го» = deadline; «все»/«обе» = все номера; если задача одна — targets [1]."""

_NUM = r"\d{1,2}(?:\s*(?:,|и)\s*\d{1,2})*"
_DONE = r"(?:готово|сделала?|сделано|выполнено|закрыла?|закрой|закрыть|done)"
_ARCH = r"(?:удали(?:ть)?|не надо|отмена|отмени(?:ть)?|в архив|архив)"


def _nums(s):
    return [int(x) for x in re.findall(r"\d{1,2}", s or "")]


def quick_edit(text):
    """Частые формы без LLM: «готово», «2 готово», «готово 1 и 3», «удали 2»."""
    t = text.strip().lower().rstrip(".!✨ ")
    for word, action in ((_DONE, "done"), (_ARCH, "archive")):
        m = re.fullmatch(r"(%s)?\s*%s\s*(%s)?" % (_NUM, word, _NUM), t)
        if m:
            return {"targets": _nums(m.group(1)) + _nums(m.group(2)), "action": action, "value": None}
    return None


def parse_edit(text, items, today=None):
    q = quick_edit(text)
    if q:
        return q
    today = today or now_msk().date()
    listing = "\n".join("%d. %s — %s" % (i, it["title"], fmt_date(it.get("deadline")) or "без срока")
                        for i, it in enumerate(items, 1))
    prompt = EDIT_PROMPT.format(today=today.isoformat(), weekday=WD_FULL[today.weekday()],
                                calendar=calendar_hint(today), items=listing, text=text)
    try:
        d = extract_json(llm(prompt))
        if not isinstance(d, dict):
            raise ValueError("не объект")
        targets = d.get("targets") or []
        if not isinstance(targets, list):
            targets = [targets]
        return {"targets": [int(x) for x in targets if str(x).isdigit()],
                "action": d.get("action") or "unknown", "value": d.get("value")}
    except Exception as e:
        print("tasks edit parse err:", type(e).__name__, str(e)[:200], flush=True)
        return {"targets": [], "action": "unknown", "value": None}


def edit_props(action, value):
    """→ (properties для PATCH, фраза подтверждения) или (None, текст ошибки)."""
    if action == "done":
        return {"Статус": {"status": {"name": "Готово"}}}, "Закрыла"
    if action == "archive":
        return {"Статус": {"status": {"name": "Архив"}}}, "Убрала в архив"
    if action == "deadline":
        v = str(value or "")
        if _valid_date(v[:10]) and (len(v) == 10 or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?", v)):
            start = v if len(v) == 10 else v[:16] + ":00+03:00"
            return {"Дедлайн": {"date": {"start": start}}}, "Перенесла на " + fmt_date(start)
        return None, "Не поняла, на какой срок перенести. Напиши, например, «срок на среду» или «до 20.10»."
    if action == "rename":
        v = re.sub(r"\s+", " ", str(value or "")).strip()[:TITLE_MAX]
        if v:
            return {"Набор": {"title": [{"text": {"content": v}}]}}, "Переименовала в «%s»" % v
        return None, "Не поняла новое название."
    if action == "priority":
        if value in PRIORITIES:
            return {"Приоритет": {"select": {"name": value}}}, "Поставила приоритет «%s»" % value
        return None, "Не поняла, какой приоритет поставить."
    return None, "Не поняла, что поменять. " + EDIT_HELP


def apply_edit(text, items, today=None):
    """Правка задач из одного сообщения бота. → (ответ, обновлённые items)."""
    e = parse_edit(text, items, today)
    targets = [n for n in e["targets"] if 1 <= n <= len(items)]
    if not targets:
        if len(items) == 1:
            targets = [1]
        elif e["action"] != "unknown":
            return ("Какую задачу — %s? Ответь с номером, например «%d %s»." % (
                ", ".join(str(i) for i in range(1, len(items) + 1)), 1,
                {"done": "готово", "archive": "удали"}.get(e["action"], "срок на пятницу"))), items
    props, phrase = edit_props(e["action"], e["value"])
    if props is None:
        return phrase, items
    for n in targets:
        update_page(items[n - 1]["id"], props)
        if "Дедлайн" in props:
            items[n - 1]["deadline"] = props["Дедлайн"]["date"]["start"]
        if "Набор" in props:
            items[n - 1]["title"] = props["Набор"]["title"][0]["text"]["content"]
    which = "" if len(items) == 1 else " (%s)" % ", ".join(str(n) for n in sorted(set(targets)))
    return phrase + which + " ✨", items


# ---------- состояние ----------

def remember(state, message_id, items):
    msgs = state.setdefault("msgs", {})
    msgs[str(message_id)] = {"ts": time.time(),
                             "items": [{k: it.get(k) for k in ("id", "url", "title", "deadline")}
                                       for it in items]}
    cutoff = time.time() - MSG_KEEP_DAYS * 86400
    for k in [k for k, v in msgs.items() if v.get("ts", 0) < cutoff]:
        msgs.pop(k, None)


def recall(state, message_id):
    m = state.get("msgs", {}).get(str(message_id))
    return m["items"] if m else None


# ---------- сценарии ----------

def capture(state, notion_user, text, now=None):
    """Пачка → задачи в Notion. → (html-ответ, items) ; items=None, если ушло в pending."""
    now = now or now_msk()
    tasks = parse_tasks(text, now.date())
    try:
        items = [create_page(t, notion_user, text, now) for t in tasks]
    except Exception as e:
        print("tasks notion err:", type(e).__name__, str(e)[:300], flush=True)
        state.setdefault("pending", []).append(
            {"notion": notion_user, "text": text, "tasks": tasks, "ts": time.time(), "at": now.isoformat()})
        return "Записала себе, в Notion внесу, как он оживёт ✨", None
    return confirm_text(items), items


def retry_pending(state, chat_for_notion):
    """Дописать отложенное. → [(chat, html, items)] для отправки; чужое (без чата) — выкидывает."""
    out, keep = [], []
    for p in state.get("pending", []):
        chat = chat_for_notion(p["notion"])
        if chat is None:
            continue
        try:
            at = datetime.datetime.fromisoformat(p["at"])
            items = [create_page(t, p["notion"], p["text"], at) for t in p["tasks"]]
            out.append((chat, confirm_text(items), items))
        except Exception as e:
            print("tasks retry err:", type(e).__name__, str(e)[:200], flush=True)
            keep.append(p)
    state["pending"] = keep
    return out


def oldest_pending_age(state):
    ts = [p["ts"] for p in state.get("pending", [])]
    return time.time() - min(ts) if ts else 0


def digest(notion_user, board=None, full=False, today=None):
    today = today or now_msk().date()
    return digest_text(query_open(notion_user), today, board=board, full=full)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "parse":
        print(json.dumps(parse_tasks(" ".join(sys.argv[2:])), ensure_ascii=False, indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == "digest":
        print(digest(sys.argv[2], full="--full" in sys.argv)[0])
    else:
        print(__doc__)
