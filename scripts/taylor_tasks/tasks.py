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
MODEL = "sonnet"         # haiku путал «одна задача на две строки» и не понимал «и эту удали»
LLM_TIMEOUT = 120
TITLE_MAX = 80
SOON_DAYS = 3            # «ближайшие N дней» в сводке
MSG_KEEP_DAYS = 30       # сколько помним, какие страницы стоят за номерами в сообщении бота
FULL_LIST_MAX = 40       # строк в «что у меня»
CONTEXT_HOURS = 18       # сообщение без ответа может править задачи из последних сообщений бота…
CONTEXT_MSGS = 3         # …не старше N часов и не больше N штук
BLOCK_KEYS = "ABC"
LINK_KEY = "L"           # задачи, на которые сотрудник прислал ссылку Notion

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
    """Один вызов модели без инструментов. Возвращает stdout или кидает исключение."""
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
    cmd = [CLAUDE, "-p", prompt, "--model", MODEL, "--tools", "", "--strict-mcp-config",
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
{task_rules}

Сообщения:
<<<
{text}
>>>"""

TASK_RULES = """- несколько независимых дел — несколько элементов; одно дело с подробностями — один элемент;
- несколько строк про одно дело — ОДНА задача, даже без знаков препинания: вторая строка уточняет первую
  («проверить пробные занятия / нужно ли согласовать новые» = «Проверить пробные занятия: нужно ли
  согласовать новые»); делить только явно разные дела — нумерованный список, разные темы или люди;
- срок только если он назван: «завтра», «в пятницу», «до 20-го», «к концу недели» (= ближайшая пятница);
  «до пятницы» = ближайшая пятница, если сегодня пятница — сегодня; «на следующей неделе» без дня = понедельник;
  ничего не сказано про срок — null, не выдумывай;
- time — только если названо время;
- from — имя из текста («Катя попросила…») или из пометки «Переслано от»; иначе null;
- priority «Срочно» — если «срочно», «горит», «asap», «прямо сейчас»; «Высокий» — если «важно», «в приоритете»; иначе null;
- пиши по-русски, сохраняй имена и названия как в тексте."""


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
                                 calendar=calendar_hint(today), task_rules=TASK_RULES, text=text)
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


# ---------- правки: что человек хочет — новые задачи или поправить записанные ----------

UNDERSTAND_PROMPT = """Ты — Тейлор, бот-записная книжка задач. Сотрудник школы пишет тебе в личку. Пойми, чего он хочет:
записать НОВЫЕ задачи или ПОПРАВИТЬ уже записанные (закрыть, удалить, перенести срок, переименовать,
поменять приоритет, склеить несколько в одну). В одном сообщении может быть и то и другое.
Сегодня {today} ({weekday}), часовой пояс Москва. Ближайшие даты:
{calendar}

Записанные задачи, о которых может идти речь (ключ задачи = буква блока + номер строки, например A2):
{blocks}
{reply_note}
Сообщение сотрудника:
<<<
{text}
>>>

Верни ТОЛЬКО JSON-объект, без пояснений:
{{"edits": [{{"targets": ["A2", ...], "action": "done" | "archive" | "deadline" | "rename" | "priority" | "merge",
             "value": см. ниже}}],
  "tasks": [{{"title": "...", "deadline": "YYYY-MM-DD" или null, "time": "HH:MM" или null,
             "from": "..." или null, "priority": "Срочно" | "Высокий" | null}}],
  "question": null или короткий вопрос, если правда непонятно, о какой задаче речь}}

Правки:
- правка — когда речь про уже записанное: «готово», «2 готово», «удали», «и эту удали», «убери эту задачу»,
  «перенеси на среду», «переименуй в …», «это одна задача», «ты записала это как 2 задачи, а это одна»;
  обращение «Тейлор, …» смысла не меняет;
- несколько правок в одном сообщении («1 готово / 2 готово / 3 готово», «1 готово, 3 на пятницу») — несколько
  элементов edits или один с несколькими targets;
- номер без буквы — из блока A (это сообщение, на которое ответили, или самое свежее); номер — это
  порядковый номер строки в блоке, а не цифры из названия задачи;
- done: «готово», «сделала», «сделано», «закрой»; archive: «удали», «убери», «не надо», «отмена», «в архив»,
  «записала лишнее / по ошибке»;
- deadline: value "YYYY-MM-DD" или "YYYY-MM-DDTHH:MM"; rename: value — новое название;
  priority: value "Срочно" | "Высокий" | "Средний" | "Низкий";
- merge — «это одна задача», «склей», «объедини»: targets — склеиваемые задачи (если не названы — все строки
  блока A), value — общее название как действие, до 80 символов, без срока;
- «эту», «её», «вот эту» в ответе на сообщение с одной задачей — эта задача; ссылка на задачу Notion — блок L;
- комментарий о том, как бот записал задачи (жалоба, уточнение, «ты неправильно записала»), — это правка
  или вопрос, НИКОГДА не новая задача.

Новые задачи (tasks) — поручения и дела, которых нет в блоках. Пересланное чужое сообщение — почти всегда
новая задача. title — коротко, как действие («Позвонить …», «Отправить …»), до 80 символов, без срока и дат.
{task_rules}

Не уверена, к какой задаче правка, — question, а edits пустой. Ничего не выдумывай. Если ни правки, ни задачи
нет (просто «спасибо», «ок») — всё пустое."""

REPLY_NOTE = ("\nСотрудник ОТВЕТИЛ на сообщение A — почти наверняка это правка задач из него; "
              "новую задачу создавай, только если в тексте явно новое поручение.\n")

_NUM = r"\d{1,2}(?:\s*(?:,|и)\s*\d{1,2})*"
_DONE = r"(?:готово|сделала?|сделано|выполнено|закрыла?|закрой|закрыть|done)"
_ARCH = r"(?:удали(?:ть)?|не надо|отмена|отмени(?:ть)?|в архив|архив)"
_NAME = re.compile(r"^\s*(тейлор|свифт)\w*[\s,!:.\-—]*", re.I)
_PAGE_ID = re.compile(r"(?:notion\.(?:so|site)|app\.notion\.com)/\S*?([0-9a-f]{32})(?![0-9a-f])", re.I)


def _nums(s):
    return [int(x) for x in re.findall(r"\d{1,2}", s or "")]


def strip_name(text):
    """«Тейлор, убери эту задачу» → «убери эту задачу»."""
    return _NAME.sub("", text or "", count=1).strip()


def quick_edit(text):
    """Частые формы без LLM: «готово», «2 готово», «готово 1 и 3», «удали 2»."""
    t = text.strip().lower().rstrip(".!✨ ")
    for word, action in ((_DONE, "done"), (_ARCH, "archive")):
        m = re.fullmatch(r"(%s)?\s*%s\s*(%s)?" % (_NUM, word, _NUM), t)
        if m:
            return {"targets": _nums(m.group(1)) + _nums(m.group(2)), "action": action, "value": None}
    return None


def make_block(key, items, mid=None, label=""):
    return {"key": key, "mid": mid, "label": label, "items": items}


def _block_text(b):
    head = "Блок %s%s:" % (b["key"], (" — " + b["label"]) if b.get("label") else "")
    rows = ["%d. %s — %s" % (i, it["title"], fmt_date(it.get("deadline")) or "без срока")
            for i, it in enumerate(b["items"], 1)]
    return "\n".join([head] + rows)


def _norm_edit(d):
    if not isinstance(d, dict):
        return None
    t = d.get("targets") or []
    if not isinstance(t, list):
        t = [t]
    return {"targets": [str(x).strip().upper() for x in t if str(x).strip()],
            "action": d.get("action") or "unknown", "value": d.get("value")}


def understand(text, blocks, today=None, is_reply=False):
    """Одно сообщение → {"edits", "tasks", "question"}. Кидает исключение, если модель не ответила JSON."""
    today = today or now_msk().date()
    prompt = UNDERSTAND_PROMPT.format(
        today=today.isoformat(), weekday=WD_FULL[today.weekday()], calendar=calendar_hint(today),
        blocks="\n\n".join(_block_text(b) for b in blocks) or "(записанных задач рядом нет)",
        reply_note=REPLY_NOTE if is_reply else "", task_rules=TASK_RULES, text=text)
    d = extract_json(llm(prompt))
    if isinstance(d, list):          # модель вернула просто список задач
        d = {"tasks": d}
    if not isinstance(d, dict):
        raise ValueError("не объект")
    edits = [e for e in (_norm_edit(x) for x in (d.get("edits") or [])) if e]
    new = [t for t in (normalize_task(x) for x in (d.get("tasks") or [])) if t]
    q = d.get("question")
    return {"edits": edits, "tasks": new, "question": q.strip() if isinstance(q, str) and q.strip() else None}


def resolve(target, blocks):
    """'A2' / '2' / 2 → (block, index) или None. Номер без буквы — из первого блока."""
    s = str(target).strip().upper()
    m = re.fullmatch(r"([A-Z]?)\s*(\d{1,2})", s)
    if not m or not blocks:
        return None
    key = m.group(1) or blocks[0]["key"]
    for b in blocks:
        if b["key"] == key:
            n = int(m.group(2))
            return (b, n - 1) if 1 <= n <= len(b["items"]) else None
    return None


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
            return {"Набор": {"title": [{"text": {"content": v}}]}}, "Переименовала"
        return None, "Не поняла новое название."
    if action == "priority":
        if value in PRIORITIES:
            return {"Приоритет": {"select": {"name": value}}}, "Поставила приоритет «%s»" % value
        return None, "Не поняла, какой приоритет поставить."
    return None, "Не поняла, что поменять. " + EDIT_HELP


def _apply_props(item, props):
    update_page(item["id"], props)
    if "Дедлайн" in props:
        item["deadline"] = props["Дедлайн"]["date"]["start"]
    if "Набор" in props:
        item["title"] = props["Набор"]["title"][0]["text"]["content"]


def _bullets(phrase, items):
    return "\n".join([phrase + " ✨"] + ["• " + _link(it) for it in items])


def _merge(picked, value):
    """Склейка: первая задача остаётся (новое название, самый ранний срок), остальные — в архив."""
    keep, rest = picked[0], picked[1:]
    title = re.sub(r"\s+", " ", str(value or "")).strip()[:TITLE_MAX] or keep["title"]
    props = {"Набор": {"title": [{"text": {"content": title}}]}}
    dls = sorted(it["deadline"] for it in picked if it.get("deadline"))
    if dls and not keep.get("deadline"):
        props["Дедлайн"] = {"date": {"start": dls[0]}}
    _apply_props(keep, props)
    for it in rest:
        update_page(it["id"], {"Статус": {"status": {"name": "Архив"}}})
    return "Склеила в одну ✨\n• " + _link(keep)


def run_edits(edits, blocks):
    """Применить правки к задачам из блоков (items меняются на месте). → (строки ответа, была ли ошибка)."""
    out = []
    for e in edits:
        picked, seen = [], set()
        for t in e["targets"]:
            r = resolve(t, blocks)
            if r and id(r[0]["items"][r[1]]) not in seen:
                seen.add(id(r[0]["items"][r[1]]))
                picked.append(r[0]["items"][r[1]])
        if not picked and blocks and len(blocks[0]["items"]) == 1 and e["action"] != "merge":
            picked = [blocks[0]["items"][0]]
        if e["action"] == "merge" and not picked and blocks:
            picked = list(blocks[0]["items"])
        if not picked:
            out.append(html.escape("Какую задачу поправить? Напиши с номером, например «2 готово».", quote=False))
            continue
        try:
            if e["action"] == "merge":
                if len(picked) < 2:
                    out.append("Тут одна задача — склеивать нечего ✨")
                    continue
                out.append(_merge(picked, e["value"]))
                continue
            props, phrase = edit_props(e["action"], e["value"])
            if props is None:
                out.append(html.escape(phrase, quote=False))
                continue
            for it in picked:
                _apply_props(it, props)
            out.append(_bullets(html.escape(phrase, quote=False), picked))
        except Exception as ex:
            print("tasks edit notion err:", type(ex).__name__, str(ex)[:300], flush=True)
            out.append("⚠️ Не достучалась до Notion — повтори правку через пару минут.")
    return out


def apply_edit(text, items, today=None):
    """Ответ на сообщение бота с задачами. → (html-ответ, обновлённые items)."""
    reply, _ = process({}, None, text, [make_block("A", items)], today=today, is_reply=True)
    return reply, items


# ---------- состояние ----------

def remember(state, message_id, items, uid=None, kind=None):
    msgs = state.setdefault("msgs", {})
    old = msgs.get(str(message_id)) or {}
    msgs[str(message_id)] = {"ts": old.get("ts") or time.time(),
                             "uid": uid if uid is not None else old.get("uid"),
                             "kind": kind or old.get("kind"),
                             "items": [{k: it.get(k) for k in ("id", "url", "title", "deadline")}
                                       for it in items]}
    cutoff = time.time() - MSG_KEEP_DAYS * 86400
    for k in [k for k, v in msgs.items() if v.get("ts", 0) < cutoff]:
        msgs.pop(k, None)


def recall(state, message_id):
    m = state.get("msgs", {}).get(str(message_id))
    return m["items"] if m else None


KIND_LABEL = {"digest": "утренняя сводка", "list": "список «что у меня»", "confirm": "подтверждение «Записала»"}


def recent_blocks(state, uid, only_user=False, now_ts=None):
    """Последние сообщения бота с задачами этому человеку — контекст для сообщения без ответа.
    Старые записи без uid берём, только если человек с задачами один (only_user)."""
    now_ts = now_ts or time.time()
    rows = [(v["ts"], mid, v) for mid, v in state.get("msgs", {}).items()
            if v.get("items") and now_ts - v.get("ts", 0) <= CONTEXT_HOURS * 3600
            and (v.get("uid") == uid or (v.get("uid") is None and only_user))]
    rows.sort(key=lambda r: r[0], reverse=True)
    out = []
    for key, (ts, mid, v) in zip(BLOCK_KEYS, rows[:CONTEXT_MSGS]):
        label = "%s, %s" % (KIND_LABEL.get(v.get("kind"), "сообщение бота"),
                            datetime.datetime.fromtimestamp(ts, MSK).strftime("%d.%m %H:%M"))
        if key == BLOCK_KEYS[0]:
            label += ", самое свежее"
        out.append(make_block(key, v["items"], mid=mid, label=label))
    return out


def page_ids(text):
    return [m.group(1).lower() for m in _PAGE_ID.finditer(text or "")]


def link_block(state, text):
    """Ссылки на задачи Notion в тексте → блок L (сначала ищем в памяти, потом спрашиваем Notion)."""
    ids, items = page_ids(text), []
    if not ids:
        return None
    known = {}
    for v in state.get("msgs", {}).values():
        for it in v.get("items", []):
            known[(it.get("id") or "").replace("-", "")] = it
    for pid in dict.fromkeys(ids):
        it = known.get(pid)
        if it is None:
            try:
                p = notion("GET", "/pages/" + pid)
                if (p.get("parent") or {}).get("database_id", "").replace("-", "") != TASKS_DB:
                    continue
                pr = p.get("properties", {})
                title = "".join(x.get("plain_text", "") for x in (pr.get("Набор", {}).get("title") or []))
                it = {"id": p["id"], "url": p.get("url") or "", "title": title.strip() or "Без названия",
                      "deadline": ((pr.get("Дедлайн") or {}).get("date") or {}).get("start")}
            except Exception as e:
                print("tasks link err:", type(e).__name__, str(e)[:200], flush=True)
                continue
        items.append(dict(it))
    return make_block(LINK_KEY, items, label="задачи по ссылкам из сообщения") if items else None


# ---------- сценарии ----------

def _create(state, notion_user, tasks, text, now):
    try:
        return [create_page(t, notion_user, text, now) for t in tasks]
    except Exception as e:
        print("tasks notion err:", type(e).__name__, str(e)[:300], flush=True)
        state.setdefault("pending", []).append(
            {"notion": notion_user, "text": text, "tasks": tasks, "ts": time.time(), "at": now.isoformat()})
        return None


def process(state, notion_user, text, blocks, now=None, today=None, is_reply=False):
    """Сообщение (пачка) → правки задач из blocks и/или новые задачи.
    blocks меняются на месте (бот перезапоминает их по mid). → (html-ответ, новые items или None)."""
    now = now or now_msk()
    today = today or now.date()
    blocks = [b for b in blocks if b and b.get("items")]
    text = strip_name(text) if is_reply else text
    q = quick_edit(text) if is_reply and len(blocks) == 1 else None
    if q:
        u = {"edits": [q], "tasks": [], "question": None}
    else:
        try:
            u = understand(text, blocks, today, is_reply)
        except Exception as e:
            print("tasks understand err:", type(e).__name__, str(e)[:200], flush=True)
            if is_reply or not notion_user:
                return "Не поняла, что поменять. " + html.escape(EDIT_HELP, quote=False), None
            u = {"edits": [], "tasks": [fallback_task(text)], "question": None}   # задачу не теряем
    parts = run_edits(u["edits"], blocks) if u["edits"] else []
    items = None
    if u["tasks"] and notion_user:
        items = _create(state, notion_user, u["tasks"], text, now)
        parts.append(confirm_text(items) if items is not None
                     else "Записала себе, в Notion внесу, как он оживёт ✨")
    if u["question"]:
        parts.append(html.escape(u["question"], quote=False))
    if not parts:
        if not is_reply:
            return "", None           # «спасибо», «ок» — молчим
        parts.append("Не поняла, что сделать. " + html.escape(EDIT_HELP, quote=False))
    return "\n\n".join(parts), items


def capture(state, notion_user, text, now=None):
    """Пачка без контекста → задачи в Notion. → (html-ответ, items) ; items=None, если ушло в pending."""
    now = now or now_msk()
    items = _create(state, notion_user, parse_tasks(text, now.date()), text, now)
    if items is None:
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
