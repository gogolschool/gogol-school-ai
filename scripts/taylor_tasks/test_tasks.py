"""Тесты tasks.py: Notion и claude замоканы. Запуск: python3 -m pytest scripts/taylor_tasks -q"""
import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import tasks  # noqa: E402

TODAY = datetime.date(2026, 10, 15)          # четверг
NOW = datetime.datetime(2026, 10, 15, 14, 30, tzinfo=tasks.MSK)
USER = "24e1c450-c5e1-428a-84c2-145c7193a8a9"


@pytest.fixture
def fake_notion(monkeypatch):
    calls = []

    def notion(method, path, body=None, tries=3):
        calls.append((method, path, body))
        if method == "POST" and path == "/pages":
            n = sum(1 for c in calls if c[1] == "/pages")
            return {"id": "page-%d" % n, "url": "https://www.notion.so/page-%d" % n}
        return {}
    monkeypatch.setattr(tasks, "notion", notion)
    return calls


def llm_returns(monkeypatch, out):
    seen = []

    def llm(prompt):
        seen.append(prompt)
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(tasks, "llm", llm)
    return seen


# --- даты и пользователи ---

@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(tasks, "now_msk", lambda: NOW)


def test_fmt_date():
    assert tasks.fmt_date("2026-10-16") == "пт 16.10"
    assert tasks.fmt_date("2026-10-16T15:00:00+03:00") == "пт 16.10 15:00"
    assert tasks.fmt_date("2024-10-18") == "пт 18.10.24"
    assert tasks.fmt_date(None) == ""


def test_calendar_hint_marks_today():
    h = tasks.calendar_hint(TODAY, 3).splitlines()
    assert h[0] == "четверг 15.10 = 2026-10-15 (сегодня)"
    assert h[1].startswith("пятница 16.10 = 2026-10-16 (завтра)")


def test_parse_users():
    u = tasks.parse_users("123:abc, 456:def:board1 ; bad, :x")
    assert u == {123: {"notion": "abc", "board": None}, 456: {"notion": "def", "board": "board1"}}
    assert tasks.parse_users("") == {}


# --- разбор ---

def test_parse_tasks_json_in_fence(monkeypatch):
    seen = llm_returns(monkeypatch, '```json\n[{"title": "Позвонить Кучеренко про зал", "deadline": "2026-10-16",'
                                    ' "time": null, "from": "Катя", "priority": "Срочно"},'
                                    ' {"title": "Отправить расписание", "deadline": null}]\n```')
    t = tasks.parse_tasks("текст", TODAY)
    assert [x["title"] for x in t] == ["Позвонить Кучеренко про зал", "Отправить расписание"]
    assert t[0]["deadline"] == "2026-10-16" and t[0]["from"] == "Катя" and t[0]["priority"] == "Срочно"
    assert t[1]["deadline"] is None and t[1]["priority"] is None
    assert "2026-10-15" in seen[0] and "четверг" in seen[0]


def test_parse_tasks_sanitizes(monkeypatch):
    llm_returns(monkeypatch, json.dumps([{"title": "x" * 200, "deadline": "16.10", "time": "25:00",
                                          "priority": "очень"}, {"title": "  "}, "мусор"]))
    t = tasks.parse_tasks("т", TODAY)
    assert len(t) == 1
    assert len(t[0]["title"]) == tasks.TITLE_MAX and t[0]["title"].endswith("…")
    assert t[0]["deadline"] is None and t[0]["time"] is None and t[0]["priority"] is None


def test_time_only_with_deadline(monkeypatch):
    llm_returns(monkeypatch, '[{"title": "Созвон", "deadline": null, "time": "15:00"}]')
    assert tasks.parse_tasks("т", TODAY)[0]["time"] is None


@pytest.mark.parametrize("out", [RuntimeError("401"), "не json", "[]"])
def test_parse_tasks_fallback(monkeypatch, out):
    llm_returns(monkeypatch, out)
    t = tasks.parse_tasks("[голосовое] Купить   воду в офис " + "а" * 100, TODAY)
    assert len(t) == 1
    assert t[0]["title"].startswith("Купить воду в офис") and t[0]["title"].endswith("…")
    assert t[0]["deadline"] is None


# --- Notion ---

def test_page_properties_full():
    p = tasks.page_properties({"title": "Т", "deadline": "2026-10-16", "time": "15:30",
                               "priority": "Срочно", "from": None}, USER)
    assert p["Набор"]["title"][0]["text"]["content"] == "Т"
    assert p["Исполнитель"] == {"people": [{"id": USER}]}
    assert p["Статус"] == {"status": {"name": "Не начато"}}
    assert p["Дедлайн"] == {"date": {"start": "2026-10-16T15:30:00+03:00"}}
    assert p["Приоритет"] == {"select": {"name": "Срочно"}}
    assert "Отдел" not in p and "Проект" not in p


def test_page_properties_minimal():
    p = tasks.page_properties(tasks.fallback_task("Т"), USER)
    assert set(p) == {"Набор", "Исполнитель", "Статус"}


def test_page_children_split_and_from():
    b = tasks.page_children({"from": "Катя"}, "x" * 4000, NOW)
    texts = [blk["paragraph"]["rich_text"][0]["text"]["content"] for blk in b]
    assert texts[0] == "От кого: Катя"
    assert texts[1] == "Исходный текст"
    assert all(len(t) <= 2000 for t in texts)
    assert "".join(texts[2:-1]) == "x" * 4000
    assert texts[-1] == "Записано через Тейлор, 15.10.2026 14:30"


def test_capture_creates_pages(monkeypatch, fake_notion):
    llm_returns(monkeypatch, '[{"title": "А", "deadline": "2026-10-16"}, {"title": "Б"}]')
    state = {}
    reply, items = tasks.capture(state, USER, "исходник", NOW)
    assert [c[1] for c in fake_notion] == ["/pages", "/pages"]
    assert fake_notion[0][2]["parent"] == {"database_id": tasks.TASKS_DB}
    assert reply.splitlines()[0] == "Записала ✨"
    assert '1. <a href="https://www.notion.so/page-1">А</a> — до пт 16.10' in reply
    assert "2. " in reply and "— без срока" in reply
    assert items[0]["id"] == "page-1" and items[0]["deadline"] == "2026-10-16"


def test_capture_single_uses_bullet(monkeypatch, fake_notion):
    llm_returns(monkeypatch, '[{"title": "А & <b>"}]')
    reply, _ = tasks.capture({}, USER, "т", NOW)
    assert "• <a href=" in reply and "А &amp; &lt;b&gt;" in reply


def test_capture_notion_down_goes_pending(monkeypatch):
    llm_returns(monkeypatch, '[{"title": "А"}]')

    def boom(*a, **k):
        raise RuntimeError("notion 503")
    monkeypatch.setattr(tasks, "notion", boom)
    state = {}
    reply, items = tasks.capture(state, USER, "исходник", NOW)
    assert items is None and "внесу" in reply
    assert state["pending"][0]["tasks"][0]["title"] == "А"
    assert tasks.oldest_pending_age(state) >= 0


def test_retry_pending(monkeypatch, fake_notion):
    state = {"pending": [
        {"notion": USER, "text": "т", "tasks": [tasks.fallback_task("А")], "ts": 0, "at": NOW.isoformat()},
        {"notion": "чужой", "text": "т", "tasks": [tasks.fallback_task("Б")], "ts": 0, "at": NOW.isoformat()},
    ]}
    out = tasks.retry_pending(state, lambda n: 777 if n == USER else None)
    assert len(out) == 1 and out[0][0] == 777 and "Записала" in out[0][1]
    assert state["pending"] == []


def test_retry_pending_keeps_on_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(tasks, "notion", boom)
    state = {"pending": [{"notion": USER, "text": "т", "tasks": [tasks.fallback_task("А")],
                          "ts": 0, "at": NOW.isoformat()}]}
    assert tasks.retry_pending(state, lambda n: 1) == []
    assert len(state["pending"]) == 1


# --- сводка ---

def items_for_digest():
    return [
        {"id": "o", "url": "u", "title": "Просроченная", "deadline": "2026-10-12"},
        {"id": "t", "url": "u", "title": "Сегодняшняя", "deadline": "2026-10-15T18:00:00.000+03:00"},
        {"id": "s", "url": "u", "title": "Скоро", "deadline": "2026-10-17"},
        {"id": "l", "url": "u", "title": "Потом", "deadline": "2026-11-01"},
        {"id": "n", "url": "u", "title": "Без срока", "deadline": None},
    ]


def test_digest_blocks_and_numbering():
    text, numbered = tasks.digest_text(items_for_digest(), TODAY, board="3f8612c7-62af-8041")
    assert [i["id"] for i in numbered] == ["o", "t", "s"]
    assert "🔴 Просрочено" in text and "1. " in text and "было до пн 12.10" in text
    assert "2. " in text and "Сегодняшняя</a>\n" in text
    assert "3. " in text and "сб 17.10" in text
    assert "Ещё позже: 1, без срока: 1" in text
    assert "https://www.notion.so/3f8612c762af8041" in text


def test_digest_empty_blocks_hidden():
    text, numbered = tasks.digest_text([{"id": "n", "url": "", "title": "Б", "deadline": None}], TODAY)
    assert numbered == []
    assert text == "На сегодня задач со сроком нет ✨ Без срока: 1"


def test_digest_full_lists_everything():
    text, numbered = tasks.digest_text(items_for_digest(), TODAY, full=True)
    assert [i["id"] for i in numbered] == ["o", "t", "s", "l", "n"]
    assert text.startswith("Твои задачи ✨") and "Без срока" in text


def test_digest_full_empty():
    text, numbered = tasks.digest_text([], TODAY, full=True)
    assert text == "Открытых задач нет ✨" and numbered == []


def test_query_open_filter(monkeypatch):
    sent = []

    def notion(method, path, body=None, tries=3):
        sent.append(body)
        return {"results": [{"id": "p1", "url": "u1", "properties": {
            "Набор": {"title": [{"plain_text": "Задача"}]},
            "Дедлайн": {"date": {"start": "2026-10-16"}}}},
            {"id": "p2", "url": "u2", "properties": {"Набор": {"title": []}, "Дедлайн": {"date": None}}}],
            "has_more": False}
    monkeypatch.setattr(tasks, "notion", notion)
    out = tasks.query_open(USER)
    f = sent[0]["filter"]["and"]
    assert f[0] == {"property": "Исполнитель", "people": {"contains": USER}}
    assert {x["status"]["equals"] for x in f[1]["or"]} == set(tasks.OPEN_STATUSES)
    assert out == [{"id": "p1", "url": "u1", "title": "Задача", "deadline": "2026-10-16"},
                   {"id": "p2", "url": "u2", "title": "Без названия", "deadline": None}]


# --- правки ---

@pytest.mark.parametrize("text,targets,action", [
    ("готово", [], "done"), ("2 готово", [2], "done"), ("Готово 1 и 3!", [1, 3], "done"),
    ("сделала", [], "done"), ("удали 2", [2], "archive"), ("не надо", [], "archive"),
])
def test_quick_edit(text, targets, action):
    assert tasks.quick_edit(text) == {"targets": targets, "action": action, "value": None}


@pytest.mark.parametrize("text", ["срок на среду", "2 срок 21.10", "готово к пятнице?"])
def test_quick_edit_skips(text):
    assert tasks.quick_edit(text) is None


ONE = [{"id": "p1", "url": "u", "title": "А", "deadline": None}]
TWO = [{"id": "p1", "url": "u", "title": "А", "deadline": None},
       {"id": "p2", "url": "u", "title": "Б", "deadline": "2026-10-16"}]


def edits_json(edits, tasks_=None, question=None):
    return json.dumps({"edits": edits, "tasks": tasks_ or [], "question": question}, ensure_ascii=False)


def test_apply_done_single_without_number(monkeypatch, fake_notion):
    reply, _ = tasks.apply_edit("готово", [dict(x) for x in ONE], TODAY)
    assert reply == 'Закрыла ✨\n• <a href="u">А</a>'
    assert fake_notion == [("PATCH", "/pages/p1", {"properties": {"Статус": {"status": {"name": "Готово"}}}})]


def test_apply_archive_never_deletes(monkeypatch, fake_notion):
    tasks.apply_edit("удали", [dict(x) for x in ONE], TODAY)
    assert fake_notion[0][2]["properties"] == {"Статус": {"status": {"name": "Архив"}}}
    assert all(c[0] != "DELETE" for c in fake_notion)


def test_apply_asks_which(monkeypatch, fake_notion):
    reply, _ = tasks.apply_edit("готово", [dict(x) for x in TWO], TODAY)
    assert reply.startswith("Какую задачу поправить?")
    assert fake_notion == []


def test_apply_deadline_via_llm(monkeypatch, fake_notion):
    seen = llm_returns(monkeypatch, edits_json([{"targets": ["A2"], "action": "deadline", "value": "2026-10-21"}]))
    items = [dict(x) for x in TWO]
    reply, items = tasks.apply_edit("2 срок на среду", items, TODAY)
    assert reply == 'Перенесла на ср 21.10 ✨\n• <a href="u">Б</a>'
    assert fake_notion[0] == ("PATCH", "/pages/p2", {"properties": {"Дедлайн": {"date": {"start": "2026-10-21"}}}})
    assert items[1]["deadline"] == "2026-10-21"
    assert "1. А — без срока" in seen[0] and "2. Б — пт 16.10" in seen[0]
    assert "ОТВЕТИЛ на сообщение A" in seen[0]


def test_apply_deadline_with_time(monkeypatch, fake_notion):
    llm_returns(monkeypatch, edits_json([{"targets": [], "action": "deadline", "value": "2026-10-16T15:00"}]))
    reply, _ = tasks.apply_edit("на завтра в 15", [dict(x) for x in ONE], TODAY)
    assert reply.startswith("Перенесла на пт 16.10 15:00 ✨")
    assert fake_notion[0][2]["properties"]["Дедлайн"]["date"]["start"] == "2026-10-16T15:00:00+03:00"


def test_apply_rename_and_priority(monkeypatch, fake_notion):
    llm_returns(monkeypatch, edits_json([{"targets": [1], "action": "rename", "value": "Новое имя"}]))
    reply, items = tasks.apply_edit("переименуй в Новое имя", [dict(x) for x in ONE], TODAY)
    assert reply == 'Переименовала ✨\n• <a href="u">Новое имя</a>' and items[0]["title"] == "Новое имя"
    llm_returns(monkeypatch, edits_json([{"targets": [1], "action": "priority", "value": "Срочно"}]))
    reply, _ = tasks.apply_edit("срочно", [dict(x) for x in ONE], TODAY)
    assert reply.startswith("Поставила приоритет «Срочно» ✨")


@pytest.mark.parametrize("out", ['{"edits": [{"action": "unknown"}]}', "мусор", RuntimeError("x"),
                                 '{"edits": [{"targets": [1], "action": "deadline", "value": "в среду"}]}',
                                 '{"edits": [], "tasks": []}'])
def test_apply_unknown_no_writes(monkeypatch, fake_notion, out):
    llm_returns(monkeypatch, out)
    reply, _ = tasks.apply_edit("что-то непонятное", [dict(x) for x in ONE], TODAY)
    assert reply.startswith("Не поняла")
    assert fake_notion == []


def test_apply_out_of_range_targets(monkeypatch, fake_notion):
    reply, _ = tasks.apply_edit("7 готово", [dict(x) for x in TWO], TODAY)
    assert reply.startswith("Какую задачу")
    assert fake_notion == []


# --- случаи из переписки с Валей (17 и 20.10) ---

def test_strip_name():
    assert tasks.strip_name("Тейлор, убери эту задачу") == "убери эту задачу"
    assert tasks.strip_name("тейлор убери") == "убери"
    assert tasks.strip_name("убери Тейлор") == "убери Тейлор"


def test_reply_with_name_is_edit(monkeypatch, fake_notion):
    """«Тейлор, убери эту задачу» ответом на подтверждение — архив, а не беседа про сайт."""
    seen = llm_returns(monkeypatch, edits_json([{"targets": ["A1"], "action": "archive", "value": None}]))
    reply, _ = tasks.process({}, USER, "Тейлор, убери эту задачу", [tasks.make_block("A", [dict(x) for x in ONE])],
                             now=NOW, is_reply=True)
    assert reply.startswith("Убрала в архив ✨")
    assert "Тейлор" not in seen[0].split("<<<")[1].split(">>>")[0]
    assert fake_notion[0][2]["properties"] == {"Статус": {"status": {"name": "Архив"}}}


def test_reply_and_this_one_delete(monkeypatch, fake_notion):
    """«и эту удали» — не шаблон, решает модель; одна задача в сообщении — она."""
    llm_returns(monkeypatch, edits_json([{"targets": [], "action": "archive", "value": None}]))
    reply, _ = tasks.process({}, USER, "и эту удали", [tasks.make_block("A", [dict(x) for x in ONE])],
                             now=NOW, is_reply=True)
    assert reply.startswith("Убрала в архив ✨")


def test_merge_two_into_one(monkeypatch, fake_notion):
    """«вот тут это одна задача» на «1. Проверить пробные 2. Согласовать новые»."""
    items = [{"id": "p1", "url": "u1", "title": "Проверить пробные занятия", "deadline": None},
             {"id": "p2", "url": "u2", "title": "Согласовать новые занятия", "deadline": "2026-10-20"}]
    llm_returns(monkeypatch, edits_json([{"targets": ["A1", "A2"], "action": "merge",
                                          "value": "Проверить пробные занятия: нужно ли согласовать новые"}]))
    b = tasks.make_block("A", items)
    reply, new = tasks.process({}, USER, "вот тут это одна задача", [b], now=NOW, is_reply=True)
    assert reply.startswith("Склеила в одну ✨") and new is None
    assert fake_notion[0] == ("PATCH", "/pages/p1", {"properties": {
        "Набор": {"title": [{"text": {"content": "Проверить пробные занятия: нужно ли согласовать новые"}}]},
        "Дедлайн": {"date": {"start": "2026-10-20"}}}})
    assert fake_notion[1] == ("PATCH", "/pages/p2", {"properties": {"Статус": {"status": {"name": "Архив"}}}})
    assert b["items"][0]["title"].startswith("Проверить пробные занятия: нужно")


def test_complaint_without_reply_is_not_a_task(monkeypatch, fake_notion):
    """«ты записала это как 2 задачи, а это 1 задача» без ответа — склейка последнего подтверждения."""
    items = [{"id": "p1", "url": "u1", "title": "Проверить пробные занятия", "deadline": None},
             {"id": "p2", "url": "u2", "title": "Согласовать новые занятия", "deadline": None}]
    llm_returns(monkeypatch, edits_json([{"targets": [], "action": "merge", "value": "Проверить пробные"}]))
    reply, new = tasks.process({}, USER, "ты записала это как 2 задачи, а это 1 задача",
                               [tasks.make_block("A", items)], now=NOW)
    assert reply.startswith("Склеила в одну") and new is None
    assert all(c[1] != "/pages" for c in fake_notion)          # новых страниц нет


def test_many_dones_without_reply(monkeypatch, fake_notion):
    """«1 готово / 2 готово / 3 готово / 4 готово» после сводки — закрыть четыре, не новая задача."""
    items = [{"id": "p%d" % i, "url": "u%d" % i, "title": "Т%d" % i, "deadline": None} for i in range(1, 6)]
    seen = llm_returns(monkeypatch, edits_json([{"targets": ["1", "2", "3", "4"], "action": "done", "value": None}]))
    blocks = [tasks.make_block("A", items, mid="98", label="утренняя сводка, 21.10 10:00, самое свежее")]
    reply, new = tasks.process({}, USER, "1 готово\n2 готово\n3 готово\n4 готово", blocks, now=NOW)
    assert new is None and reply.count("• ") == 4
    assert [c[1] for c in fake_notion] == ["/pages/p1", "/pages/p2", "/pages/p3", "/pages/p4"]
    assert "Блок A — утренняя сводка" in seen[0]


def test_new_task_and_edit_in_one_message(monkeypatch, fake_notion):
    llm_returns(monkeypatch, edits_json([{"targets": ["A1"], "action": "done", "value": None}],
                                        [{"title": "Позвонить Кате", "deadline": None}]))
    reply, new = tasks.process({}, USER, "1 готово, и ещё позвонить Кате",
                               [tasks.make_block("A", [dict(x) for x in ONE])], now=NOW)
    assert reply.startswith("Закрыла ✨") and "Записала ✨" in reply
    assert new[0]["title"] == "Позвонить Кате"


def test_thanks_is_silent(monkeypatch, fake_notion):
    llm_returns(monkeypatch, edits_json([]))
    reply, new = tasks.process({}, USER, "спасибо", [tasks.make_block("A", [dict(x) for x in ONE])], now=NOW)
    assert reply == "" and new is None and fake_notion == []


def test_llm_down_without_reply_keeps_task(monkeypatch, fake_notion):
    llm_returns(monkeypatch, RuntimeError("x"))
    reply, new = tasks.process({}, USER, "Позвонить Кате", [], now=NOW)
    assert new and new[0]["title"] == "Позвонить Кате"


def test_question_passed_through(monkeypatch, fake_notion):
    llm_returns(monkeypatch, edits_json([], question="Какую закрыть — «А» или «Б»?"))
    reply, _ = tasks.process({}, USER, "закрой её", [tasks.make_block("A", [dict(x) for x in TWO])], now=NOW)
    assert reply == "Какую закрыть — «А» или «Б»?" and fake_notion == []


def test_link_block_from_memory_and_notion(monkeypatch):
    state = {"msgs": {"5": {"ts": 1, "items": [
        {"id": "3ff612c7-62af-81df-9b45-fde6f6feb4ff", "url": "x", "title": "1 готово 2 готово", "deadline": None}]}}}
    calls = []

    def notion(method, path, body=None, tries=3):
        calls.append(path)
        if path.endswith("aaaa"):
            return {"id": "p-aaaa", "url": "ua", "parent": {"database_id": "aefa741e-a95f-49da-9381-e9cbaba55c81"},
                    "properties": {"Набор": {"title": [{"plain_text": "Из Notion"}]}}}
        return {"id": "p-x", "parent": {"database_id": "другая"}}
    monkeypatch.setattr(tasks, "notion", notion)
    text = ("https://app.notion.com/p/1-2-3-4-3ff612c762af81df9b45fde6f6feb4ff и "
            "https://www.notion.so/Zadacha-" + "1" * 28 + "aaaa и https://www.notion.so/" + "2" * 32)
    b = tasks.link_block(state, text)
    assert b["key"] == "L" and [it["title"] for it in b["items"]] == ["1 готово 2 готово", "Из Notion"]
    assert len(calls) == 2                       # из памяти — без запроса; чужая база отброшена


def test_recent_blocks_order_and_window(monkeypatch):
    now = 1_000_000.0
    item = [{"id": "p", "url": "u", "title": "Т", "deadline": None}]
    state = {"msgs": {
        "1": {"ts": now - 3600, "uid": 7, "kind": "digest", "items": item},
        "2": {"ts": now - 60, "uid": 7, "kind": "confirm", "items": item},
        "3": {"ts": now - 30, "uid": 8, "kind": "confirm", "items": item},       # чужое
        "4": {"ts": now - 20 * 3600, "uid": 7, "kind": "confirm", "items": item},  # старое
        "5": {"ts": now - 10, "kind": "digest", "items": item},                 # без uid
    }}
    bl = tasks.recent_blocks(state, 7, now_ts=now)
    assert [b["mid"] for b in bl] == ["2", "1"] and bl[0]["key"] == "A"
    assert "самое свежее" in bl[0]["label"] and "подтверждение" in bl[0]["label"]
    assert [b["mid"] for b in tasks.recent_blocks(state, 7, only_user=True, now_ts=now)] == ["5", "2", "1"]


# --- память о сообщениях ---

def test_remember_recall_and_prune(monkeypatch):
    state = {"msgs": {"1": {"ts": 0, "items": []}}}
    tasks.remember(state, 42, [{"id": "p", "url": "u", "title": "Т", "deadline": None, "лишнее": 1}])
    assert tasks.recall(state, 42) == [{"id": "p", "url": "u", "title": "Т", "deadline": None}]
    assert "1" not in state["msgs"]
    assert tasks.recall(state, 999) is None


def test_remember_keeps_ts_uid_kind_on_update(monkeypatch):
    state = {}
    tasks.remember(state, 42, [{"id": "p", "title": "Т"}], uid=7, kind="digest")
    ts = state["msgs"]["42"]["ts"]
    tasks.remember(state, 42, [{"id": "p", "title": "Новое"}])
    m = state["msgs"]["42"]
    assert (m["ts"], m["uid"], m["kind"], m["items"][0]["title"]) == (ts, 7, "digest", "Новое")
