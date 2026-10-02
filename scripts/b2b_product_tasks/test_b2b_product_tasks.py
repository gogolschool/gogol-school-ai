import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import b2b_product_tasks as bt  # noqa: E402

NOW = datetime.datetime(2026, 10, 3, 9, 0, tzinfo=datetime.timezone.utc)


def prod(pid=12600, **kw):
    p = {"id": pid, "name": "SoftLine · SoftLine · октябрь 2026", "deleted": False, "status": "Согласовано",
         "price": 340000, "disk": None, "customer": "SoftLine", "contact": "Мария", "tg": "@MF0kina",
         "descr": None, "start": "2026-10-05", "manager": "Сучкова Александра Офис",
         "deal": "SoftLine · сентябрь 2026", "deal_contact": "Мария", "deal_resp": None,
         "client": "SoftLine", "deal_client": "SoftLine", "first_lesson": None}
    p.update(kw)
    return p


# ---------- чистые функции ----------

def test_title_like_katya():
    assert bt.task_title(prod()) == "SoftLine/05.10.26/Мария - b2b"


def test_title_first_lesson_beats_start_and_is_msk():
    p = prod(first_lesson="2026-10-07T22:30:00Z", start="2026-10-01")
    assert bt.task_title(p) == "SoftLine/08.10.26/Мария - b2b"


def test_title_fallbacks():
    assert bt.task_title(prod(customer=None, contact=None, start=None)) == "SoftLine/Мария - b2b"
    assert bt.task_title(prod(customer=None, client=None, deal_client=None)) == \
        "SoftLine · SoftLine · октябрь 2026 - b2b"


@pytest.mark.parametrize("kw,why", [
    ({"deleted": True}, "удалён"),
    ({"status": "Отменено"}, "отменён"),
    ({"name": "Сбер · Тест b2b — x"}, "тестовый"),
    ({"deal": "ТЕСТ цепочки шагов (архив)"}, "тестовый"),
    ({}, None),
])
def test_skip_reason(kw, why):
    assert bt.skip_reason(prod(**kw)) == why


def test_links_to_product_exact_id():
    assert bt.links_to_product("https://ozma.gogol.school/views/crm/b2b_product_form?id=12550", 12550)
    assert not bt.links_to_product("https://ozma.gogol.school/views/crm/b2b_product_form?id=125501", 12550)
    assert not bt.links_to_product("https://x?id=12550", 1255)
    assert not bt.links_to_product(None, 1)


def test_task_properties():
    props = bt.task_properties(prod(disk="https://disk/x"), datetime.date(2026, 10, 3))
    assert props["Дедлайн"]["date"]["start"] == "2026-10-06"
    assert props["Ответственный"]["people"][0]["id"] == bt.KATYA
    assert props["Процессы"]["multi_select"] == [{"name": "b2b клиенты"}]
    assert props[bt.LINK_PROP]["url"].endswith("?id=12600")
    assert props["Сумма"] == {"number": 340000}
    assert props["Ссылка Диск"] == {"url": "https://disk/x"}
    assert props["Phone"] == {"phone_number": "@MF0kina"}


def test_refill_only_empty():
    p = prod(disk="https://disk/x")
    full = {"Сумма": {"number": 1}, "Ссылка Диск": {"url": "https://other"}}
    assert bt.refill_properties(p, full) == {}
    empty = {"Сумма": {"number": None}, "Ссылка Диск": {"url": None}}
    assert set(bt.refill_properties(p, empty)) == {"Сумма", "Ссылка Диск"}


def test_blocks_have_checklist_and_long_descr_cut():
    blocks = bt.task_blocks(prod(descr="я" * 2000))
    todos = [b for b in blocks if b["type"] == "to_do"]
    assert len(todos) == len(bt.CHECKLIST)
    quote = [b for b in blocks if b["type"] == "quote"][0]
    assert len(quote["quote"]["rich_text"][0]["text"]["content"]) == 1501


# ---------- прогон с подменой Озмы и Notion ----------

class FakeNotion:
    def __init__(self, existing=None, task_props=None):
        self.calls, self.existing, self.task_props = [], existing or {}, task_props or {}

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.endswith("/query"):
            pid = int(body["filter"]["url"]["contains"].split("=")[1])
            url = bt.PRODUCT_URL % pid
            return {"results": [{"id": self.existing[pid], "properties": {bt.LINK_PROP: {"url": url}}}]
                    if pid in self.existing else []}
        if method == "POST" and path == "/pages":
            return {"id": "task-%d" % len(self.calls)}
        if method == "GET":
            return {"id": path.split("/")[-1], "properties": self.task_props}
        return {}

    def created(self):
        return [c for c in self.calls if c[0] == "POST" and c[1] == "/pages"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "STATE_FILE", str(tmp_path / "state.json"))
    products = []
    monkeypatch.setattr(bt, "list_products", lambda: list(products))
    fake = FakeNotion()
    monkeypatch.setattr(bt, "notion", fake)
    return products, fake


def test_first_run_marks_all_seen_no_tasks(env):
    products, fake = env
    products += [prod(1), prod(2)]
    log = bt.run(now=NOW)
    assert "первый запуск" in log[0]
    assert fake.calls == []
    assert bt.load_json(bt.STATE_FILE)["seen"] == [1, 2]


def test_new_product_waits_then_creates_once(env):
    products, fake = env
    products.append(prod(1))
    bt.run(now=NOW)
    products.append(prod(2))
    bt.run(now=NOW)                                         # впервые увиден — ждём
    assert fake.created() == []
    bt.run(now=NOW + datetime.timedelta(minutes=10))       # ещё рано
    assert fake.created() == []
    bt.run(now=NOW + datetime.timedelta(minutes=20))
    assert len(fake.created()) == 1
    body = fake.created()[0][2]
    assert body["properties"][bt.LINK_PROP]["url"].endswith("?id=2")
    bt.run(now=NOW + datetime.timedelta(minutes=30))
    assert len(fake.created()) == 1                        # без дублей
    assert "2" not in bt.load_json(bt.STATE_FILE)["pending"]


def test_existing_manual_task_is_adopted(env):
    products, fake = env
    bt.run(now=NOW)
    fake.existing[5] = "manual-task"
    products.append(prod(5))
    bt.run(now=NOW)
    bt.run(now=NOW + datetime.timedelta(minutes=20))
    assert fake.created() == []
    assert bt.load_json(bt.STATE_FILE)["tasks"]["5"]["task"] == "manual-task"


def test_test_product_skipped(env):
    products, fake = env
    bt.run(now=NOW)
    products.append(prod(7, name="Сбер · Тест b2b"))
    bt.run(now=NOW)
    bt.run(now=NOW + datetime.timedelta(minutes=20))
    assert fake.created() == []
    assert 7 in bt.load_json(bt.STATE_FILE)["seen"]


def test_refill_within_3_days_only(env):
    products, fake = env
    bt.run(now=NOW)
    products.append(prod(8, disk=None))
    bt.run(now=NOW)
    bt.run(now=NOW + datetime.timedelta(minutes=20))
    fake.task_props = {"Сумма": {"number": 340000}, "Ссылка Диск": {"url": None}}
    products[-1] = prod(8, disk="https://disk/folder")
    bt.run(now=NOW + datetime.timedelta(hours=1))
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert patches and patches[-1][2]["properties"] == {"Ссылка Диск": {"url": "https://disk/folder"}}
    n = len(fake.calls)
    bt.run(now=NOW + datetime.timedelta(days=4))
    assert len(fake.calls) == n                            # через 3 дня не трогаем


def test_dry_run_writes_nothing(env):
    products, fake = env
    bt.run(now=NOW)
    products.append(prod(9))
    bt.run(now=NOW)
    before = bt.load_json(bt.STATE_FILE)
    log = bt.run(dry=True, now=NOW + datetime.timedelta(minutes=20))
    assert any("создать задачу" in x for x in log)
    assert fake.created() == []
    assert bt.load_json(bt.STATE_FILE) == before
