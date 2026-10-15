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


def test_apply_done_single_without_number(monkeypatch, fake_notion):
    reply, _ = tasks.apply_edit("готово", [dict(x) for x in ONE], TODAY)
    assert reply == "Закрыла ✨"
    assert fake_notion == [("PATCH", "/pages/p1", {"properties": {"Статус": {"status": {"name": "Готово"}}}})]


def test_apply_archive_never_deletes(monkeypatch, fake_notion):
    tasks.apply_edit("удали", [dict(x) for x in ONE], TODAY)
    assert fake_notion[0][2]["properties"] == {"Статус": {"status": {"name": "Архив"}}}
    assert all(c[0] != "DELETE" for c in fake_notion)


def test_apply_asks_which(monkeypatch, fake_notion):
    reply, _ = tasks.apply_edit("готово", [dict(x) for x in TWO], TODAY)
    assert reply.startswith("Какую задачу — 1, 2?")
    assert fake_notion == []


def test_apply_deadline_via_llm(monkeypatch, fake_notion):
    seen = llm_returns(monkeypatch, '{"targets": [2], "action": "deadline", "value": "2026-10-21"}')
    items = [dict(x) for x in TWO]
    reply, items = tasks.apply_edit("2 срок на среду", items, TODAY)
    assert reply == "Перенесла на ср 21.10 (2) ✨"
    assert fake_notion[0] == ("PATCH", "/pages/p2", {"properties": {"Дедлайн": {"date": {"start": "2026-10-21"}}}})
    assert items[1]["deadline"] == "2026-10-21"
    assert "1. А — без срока" in seen[0] and "2. Б — пт 16.10" in seen[0]


def test_apply_deadline_with_time(monkeypatch, fake_notion):
    llm_returns(monkeypatch, '{"targets": [], "action": "deadline", "value": "2026-10-16T15:00"}')
    reply, _ = tasks.apply_edit("на завтра в 15", [dict(x) for x in ONE], TODAY)
    assert reply == "Перенесла на пт 16.10 15:00 ✨"
    assert fake_notion[0][2]["properties"]["Дедлайн"]["date"]["start"] == "2026-10-16T15:00:00+03:00"


def test_apply_rename_and_priority(monkeypatch, fake_notion):
    llm_returns(monkeypatch, '{"targets": [1], "action": "rename", "value": "Новое имя"}')
    reply, items = tasks.apply_edit("переименуй в Новое имя", [dict(x) for x in ONE], TODAY)
    assert reply == "Переименовала в «Новое имя» ✨" and items[0]["title"] == "Новое имя"
    llm_returns(monkeypatch, '{"targets": [1], "action": "priority", "value": "Срочно"}')
    reply, _ = tasks.apply_edit("срочно", [dict(x) for x in ONE], TODAY)
    assert reply == "Поставила приоритет «Срочно» ✨"


@pytest.mark.parametrize("out", ['{"action": "unknown"}', "мусор", RuntimeError("x"),
                                 '{"targets": [1], "action": "deadline", "value": "в среду"}'])
def test_apply_unknown_no_writes(monkeypatch, fake_notion, out):
    llm_returns(monkeypatch, out)
    reply, _ = tasks.apply_edit("что-то непонятное", [dict(x) for x in ONE], TODAY)
    assert reply.startswith("Не поняла")
    assert fake_notion == []


def test_apply_out_of_range_targets(monkeypatch, fake_notion):
    reply, _ = tasks.apply_edit("7 готово", [dict(x) for x in TWO], TODAY)
    assert reply.startswith("Какую задачу")
    assert fake_notion == []


# --- память о сообщениях ---

def test_remember_recall_and_prune(monkeypatch):
    state = {"msgs": {"1": {"ts": 0, "items": []}}}
    tasks.remember(state, 42, [{"id": "p", "url": "u", "title": "Т", "deadline": None, "лишнее": 1}])
    assert tasks.recall(state, 42) == [{"id": "p", "url": "u", "title": "Т", "deadline": None}]
    assert "1" not in state["msgs"]
    assert tasks.recall(state, 999) is None
