# Папки B2B на Яндекс.Диске — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Агенты `/b2b` и Джангир сами создают на Яндекс.Диске папки `b2b - корпоративные клиенты/{бренд}/{сделка (№id)}/документооборот` и пишут ссылки в Озму.

**Architecture:** В Disk MCP (`~/YandexDiskMCP`, отдельный git-репозиторий без remote) добавляется один тул `create_folder`, работающий только внутри белого списка. MCP остаётся на Маке и дополнительно выкладывается на сервер `ozma.gogol.school` для Джангира с белым списком из одной папки B2B. Правила «когда и как называть» живут в мастер-промпте `/b2b` в Notion; промпт Джангира ссылается на них. В Озме ничего не меняется — все три поля под ссылки уже есть.

**Tech Stack:** Python 3.10+, FastMCP (`mcp>=1.2,<2`), pytest, systemd, Yandex Disk REST API, Notion MCP, OzmaDB MCP.

Спека: `docs/superpowers/specs/2026-09-21-b2b-disk-folders-design.md`.

## Global Constraints

- Корень на Диске: `disk:/b2b - корпоративные клиенты`, аккаунт `bella.fatt@gogol.school`.
- В MCP нет и не появляется тулов удаления, перемещения, переименования и публичных ссылок; `tests/test_server.py::test_destructive_tools_are_absent` должен оставаться зелёным.
- Белый список сервера — только `disk:/b2b - корпоративные клиенты`. Папку договоров на сервер не добавлять.
- Значения токенов (`YANDEX_DISK_TOKEN`, `YANDEX_DISK_MCP_TOKEN`) не выводить в терминал, в чат, в коммиты и в логи: только пайпом из файла в файл.
- Ссылка на папку сделки — `crm.b2b_deals.materials_link`; на «документооборот» — `crm.actions.b2b_disk_folder`; на папку верхнего уровня — `crm.b2b_clients.disk_folder`. Новых полей в Озме не заводить.
- `materials_link = '—'` означает «папка не нужна», добор такие сделки пропускает.
- Чужую ссылку в `materials_link` (не на наш корень) агент не затирает.
- Код ботов живёт только на сервере (`/home/agentbot/bots/jangir/`), в git не кладётся. Перед правкой `bot.py` — бэкап `bot.py.bak-20260921`.
- Записи в боевую Озму и в Notion-промпты в задачах 4 и 6 — только после «да» Бэллы на конкретный список/текст.
- Коммиты в `gogol-school-ai` заканчиваются строкой `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File Structure

| Файл | Что делает |
|---|---|
| `~/YandexDiskMCP/src/yandex_disk_mcp/client.py` | + `DiskError.code`, `DiskClient._mkdir`, `DiskClient.create_folder` |
| `~/YandexDiskMCP/src/yandex_disk_mcp/server.py` | + тул `create_folder` |
| `~/YandexDiskMCP/tests/test_mkdir.py` (новый) | тесты клиента: цепочка уровней, 409, белый список |
| `~/YandexDiskMCP/tests/test_server.py` | точный набор тулов + тесты тула |
| `~/YandexDiskMCP/README.md`, `deploy/env.example` | описание тула, серверный белый список |
| `~/YandexDiskMCP/.env` | + вторая папка в `YANDEX_DISK_ALLOWED_PATHS` |
| Notion `3dd612c762af8131a33aee92712a7fcb` (промпт `/b2b`) | + блок «Папки на Яндекс.Диске» |
| Notion `3db612c762af81fbb9bdd4c06aaacf59` (промпт Джангира) | + две ссылки на этот блок |
| сервер: `/etc/yandex-disk-mcp.env`, `/opt/YandexDiskMCP`, `/home/agentbot/bots/jangir/bot.py` | выкладка MCP, доступ Джангира |

---

### Task 1: `DiskClient.create_folder`

**Files:**
- Modify: `~/YandexDiskMCP/src/yandex_disk_mcp/client.py`
- Test: `~/YandexDiskMCP/tests/test_mkdir.py` (create)

**Interfaces:**
- Consumes: `paths.assert_allowed(path) -> str`, `paths.allowed_roots() -> list[str]`, `DiskClient._request`, `DiskClient.get_meta`.
- Produces: `DiskError(message, code: int | None = None)` с атрибутом `.code`; `DiskClient.create_folder(path: str) -> dict` с ключами `path: str` (нормализованный), `created: bool` (создана ли сама целевая папка этим вызовом), `created_levels: list[str]` (все уровни, созданные этим вызовом, сверху вниз).

- [ ] **Step 1: Написать падающие тесты**

Создать `~/YandexDiskMCP/tests/test_mkdir.py`:

```python
import urllib.error
import urllib.request

import pytest

from yandex_disk_mcp.client import DiskClient, DiskError
from yandex_disk_mcp.paths import DiskAccessError

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"
B2B = "disk:/b2b - корпоративные клиенты"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS + "|" + B2B)
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "test-token")


class FakeDisk:
    """Диск в памяти: помнит папки и файлы и отвечает как API Диска.

    PUT /resources: 409, если путь занят ИЛИ нет родителя — API Диска
    отвечает одним кодом на обе ситуации, и клиент обязан их различать.
    """

    def __init__(self, dirs=(), files=(), fail_on=None):
        self.dirs = set(dirs)
        self.files = set(files)
        self.fail_on = fail_on  # (path, code): отдать этот код на PUT этого пути
        self.calls = []

    def request(self, client, method, endpoint, params, body=None):
        path = params["path"]
        self.calls.append((method, path))
        if method == "PUT":
            if self.fail_on and self.fail_on[0] == path:
                code = self.fail_on[1]
                raise DiskError(DiskClient._explain_http(code, ""), code=code)
            parent = path.rsplit("/", 1)[0]
            occupied = path in self.dirs or path in self.files
            orphan = parent != "disk:" and parent not in self.dirs
            if occupied or orphan:
                raise DiskError(DiskClient._explain_http(409, ""), code=409)
            self.dirs.add(path)
            return {}
        if path in self.dirs:
            return {"name": path.rsplit("/", 1)[-1], "path": path, "type": "dir"}
        if path in self.files:
            return {"name": path.rsplit("/", 1)[-1], "path": path, "type": "file"}
        raise DiskError(DiskClient._explain_http(404, ""), code=404)


@pytest.fixture
def disk(monkeypatch):
    fake = FakeDisk()
    monkeypatch.setattr(
        DiskClient, "_request", lambda self, *a, **kw: fake.request(self, *a, **kw)
    )
    return fake


def _puts(fake):
    return [p for m, p in fake.calls if m == "PUT"]


def test_creates_every_missing_level_top_down(disk):
    target = B2B + "/Сбер/Сбер - Елена - январь 2027 (№16)/документооборот"
    out = DiskClient().create_folder(target)

    assert _puts(disk) == [
        B2B,
        B2B + "/Сбер",
        B2B + "/Сбер/Сбер - Елена - январь 2027 (№16)",
        target,
    ], "уровни создаются по одному сверху вниз, начиная с корня белого списка"
    assert out == {"path": target, "created": True, "created_levels": _puts(disk)}


def test_second_call_is_harmless(disk):
    target = B2B + "/Яндекс/Сделка (№17)"
    DiskClient().create_folder(target)
    out = DiskClient().create_folder(target)

    assert out == {"path": target, "created": False, "created_levels": []}


def test_only_missing_tail_is_reported(disk):
    disk.dirs.update({B2B, B2B + "/Сбер"})
    target = B2B + "/Сбер/Новая сделка (№20)"
    out = DiskClient().create_folder(target)

    assert out["created_levels"] == [target]
    assert out["created"] is True


def test_outside_whitelist_is_denied_before_any_request(disk):
    with pytest.raises(DiskAccessError):
        DiskClient().create_folder("disk:/Пароли руководителей/новая")
    assert disk.calls == [], "отказ должен случиться до первого обращения к Диску"


def test_dotdot_cannot_escape_whitelist(disk):
    with pytest.raises(DiskAccessError):
        DiskClient().create_folder(B2B + "/../Пароли руководителей/новая")
    assert disk.calls == []


def test_file_in_the_way_is_an_error_not_success(disk):
    disk.dirs.add(B2B)
    disk.files.add(B2B + "/Сбер")
    with pytest.raises(DiskError) as exc:
        DiskClient().create_folder(B2B + "/Сбер")
    assert "файл" in str(exc.value)


def test_non_409_error_mid_chain_propagates(monkeypatch):
    fake = FakeDisk(dirs={B2B}, fail_on=(B2B + "/Сбер/Сделка (№1)", 429))
    monkeypatch.setattr(
        DiskClient, "_request", lambda self, *a, **kw: fake.request(self, *a, **kw)
    )
    with pytest.raises(DiskError) as exc:
        DiskClient().create_folder(B2B + "/Сбер/Сделка (№1)/документооборот")

    assert exc.value.code == 429
    assert B2B + "/Сбер" in fake.dirs, "уже созданные уровни не откатываются"
    assert B2B + "/Сбер/Сделка (№1)/документооборот" not in _puts(fake)


def test_disk_error_carries_http_code(monkeypatch):
    """create_folder различает 409 по коду, поэтому код обязан доезжать
    из HTTPError в DiskError, а не теряться в тексте сообщения."""
    import io

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(DiskError) as exc:
        DiskClient()._request("PUT", "/resources", {"path": B2B})
    assert exc.value.code == 409
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_mkdir.py -q`
Expected: FAIL — `TypeError: DiskError() takes no keyword arguments` и `AttributeError: 'DiskClient' object has no attribute 'create_folder'`.

- [ ] **Step 3: Реализация**

В `src/yandex_disk_mcp/client.py`:

Заменить импорт из `paths`:

```python
from .paths import allowed_roots, assert_allowed
```

Заменить класс `DiskError`:

```python
class DiskError(RuntimeError):
    """Ошибка API Диска. `code` — HTTP-статус, если он был."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
```

В `_urlopen_bytes` заменить ветку `HTTPError`:

```python
    except urllib.error.HTTPError as e:
        raise DiskError(
            DiskClient._explain_http(e.code, e.read().decode("utf-8", "replace")),
            code=e.code,
        )
```

В конец класса `DiskClient` добавить:

```python
    def _mkdir(self, norm: str) -> bool:
        """Создать один уровень. True — создан сейчас, False — уже был."""
        try:
            self._request("PUT", "/resources", {"path": norm})
            return True
        except DiskError as e:
            if e.code != 409:
                raise
        # 409 у Диска двусмысленный: «путь уже существует» и «нет родителя».
        # Различаем фактом: лежит по пути папка — значит, уже была.
        try:
            meta = self.get_meta(norm)
        except DiskError:
            raise DiskError(
                f"Не удалось создать «{norm}»: родительской папки нет.", code=409
            )
        if meta["type"] != "dir":
            raise DiskError(f"По пути «{norm}» лежит файл, а не папка.", code=409)
        return False

    def create_folder(self, path: str) -> dict[str, Any]:
        """Создать папку со всеми недостающими уровнями. Повторный вызов безвреден.

        API Диска не создаёт вложенные папки одним запросом, поэтому идём
        сверху вниз от корня белого списка: выше него создавать нечего и нельзя.
        """
        norm = assert_allowed(path)
        root = next(
            r for r in allowed_roots() if norm == r or norm.startswith(r + "/")
        )
        levels = [root]
        rest = norm[len(root):].strip("/")
        for part in rest.split("/") if rest else []:
            levels.append(levels[-1] + "/" + part)
        created = [lvl for lvl in levels if self._mkdir(lvl)]
        return {"path": norm, "created": norm in created, "created_levels": created}
```

- [ ] **Step 4: Убедиться, что тесты проходят и ничего не сломано**

Run: `cd ~/YandexDiskMCP && .venv/bin/pytest -q`
Expected: все тесты PASS, включая 8 новых из `test_mkdir.py`.

- [ ] **Step 5: Commit**

```bash
cd ~/YandexDiskMCP && git add src/yandex_disk_mcp/client.py tests/test_mkdir.py && git commit -m "create_folder в клиенте: уровни сверху вниз, 409 различаем фактом, код HTTP в DiskError"
```

---

### Task 2: тул `create_folder` в MCP

**Files:**
- Modify: `~/YandexDiskMCP/src/yandex_disk_mcp/server.py` (после тула `upload`)
- Modify: `~/YandexDiskMCP/tests/test_server.py`
- Modify: `~/YandexDiskMCP/README.md`, `~/YandexDiskMCP/deploy/env.example`

**Interfaces:**
- Consumes: `DiskClient.create_folder(path) -> {"path", "created", "created_levels"}` из Task 1; `documents.build_link(path) -> str`; `SearchIndex.invalidate(path) -> None`.
- Produces: MCP-тул `create_folder(path: str)`; успешный ответ — dict `{"path": str, "created": bool, "created_levels": list[str], "link": str}`, ошибка — строка с текстом (как у остальных тулов через `_guard`).

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_server.py` в `test_tool_set_is_exact` заменить ожидаемый набор:

```python
    assert names == {
        "list_folder",
        "find",
        "get_link",
        "read_document",
        "upload",
        "create_folder",
        "allowed_folders",
    }
```

В конец `tests/test_server.py` добавить:

```python
def test_create_folder_tool_returns_link_and_drops_search_cache(monkeypatch):
    from yandex_disk_mcp import server
    from yandex_disk_mcp.client import DiskClient

    target = CONTRACTS + "/Сбер/Новая"

    def fake_create(self, path):
        return {"path": target, "created": True, "created_levels": [target]}

    dropped = []

    class FakeIndex:
        def invalidate(self, path):
            dropped.append(path)

    monkeypatch.setattr(DiskClient, "create_folder", fake_create)
    monkeypatch.setattr(server, "_search", lambda: FakeIndex())

    out = server.create_folder(target)

    assert out["created"] is True
    assert out["link"].startswith("https://disk.360.yandex.ru/client/disk/")
    assert dropped == [CONTRACTS + "/Сбер"], "find() должен увидеть новую папку сразу"


def test_create_folder_tool_reports_denial_as_text():
    from yandex_disk_mcp.server import create_folder

    out = create_folder("disk:/Пароли руководителей/новая")
    assert "вне разрешённых папок" in out
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_server.py -q`
Expected: FAIL — `test_tool_set_is_exact` (нет `create_folder` в наборе) и `AttributeError: module 'yandex_disk_mcp.server' has no attribute 'create_folder'`.

- [ ] **Step 3: Реализация**

В `src/yandex_disk_mcp/server.py` после функции `upload` добавить:

```python
@mcp.tool()
def create_folder(path: str) -> Any:
    """Создать папку на Диске со всеми недостающими уровнями (только внутри разрешённых папок).

    Папка уже есть — не ошибка: вернётся created=false и та же ссылка.
    Имя готовит вызывающий: «/» внутри имени уровня недопустим.
    Удалить или переименовать созданное этим сервером нельзя — проверь имя до вызова.
    """

    def _run() -> Any:
        result = _client().create_folder(path)
        for level in result["created_levels"]:
            _search().invalidate(posixpath.dirname(level))
        result["link"] = build_link(result["path"])
        return result

    return _guard(_run)
```

В `README.md` в список тулов после строки про `upload` добавить:

```markdown
- `create_folder(path)` — папка со всеми недостающими уровнями; повторный вызов безвреден
```

и в раздел «Границы» после абзаца про белый список:

```markdown
На Маке в белом списке две папки (договоры и `b2b - корпоративные клиенты`),
на сервере — только вторая: Джангиру договоры не видны.
```

В `deploy/env.example` заменить строку белого списка:

```
YANDEX_DISK_ALLOWED_PATHS=disk:/b2b - корпоративные клиенты
```

- [ ] **Step 4: Убедиться, что всё зелёное**

Run: `cd ~/YandexDiskMCP && .venv/bin/pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/YandexDiskMCP && git add -A src tests README.md deploy/env.example && git commit -m "Тул create_folder: ссылка в ответе, сброс кэша поиска, серверный белый список — только B2B"
```

---

### Task 3: белый список на Маке и живая проверка

**Files:**
- Modify: `~/YandexDiskMCP/.env` (вне git)

**Interfaces:**
- Consumes: `DiskClient.create_folder` (Task 1).
- Produces: на Диске существует `disk:/b2b - корпоративные клиенты`; локальный MCP после перезапуска сессии Claude Code видит её и умеет `create_folder`.

- [ ] **Step 1: Добавить папку в белый список, не показывая токен**

```bash
python3 - <<'EOF'
import os, re
p = os.path.expanduser("~/YandexDiskMCP/.env")
s = open(p, encoding="utf-8").read()
new = "disk:/b2b - корпоративные клиенты"
m = re.search(r"^YANDEX_DISK_ALLOWED_PATHS=(.*)$", s, flags=re.M)
assert m, "строка YANDEX_DISK_ALLOWED_PATHS не найдена"
if new not in m.group(1).split("|"):
    s = s[: m.end(1)] + "|" + new + s[m.end(1):]
    open(p, "w", encoding="utf-8").write(s)
print(re.search(r"^YANDEX_DISK_ALLOWED_PATHS=.*$", open(p, encoding="utf-8").read(), flags=re.M).group(0))
EOF
```

Expected: `YANDEX_DISK_ALLOWED_PATHS=disk:/Договоры с компаниями (Реализация)|disk:/b2b - корпоративные клиенты`

- [ ] **Step 2: Живая проверка — создать корень и повторить вызов**

```bash
cd ~/YandexDiskMCP && .venv/bin/python - <<'EOF'
from yandex_disk_mcp.client import DiskClient
from yandex_disk_mcp.documents import build_link
root = "disk:/b2b - корпоративные клиенты"
c = DiskClient()
print(c.create_folder(root))
print(c.create_folder(root))
print(c.list_folder(root))
print(build_link(root))
EOF
```

Expected: первая строка — `created: True` (или `False`, если Бэлла уже создала папку руками), вторая — `{'path': 'disk:/b2b - корпоративные клиенты', 'created': False, 'created_levels': []}`, третья — список (пустой или с содержимым), четвёртая — ссылка `https://disk.360.yandex.ru/client/disk/b2b%20-%20...`.

- [ ] **Step 3: Проверить отказ вне белого списка на живом сервере**

```bash
cd ~/YandexDiskMCP && .venv/bin/python -c "
from yandex_disk_mcp.server import create_folder
print(create_folder('disk:/Проверка вне списка'))"
```

Expected: строка `Путь «disk:/Проверка вне списка» вне разрешённых папок. Разрешено: …` — папка на Диске НЕ появилась.

- [ ] **Step 4: Попросить Бэллу**

Сообщить Бэлле: (а) открыть ссылку из Step 2 и убедиться, что папка на месте; (б) в интерфейсе Диска расшарить папку `b2b - корпоративные клиенты` аккаунтам (Саша, Вика, Катя) — без этого приватные ссылки у них не откроются; (в) перезапустить сессию Claude Code, чтобы локальный MCP подхватил новый тул и белый список.

---

### Task 4: правила в мастер-промптах Notion

**Files:**
- Modify: Notion-страница `3dd612c762af8131a33aee92712a7fcb` (промпт `/b2b`)
- Modify: Notion-страница `3db612c762af81fbb9bdd4c06aaacf59` (промпт Джангира)

Работать только через рабочий Notion MCP (`mcp__c2755fd9-…__notion-fetch` / `notion-update-page`), не через personal.

**Interfaces:**
- Consumes: тул `mcp__yandex-disk__create_folder` → `{"path", "created", "created_levels", "link"}`; `mcp__yandex-disk__get_link(path) -> str`.
- Produces: раздел «Папки на Яндекс.Диске» в промпте `/b2b`; Джангир грузит оба промпта, поэтому правила у него те же.

- [ ] **Step 1: Прочитать обе страницы**

`notion-fetch` обеих страниц. Найти в промпте `/b2b` раздел про заведение сделки и продукта, в промпте Джангира — описание шага 8 и утреннего обхода.

- [ ] **Step 2: Показать Бэлле точный текст и место вставки, дождаться «да»**

Текст блока для промпта `/b2b` (вставить отдельным разделом после раздела о заведении сделок и продуктов):

````markdown
## Папки на Яндекс.Диске

Корень: `disk:/b2b - корпоративные клиенты`. Структура: `{бренд}/{сделка}/документооборот`. Тулы: `mcp__yandex-disk__create_folder` (повторный вызов безвреден), `get_link`, `list_folder`. Удалить или переименовать папку нечем — проверь имя до вызова.

**Имена**
- Верхний уровень = бренд клиента (`client=>brand=>name`). У клиента без бренда — имя клиента (`client=>name`).
- Папка сделки = `{название сделки} (№{id})`.
- У сделки пустое название → собери `{Клиент} · {месяц год}`: Клиент = `name`, при заполненной команде — `name — team`; месяц словом в именительном падеже и год — по `request_date`, при пустой дате текущие. Запиши это название в `crm.b2b_deals.name`.
- Очистка каждого уровня: `/` и `\` → `-`, повторные пробелы схлопнуть, пробелы и точки на концах убрать, не длиннее 200 символов (хвост ` (№id)` не обрезать).

**Когда**
1. Завёл сделку → `create_folder("disk:/b2b - корпоративные клиенты/{верх}/{папка сделки}")` → `link` из ответа запиши в `crm.b2b_deals.materials_link`. Если у клиента пуст `disk_folder` — запиши туда `get_link` папки верхнего уровня.
2. Создал продукт из сделки (шаг 8 или «создай продукт») → `create_folder("{путь папки сделки}/документооборот")` → `link` запиши в `crm.actions.b2b_disk_folder` этого продукта. Путь папки сделки бери из её `materials_link`: всё после `https://disk.360.yandex.ru/client/disk/`, url-декодированное, с префиксом `disk:/`. Пусто — сначала пункт 1. Чужая ссылка (не на наш корень) — собери путь по правилу имён, ссылку в сделке не меняй.
3. Подтверждения на папку не спрашивай и отдельно о ней не отчитывайся — добавь в ту же карточку-ответ строку «Папка: {ссылка}».

**Добор** — на утреннем обходе и по просьбе «создай недостающие папки»
- Сделки без папки:
  `SELECT id, name, request_date, client=>name AS client, client=>team AS team, client=>brand=>name AS brand FROM crm.b2b_deals WHERE NOT is_deleted AND (materials_link IS NULL OR materials_link = '') AND NOT (status=>is_final AND NOT status=>is_success)`
- Продукты без «документооборота»:
  `SELECT id, name, b2b_deal, b2b_deal=>materials_link AS deal_folder FROM crm.actions WHERE b2b_deal IS NOT NULL AND NOT b2b_deal=>is_deleted AND (b2b_disk_folder IS NULL OR b2b_disk_folder = '') AND (b2b_deal=>materials_link IS NULL OR b2b_deal=>materials_link <> '—')`
- `materials_link = '—'` значит «папка не нужна» — не трогай.
- Об успешном доборе не пиши. Ошибки — одной строкой в «Сводки».

**Если Диск не ответил.** Сделку или продукт всё равно заведи, ссылку оставь пустой, скажи одной строкой «папку не создал, доберу». Отказ «вне разрешённых папок» — ошибка настройки: передай её дословно и не повторяй попытку.
````

Две вставки в промпт Джангира:
- в описание шага 8 (создание продукта), одной строкой: `После создания продукта — «документооборот» по разделу «Папки на Яндекс.Диске» промпта /b2b.`
- в описание утреннего обхода, одной строкой: `В конце обхода — добор папок по разделу «Папки на Яндекс.Диске» промпта /b2b; молча, ошибки — в «Сводки».`

- [ ] **Step 3: Записать в Notion после «да»**

`notion-update-page` для обеих страниц: вставка нового содержимого после найденного якоря, без замены остального текста страницы.

- [ ] **Step 4: Проверить**

`notion-fetch` обеих страниц: раздел «Папки на Яндекс.Диске» есть в `/b2b`, обе строки есть у Джангира, остальной текст не изменился (сравнить длину и соседние заголовки с тем, что прочитано в Step 1).

---

### Task 5: выкладка Disk MCP на сервер и доступ Джангира

**Files:**
- Create (сервер): `/etc/yandex-disk-mcp.env`, `/opt/YandexDiskMCP/`, `/etc/systemd/system/yandex-disk-mcp.service`
- Modify (сервер): `/home/agentbot/bots/jangir/bot.py` (список `ALLOWED_TOOLS`, строки ~88–108), `/home/agentbot/.claude.json` (через `claude mcp add`)

Перед началом получить от Бэллы явное «да» на перенос токена Диска на сервер (решение принято в спеке, но действие необратимо в смысле «секрет уехал»).

**Interfaces:**
- Consumes: репозиторий `~/YandexDiskMCP` после Task 2; `deploy/install.sh`; `CLAUDE = "/home/agentbot/.nvm/versions/node/v20.20.2/bin/claude"`, бот запускает claude с `cwd=/home/agentbot/gogol-school-ai-jangir` и `NO_PROXY=127.0.0.1,localhost`.
- Produces: `http://127.0.0.1:8005/mcp` на сервере с заголовком `Authorization: Bearer <YANDEX_DISK_MCP_TOKEN>`; у Джангира доступны `mcp__yandex-disk__create_folder`, `mcp__yandex-disk__list_folder`, `mcp__yandex-disk__get_link`.

- [ ] **Step 1: Создать env на сервере пайпом (значения на экран не выводятся)**

```bash
DISK_LINE=$(grep '^YANDEX_DISK_TOKEN=' ~/YandexDiskMCP/.env) && \
MCP_TOKEN=$(openssl rand -hex 24) && \
printf '%s\nYANDEX_DISK_ALLOWED_PATHS=disk:/b2b - корпоративные клиенты\nYANDEX_DISK_MCP_TOKEN=%s\nYANDEX_DISK_MCP_HOST=127.0.0.1\nYANDEX_DISK_MCP_PORT=8005\n' "$DISK_LINE" "$MCP_TOKEN" \
  | ssh root@ozma.gogol.school 'umask 077; cat > /etc/yandex-disk-mcp.env; stat -c "%a %n" /etc/yandex-disk-mcp.env; cut -d= -f1 /etc/yandex-disk-mcp.env'
```

Expected: `600 /etc/yandex-disk-mcp.env` и пять имён переменных без значений.

- [ ] **Step 2: Залить код и установить сервис**

```bash
rsync -a --delete --exclude .venv --exclude .git --exclude .env --exclude .pytest_cache --exclude '__pycache__' ~/YandexDiskMCP/ root@ozma.gogol.school:/root/YandexDiskMCP-src/ && \
ssh root@ozma.gogol.school 'cd /root/YandexDiskMCP-src && ./deploy/install.sh'
```

Expected: `Active: active (running)`; строки «Создан /etc/yandex-disk-mcp.env» быть НЕ должно (файл уже есть).

- [ ] **Step 3: Смоук сервиса**

```bash
ssh root@ozma.gogol.school 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8005/mcp; ss -ltn | grep ":8005 "'
```

Expected: `401` (без токена не пускает) и слушающий сокет на `127.0.0.1:8005` — не на `0.0.0.0`.

- [ ] **Step 4: Подключить MCP только клону Джангира**

```bash
ssh root@ozma.gogol.school 'T=$(grep "^YANDEX_DISK_MCP_TOKEN=" /etc/yandex-disk-mcp.env | cut -d= -f2-); sudo -u agentbot -H bash -c "cd /home/agentbot/gogol-school-ai-jangir && /home/agentbot/.nvm/versions/node/v20.20.2/bin/claude mcp add -s local --transport http yandex-disk http://127.0.0.1:8005/mcp --header \"Authorization: Bearer $T\" >/dev/null && echo added"'
```

Expected: `added`. Проверка, что подключение не ушло в общий скоуп:

```bash
ssh root@ozma.gogol.school 'python3 - <<EOF
import json
d = json.load(open("/home/agentbot/.claude.json"))
print("user-scope:", "yandex-disk" in (d.get("mcpServers") or {}))
for p, pc in (d.get("projects") or {}).items():
    if "yandex-disk" in (pc.get("mcpServers") or {}): print("local:", p)
EOF'
```

Expected: `user-scope: False` и `local: /home/agentbot/gogol-school-ai-jangir`.

- [ ] **Step 5: Разрешить Джангиру три тула**

```bash
ssh root@ozma.gogol.school 'cd /home/agentbot/bots/jangir && cp -p bot.py bot.py.bak-20260921 && python3 - <<EOF
p = "bot.py"
s = open(p, encoding="utf-8").read()
anchor = "    \"mcp__notion\",\n])"
assert s.count(anchor) == 1, "якорь ALLOWED_TOOLS не найден или не уникален"
add = (
    "    \"mcp__notion\",\n"
    "    # Яндекс.Диск — только папки B2B (белый список на сервере); upload и чтение документов не даём\n"
    "    \"mcp__yandex-disk__create_folder\",\n"
    "    \"mcp__yandex-disk__list_folder\",\n"
    "    \"mcp__yandex-disk__get_link\",\n"
    "])"
)
open(p, "w", encoding="utf-8").write(s.replace(anchor, add))
EOF
chown agentbot:agentbot bot.py bot.py.bak-20260921 && python3 -m py_compile bot.py && systemctl restart jangir-bot && sleep 2 && systemctl is-active jangir-bot'
```

Expected: `active`.

- [ ] **Step 6: Обновить кэш промптов и проверить от имени Джангира**

```bash
ssh root@ozma.gogol.school 'sudo -u agentbot -H /home/agentbot/refresh-prompt-cache.sh 2>&1 | tail -3'
```

Затем в Telegram в теме Бэллы/Саши спросить Джангира: «какие папки лежат в корне b2b на диске?». Expected: Джангир вызывает `list_folder` и перечисляет содержимое корня (или говорит, что пусто) — без ошибки доступа.

- [ ] **Step 7: Попросить Бэллу**

Записать значение `YANDEX_DISK_MCP_TOKEN` (лежит в `/etc/yandex-disk-mcp.env` на сервере) на страницу Notion «🔐 Токены MCP» — это принятое место хранения ключей; значение в чат не выводить.

---

### Task 6: первый прогон на живых сделках

**Files:** данные в боевой Озме (`crm.b2b_deals`, `crm.actions`, `crm.b2b_clients`) и папки на Диске.

**Interfaces:**
- Consumes: тул `mcp__yandex-disk__create_folder` локального MCP (после перезапуска сессии, Task 3 Step 4); запросы добора из Task 4.
- Produces: у отмеченных сделок заполнены `materials_link`, у их продуктов — `b2b_disk_folder`, у клиентов — `disk_folder`; у неотмеченных сделок `materials_link = '—'`.

- [ ] **Step 1: Собрать и показать список**

Выполнить оба запроса добора из Task 4. По каждой сделке показать Бэлле строку: `№id · бренд/клиент · будущий путь папки · есть ли продукт`. На 21.09 запрос сделок возвращает пять: 2, 3 (тестовые «Тест b2b»), 10 (Вероника Коробова), 16 (Сбер — Елена), 17 (Яндекс, без названия). Ожидаемые пути:

```
№10 → b2b - корпоративные клиенты/Вероника Коробова/ВИДЕТЬ. СЛЫШАТЬ. ПРИНИМАТЬ (№10)
№16 → b2b - корпоративные клиенты/Сбер/Сбер - Елена - январь 2027 (№16)   + документооборот (продукт 12434)
№17 → b2b - корпоративные клиенты/Яндекс/Яндекс — Тест Ангелина · сентябрь 2026 (№17)   + документооборот (продукт 12444)
```

(У №10 хвостовая точка названия срезана по правилу очистки.) Спросить: каким сделкам создаём папки, каким ставим «—».

- [ ] **Step 2: Создать папки отмеченным сделкам**

Для каждой отмеченной сделки: `create_folder` папки сделки → при наличии продукта `create_folder` для `…/документооборот`. Записать ответы (`path`, `link`, `created`).

- [ ] **Step 3: Записать ссылки в Озму одной транзакцией на сделку**

Через `mcp__ozma__transaction` (операции `update`): `crm.b2b_deals.materials_link` = ссылка сделки; для сделки 17 ещё `name` = `Яндекс — Тест Ангелина · сентябрь 2026`; `crm.actions.b2b_disk_folder` = ссылка «документооборота» у каждого продукта сделки; `crm.b2b_clients.disk_folder` = `get_link` папки верхнего уровня, если поле пусто. Неотмеченным сделкам — `materials_link = '—'`.

- [ ] **Step 4: Проверить**

Повторно выполнить оба запроса добора. Expected: оба возвращают `[]`. Повторить `create_folder` по одному из путей — `created: false`. Открыть форму сделки 16 в Озме: в поле «Папка на материалы» стоит ссылка, она открывается.

- [ ] **Step 5: Обновить память и закоммитить план**

В `reference_ozma_b2b_crm.md` заменить «на 21.09 спека написана, реализации ещё нет» на фактический статус; в `reference_yandex_disk_mcp.md` дописать: тул `create_folder`, две папки в белом списке на Маке, сервер — порт 8005, только B2B, подключён только Джангиру (`-s local`).

```bash
cd /Users/bellafatt/gogol-school-ai && git add docs/superpowers/plans/2026-09-21-b2b-disk-folders.md && git commit -m "План: папки B2B на Яндекс.Диске

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
