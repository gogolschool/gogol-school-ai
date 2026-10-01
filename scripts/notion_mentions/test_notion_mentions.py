import datetime
import json
import os
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import notion_mentions as nm  # noqa: E402

BELLA = "40cc4df7-607d-40cb-9ab3-f3d2004a633a"
KATYA = "d5b39214-66db-4761-8ef7-0447637286d0"
ANYA = "e1d861ef-12c6-4f3a-804f-bb8a64bbb455"
SASHA = "927f0355-35a0-472b-af91-16ef68a9e5b4"
PRODUCT = "3e3612c7-62af-81bf-ad80-c763613bada2"
NOW = datetime.datetime(2026, 9, 29, 15, 0, tzinfo=datetime.timezone.utc)


def mention(uid, name="@X"):
    return {"type": "mention", "mention": {"type": "user", "user": {"object": "user", "id": uid}},
            "plain_text": name}


def text(s):
    return {"type": "text", "text": {"content": s}, "plain_text": s}


def comment(cid, uids, created="2026-09-29T12:00:00.000Z", author=SASHA, page=PRODUCT):
    rich = []
    for u in uids:
        rich += [mention(u), text(" заполни поля ")]
    return {"id": cid, "discussion_id": "disc-" + cid, "created_time": created,
            "created_by": {"object": "user", "id": author},
            "parent": {"type": "page_id", "page_id": page}, "rich_text": rich}


def product(pid=PRODUCT, title="ИНТ Миххалёв Шадрин"):
    return {"id": pid, "url": "https://www.notion.so/" + pid.replace("-", ""),
            "properties": {"Название программы (внутр)": {"type": "title", "title": [
                {"plain_text": title}]}}}


# ---------- Task 1: чистые функции ----------

def test_watched_mentions_only_watched_and_unique():
    c = comment("c1", [BELLA, ANYA, BELLA, KATYA])
    assert nm.watched_mentions(c) == [BELLA, KATYA]


def test_watched_mentions_ignores_page_mentions():
    c = {"rich_text": [{"type": "mention", "mention": {"type": "page", "page": {"id": "p"}}}]}
    assert nm.watched_mentions(c) == []


def test_msk_date_crosses_midnight():
    assert nm.msk_date("2026-09-29T22:30:00.000Z") == "2026-09-30"
    assert nm.msk_date("2026-09-29T20:59:00.000Z") == "2026-09-29"


def test_comment_link():
    assert nm.comment_link(PRODUCT, "3e3612c7-62af-8131-bea0-001c735454bd") == (
        "https://app.notion.com/p/3e3612c762af81bfad80c763613bada2?d=3e3612c762af8131bea0001c735454bd")


def test_product_title_empty():
    assert nm.product_title({"properties": {}}) == "Без названия"


def test_task_properties():
    p = nm.task_properties("ИНАЧЕ 5", BELLA, "2026-09-29")
    assert p["Задачи"]["title"][0]["text"]["content"] == "ИНАЧЕ 5"
    assert p["Ответственный"] == {"people": [{"object": "user", "id": BELLA}]}
    assert p["Процессы"] == {"multi_select": [{"name": "Продукты"}]}
    assert p["Дедлайн"] == {"date": {"start": "2026-09-29"}}
    assert p["Status"] == {"status": {"name": "Не начато"}}


def test_mention_blocks():
    blocks = nm.mention_blocks(comment("c1", [BELLA]), "Саша Сучкова")
    head, quote = blocks
    head_text = "".join(x["text"]["content"] for x in head["paragraph"]["rich_text"])
    assert "29.09.2026 15:00" in head_text and "Саша Сучкова" in head_text
    assert head["paragraph"]["rich_text"][-1]["text"]["link"]["url"].startswith(
        "https://app.notion.com/p/3e3612c762af81bfad80c763613bada2?d=")
    assert quote["quote"]["rich_text"][0]["text"]["content"] == "@X заполни поля"


# ---------- Task 2: исполнитель ----------

class FakeNotion:
    def __init__(self, products, comments, pages=None):
        self.products = products
        self.comments = comments
        self.pages = pages or {}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path.startswith("/databases/"):
            return {"results": self.products, "has_more": False}
        if method == "GET" and path.startswith("/comments"):
            q = urllib.parse.parse_qs(path.split("?", 1)[1])
            return {"results": self.comments.get(q["block_id"][0], []), "has_more": False}
        if method == "GET" and path.startswith("/users/"):
            return {"name": "Саша Сучкова"}
        if method == "POST" and path == "/pages":
            pid = "task-%d" % (len(self.pages) + 1)
            self.pages[pid] = {"id": pid, "archived": False,
                               "properties": {"Status": {"status": {"name": "Не начато"}}}}
            return self.pages[pid]
        if method == "GET" and path.startswith("/pages/"):
            pid = path.split("/")[2]
            if pid not in self.pages:
                raise nm.NotionError(404, "not found")
            return self.pages[pid]
        if method == "PATCH":
            return {}
        raise AssertionError("unexpected call %s %s" % (method, path))

    def of(self, method, prefix):
        return [c for c in self.calls if c[0] == method and c[1].startswith(prefix)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(nm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(nm, "FAILS_FILE", str(tmp_path / "fails.json"))
    return tmp_path


def use(monkeypatch, fake):
    monkeypatch.setattr(nm, "notion", fake)
    return fake


def write_state(env, seen=(), tasks=None):
    (env / "state.json").write_text(json.dumps({"seen": list(seen), "tasks": tasks or {}}))


def read_state(env):
    return json.loads((env / "state.json").read_text())


def test_first_run_marks_seen_without_tasks(env, monkeypatch):
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c1", [BELLA])]}))
    nm.run(now=NOW)
    assert fake.of("POST", "/pages") == []
    assert read_state(env)["seen"] == ["c1"]


def test_new_tag_creates_task(env, monkeypatch):
    write_state(env)
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c1", [BELLA])]}))
    nm.run(now=NOW)
    (_, _, body), = fake.of("POST", "/pages")
    assert body["parent"] == {"database_id": nm.TASKS_DB}
    assert body["properties"]["Ответственный"]["people"][0]["id"] == BELLA
    assert body["properties"]["Задачи"]["title"][0]["text"]["content"] == "ИНТ Миххалёв Шадрин"
    st = read_state(env)
    assert st["tasks"] == {"%s:%s" % (PRODUCT, BELLA): "task-1"}
    assert st["seen"] == ["c1"]


def test_two_watched_in_one_comment_two_tasks(env, monkeypatch):
    write_state(env)
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c1", [BELLA, KATYA])]}))
    nm.run(now=NOW)
    owners = [b["properties"]["Ответственный"]["people"][0]["id"] for _, _, b in fake.of("POST", "/pages")]
    assert owners == [BELLA, KATYA]


def test_repeat_tag_appends_and_moves_deadline(env, monkeypatch):
    write_state(env, seen=["c1"], tasks={"%s:%s" % (PRODUCT, BELLA): "task-1"})
    pages = {"task-1": {"id": "task-1", "archived": False,
                        "properties": {"Status": {"status": {"name": "В работе"}}}}}
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [
        comment("c1", [BELLA]), comment("c2", [BELLA], created="2026-09-29T13:00:00.000Z")]}, pages))
    nm.run(now=NOW)
    assert fake.of("POST", "/pages") == []
    assert len(fake.of("PATCH", "/blocks/task-1/children")) == 1
    (_, _, body), = fake.of("PATCH", "/pages/task-1")
    assert body["properties"] == {"Дедлайн": {"date": {"start": "2026-09-29"}}}


def test_repeat_tag_reopens_done(env, monkeypatch):
    write_state(env, tasks={"%s:%s" % (PRODUCT, BELLA): "task-1"})
    pages = {"task-1": {"id": "task-1", "archived": False,
                        "properties": {"Status": {"status": {"name": "Готово"}}}}}
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c2", [BELLA])]}, pages))
    nm.run(now=NOW)
    (_, _, body), = fake.of("PATCH", "/pages/task-1")
    assert body["properties"]["Status"] == {"status": {"name": "Не начато"}}


def test_archived_task_recreated(env, monkeypatch):
    write_state(env, tasks={"%s:%s" % (PRODUCT, BELLA): "old"})
    pages = {"old": {"id": "old", "archived": True, "properties": {}}}
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c2", [BELLA])]}, pages))
    nm.run(now=NOW)
    assert len(fake.of("POST", "/pages")) == 1
    assert read_state(env)["tasks"]["%s:%s" % (PRODUCT, BELLA)] != "old"


def test_deleted_task_recreated(env, monkeypatch):
    write_state(env, tasks={"%s:%s" % (PRODUCT, BELLA): "gone"})
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c2", [BELLA])]}))
    nm.run(now=NOW)
    assert len(fake.of("POST", "/pages")) == 1


def test_unwatched_tag_ignored_but_seen(env, monkeypatch):
    write_state(env)
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c1", [ANYA])]}))
    nm.run(now=NOW)
    assert fake.of("POST", "/pages") == []
    assert read_state(env)["seen"] == ["c1"]


def test_dry_run_writes_nothing(env, monkeypatch):
    write_state(env)
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [comment("c1", [BELLA])]}))
    out = nm.run(dry=True, now=NOW)
    assert fake.of("POST", "/pages") == [] and fake.of("PATCH", "") == []
    assert read_state(env)["seen"] == []
    assert any("Бэлла" in line and "ИНТ Миххалёв Шадрин" in line for line in out)


def test_backfill_first_run_only_recent(env, monkeypatch):
    fake = use(monkeypatch, FakeNotion([product()], {PRODUCT: [
        comment("old", [BELLA], created="2026-09-20T12:00:00.000Z"),
        comment("new", [KATYA], created="2026-09-28T12:00:00.000Z")]}))
    nm.run(backfill_days=2, now=NOW)
    owners = [b["properties"]["Ответственный"]["people"][0]["id"] for _, _, b in fake.of("POST", "/pages")]
    assert owners == [KATYA]
    assert sorted(read_state(env)["seen"]) == ["new", "old"]


# ---------- Task 3: CLI и алерт ----------

def test_three_failures_alert_once_and_success_resets(env, monkeypatch):
    sent = []
    monkeypatch.setattr(nm, "alert", sent.append)

    def boom(*a, **k):
        raise nm.NotionError(500, "down")
    monkeypatch.setattr(nm, "notion", boom)
    for _ in range(4):
        assert nm.main([]) == 1
    assert len(sent) == 1 and "500" in sent[0]
    monkeypatch.setattr(nm, "notion", FakeNotion([], {}))
    assert nm.main([]) == 0
    assert json.loads((env / "fails.json").read_text())["fails"] == 0


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_notion_retries_transient_server_errors(monkeypatch, code):
    import io
    import urllib.error
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(b"{}"))
        return Resp(b'{"ok": true}')

    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setattr(nm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(nm.time, "sleep", lambda s: None)
    assert nm.notion("GET", "/users/me") == {"ok": True}
    assert len(calls) == 2
