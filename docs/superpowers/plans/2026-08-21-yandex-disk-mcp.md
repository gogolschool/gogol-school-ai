# Yandex Disk MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MCP-сервер, который находит документы в папке `disk:/Договоры с компаниями (Реализация)` на Яндекс.Диске и выдаёт на них ссылки, не имея доступа к остальному Диску.

**Architecture:** Пять модулей с раздельной ответственностью: `paths.py` — защитный периметр (белый список, без сети), `client.py` — обёртка над REST API Яндекса (без знания про MCP), `search.py` — нормализация имён и сопоставление (чистые функции, без сети), `documents.py` — ссылки и извлечение текста, `server.py` — определения тулов MCP. Спека называла три модуля; поиск и работа с документами вынесены отдельно, потому что это разные оси изменений и обе тестируются без сети. Каждый тул сначала прогоняет путь через `assert_allowed`, потом работает.

**Tech Stack:** Python ≥3.10, `mcp` 1.x (FastMCP), `python-dotenv`, `python-docx`, `pypdf`, `pytest`. HTTP-транспорт — через `mcp.streamable_http_app()` + uvicorn, как в `YandexMailMCP`.

## Global Constraints

- Каталог проекта: `~/YandexDiskMCP` (уже создан, там лежит `.env` с токеном, `chmod 600`, и `.gitignore`).
- Раскладка повторяет `~/YandexMailMCP`: `src/yandex_disk_mcp/`, `deploy/`, `pyproject.toml` с двумя entrypoint'ами (stdio и http).
- Имя пакета: `yandex_disk_mcp`. Имя сервера в FastMCP: `"yandex-disk"`.
- API-база: `https://cloud-api.yandex.net/v1/disk`. Заголовок авторизации: `Authorization: OAuth <token>` (именно `OAuth`, не `Bearer`).
- Переменные окружения: `YANDEX_DISK_TOKEN`, `YANDEX_DISK_ALLOWED_PATHS` (разделитель `|`), `YANDEX_DISK_ENV_FILE` (необязательный).
- **Тулов удаления и перемещения не существует.** Не добавлять их ни под каким предлогом.
- **Публичные ссылки не поддерживаются.** Эндпоинт `PUT /resources/publish` не вызывается нигде.
- Загрузка всегда с `overwrite=false`.
- **Хвостовые пробелы в именах папок значимы.** На реальном Диске есть папки `Озон `, `Другие `, `LUXOTTICA `, `Яков и партнеры `. Нормализация пути НЕ должна их срезать — иначе путь перестанет резолвиться. Это касается только путей; нормализация для *поиска* пробелы схлопывает, и это другая функция.
- Все сообщения об ошибках — по-русски, пользователь читает их напрямую.
- **Версия `mcp` прижата к `<2`.** Проверено 21.08.2026: `mcp>=1.2.0` тянет 2.0.0, где `mcp.server.fastmcp` удалён, а `FastMCP` переименован в `MCPServer` (`mcp.server.mcpserver`). Почтовый и Unisender-серверы работают на 1.x, спека требует не плодить разных подходов — поэтому здесь тоже 1.x. Миграция всех серверов на 2.0 — отдельная задача, не эта.
- **Полный обход папки договоров запрещён.** Замерено на живом API: один запрос ~1.1 с, папок 159 — рекурсивный обход это ~180 с, клиент отвалится по таймауту. Поиск двухфазный (см. Task 3).

---

### Task 1: Каркас проекта и белый список путей

Защитный периметр. Делается первым и тестируется первым, потому что именно этот код держит сервер подальше от папок «Пароли руководителей», «Трудовые договоры» и «Самозанятость - мастера», которые лежат на том же Диске.

**Files:**
- Create: `~/YandexDiskMCP/pyproject.toml`
- Create: `~/YandexDiskMCP/src/yandex_disk_mcp/__init__.py`
- Create: `~/YandexDiskMCP/src/yandex_disk_mcp/paths.py`
- Create: `~/YandexDiskMCP/tests/test_paths.py`

**Interfaces:**
- Consumes: ничего
- Produces:
  - `DiskAccessError(PermissionError)`
  - `normalize_path(path: str) -> str` — приводит к виду `disk:/A/B`, схлопывает `..` и `//`, убирает хвостовой слэш, **сохраняет пробелы внутри имён**
  - `assert_allowed(path: str) -> str` — возвращает нормализованный путь или кидает `DiskAccessError`
  - `allowed_roots() -> list[str]`

- [ ] **Step 1: Инициализировать репозиторий и раскладку**

```bash
cd ~/YandexDiskMCP
git init -q
mkdir -p src/yandex_disk_mcp tests deploy
touch src/yandex_disk_mcp/__init__.py
```

`.env` и `.gitignore` уже существуют — не трогать и не перезаписывать. Убедиться, что `.env` в `.gitignore`:

```bash
grep -q '^\.env$' .gitignore && echo "ok: .env игнорируется"
```

- [ ] **Step 2: Написать `pyproject.toml`**

```toml
[project]
name = "yandex-disk-mcp"
version = "0.1.0"
description = "MCP server for Yandex Disk (contracts folder, read-mostly)"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.2.0,<2",  # в mcp 2.0 FastMCP переименован в MCPServer — см. Global Constraints
    "python-dotenv>=1.0.0",
    "python-docx>=1.1.0",
    "pypdf>=4.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]

[project.scripts]
yandex-disk-mcp = "yandex_disk_mcp.server:main"
yandex-disk-mcp-http = "yandex_disk_mcp.server:main_http"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/yandex_disk_mcp"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 3: Написать падающий тест на белый список**

Create `tests/test_paths.py`:

```python
import pytest

from yandex_disk_mcp.paths import (
    DiskAccessError,
    allowed_roots,
    assert_allowed,
    normalize_path,
)

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS)


def test_normalize_adds_prefix():
    assert normalize_path("Договоры с компаниями (Реализация)") == CONTRACTS


def test_normalize_strips_trailing_slash():
    assert normalize_path(CONTRACTS + "/") == CONTRACTS


def test_normalize_collapses_double_slash():
    assert normalize_path(CONTRACTS + "//Сбер") == CONTRACTS + "/Сбер"


def test_normalize_keeps_trailing_space_in_name():
    """На Диске реально есть папка 'Озон ' — пробел значим, срезать нельзя."""
    assert normalize_path(CONTRACTS + "/Озон ") == CONTRACTS + "/Озон "


def test_root_itself_is_allowed():
    assert assert_allowed(CONTRACTS) == CONTRACTS


def test_child_is_allowed():
    assert assert_allowed(CONTRACTS + "/Сбер") == CONTRACTS + "/Сбер"


def test_outside_path_is_denied():
    with pytest.raises(DiskAccessError):
        assert_allowed("disk:/Пароли руководителей")


def test_dotdot_escape_is_denied():
    """Схлопывание .. должно происходить ДО проверки, иначе периметр дырявый."""
    with pytest.raises(DiskAccessError):
        assert_allowed(CONTRACTS + "/../Пароли руководителей")


def test_deep_dotdot_escape_is_denied():
    with pytest.raises(DiskAccessError):
        assert_allowed(CONTRACTS + "/Сбер/../../Трудовые договоры")


def test_prefix_attack_is_denied():
    """Соседняя папка, имя которой начинается так же, не должна проходить."""
    with pytest.raises(DiskAccessError):
        assert_allowed(CONTRACTS + " и ещё что-то")


def test_empty_allowlist_denies_everything(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", "")
    with pytest.raises(DiskAccessError):
        assert_allowed(CONTRACTS)


def test_error_message_lists_allowed_roots():
    """Отказ должен отличаться от «не найдено» и показывать, куда можно."""
    with pytest.raises(DiskAccessError) as exc:
        assert_allowed("disk:/Пароли руководителей")
    assert "Договоры с компаниями" in str(exc.value)


def test_allowed_roots_parses_pipe_separator(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", f"{CONTRACTS}|disk:/Шаблоны документов")
    assert allowed_roots() == [CONTRACTS, "disk:/Шаблоны документов"]
```

- [ ] **Step 4: Запустить тест, убедиться что падает**

```bash
cd ~/YandexDiskMCP && python -m venv .venv && .venv/bin/pip install -q -e ".[dev]" && .venv/bin/pytest tests/test_paths.py -q
```

Ожидается: `ModuleNotFoundError: No module named 'yandex_disk_mcp.paths'` — все 13 тестов падают на импорте.

- [ ] **Step 5: Реализовать `paths.py`**

```python
"""Защитный периметр: решает, разрешён путь или нет.

Единственное место в проекте, где принимается это решение. Токен имеет
read+write на весь Диск, включая «Пароли руководителей» и трудовые договоры,
поэтому ограничение живёт здесь и проверяется тестами без сети.
"""

from __future__ import annotations

import os
import posixpath
from pathlib import Path

from dotenv import load_dotenv

_ENV_CANDIDATES = [
    os.environ.get("YANDEX_DISK_ENV_FILE"),
    str(Path.home() / "YandexDiskMCP" / ".env"),
    str(Path.home() / ".config" / "yandex-disk-mcp" / ".env"),
]
for _path in _ENV_CANDIDATES:
    if _path and Path(_path).is_file():
        load_dotenv(_path, override=False)
        break

DISK_PREFIX = "disk:/"


class DiskAccessError(PermissionError):
    """Путь вне белого списка. Отличается от «не найдено» намеренно."""


def normalize_path(path: str) -> str:
    """`Договоры/Сбер/` -> `disk:/Договоры/Сбер`.

    Схлопывает `..` и `//` ДО того, как путь увидит проверка доступа.
    Пробелы внутри имён сохраняются: на Диске есть папки «Озон » и «Другие ».
    """
    if not path:
        raise DiskAccessError("Пустой путь")
    rest = path[len(DISK_PREFIX):] if path.startswith(DISK_PREFIX) else path.lstrip("/")
    # normpath делает это лексически; выше корня выйти невозможно
    rest = posixpath.normpath("/" + rest).lstrip("/")
    return DISK_PREFIX + rest


def allowed_roots() -> list[str]:
    raw = os.environ.get("YANDEX_DISK_ALLOWED_PATHS", "")
    return [normalize_path(p) for p in raw.split("|") if p.strip()]


def assert_allowed(path: str) -> str:
    """Вернуть нормализованный путь или отказать."""
    norm = normalize_path(path)
    roots = allowed_roots()
    if not roots:
        raise DiskAccessError(
            "YANDEX_DISK_ALLOWED_PATHS пуст — серверу не разрешена ни одна папка"
        )
    for root in roots:
        if norm == root or norm.startswith(root + "/"):
            return norm
    listed = ", ".join(f"«{r[len(DISK_PREFIX):]}»" for r in roots)
    raise DiskAccessError(
        f"Путь «{norm}» вне разрешённых папок. Разрешено: {listed}"
    )
```

- [ ] **Step 6: Запустить тесты, убедиться что проходят**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_paths.py -q
```

Ожидается: `13 passed`.

- [ ] **Step 7: Коммит**

```bash
cd ~/YandexDiskMCP
git add pyproject.toml src tests .gitignore
git commit -m "Каркас проекта и белый список путей

Периметр вынесен в отдельный модуль и покрыт тестами: обход через ..,
префиксные совпадения, пустой список. Пробелы в именах папок сохраняются."
```

---

### Task 2: Клиент REST API — листинг и метаданные

**Files:**
- Create: `~/YandexDiskMCP/src/yandex_disk_mcp/client.py`
- Create: `~/YandexDiskMCP/tests/test_client.py`

**Interfaces:**
- Consumes: `paths.assert_allowed`
- Produces:
  - `DiskError(RuntimeError)`
  - `DiskClient(token: str | None = None)`
  - `DiskClient._request(method: str, endpoint: str, params: dict, body: bytes | None = None) -> dict` — единственная точка выхода в сеть; тесты её подменяют
  - `DiskClient.list_folder(path: str, limit: int = 1000) -> list[dict]` — элементы `{"name", "path", "type", "size", "modified"}`, `type` ∈ `{"dir", "file"}`
  - `DiskClient.get_meta(path: str) -> dict`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_client.py`:

```python
import pytest

from yandex_disk_mcp.client import DiskClient, DiskError
from yandex_disk_mcp.paths import DiskAccessError

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS)
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "test-token")


def _fake_listing(monkeypatch, items):
    def fake_request(self, method, endpoint, params, body=None):
        return {"_embedded": {"items": items}}

    monkeypatch.setattr(DiskClient, "_request", fake_request)


def test_list_folder_maps_fields(monkeypatch):
    _fake_listing(monkeypatch, [
        {"name": "Сбер", "path": CONTRACTS + "/Сбер", "type": "dir",
         "modified": "2026-05-01T10:00:00+00:00"},
        {"name": "Договор.docx", "path": CONTRACTS + "/Договор.docx", "type": "file",
         "size": 51200, "modified": "2026-06-02T09:30:00+00:00"},
    ])
    items = DiskClient().list_folder(CONTRACTS)
    assert [i["name"] for i in items] == ["Сбер", "Договор.docx"]
    assert items[0]["type"] == "dir"
    assert items[1]["size"] == 51200
    assert items[1]["modified"] == "2026-06-02"


def test_list_folder_denies_outside_path(monkeypatch):
    _fake_listing(monkeypatch, [])
    with pytest.raises(DiskAccessError):
        DiskClient().list_folder("disk:/Пароли руководителей")


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("YANDEX_DISK_TOKEN", raising=False)
    with pytest.raises(DiskError) as exc:
        DiskClient()
    assert "YANDEX_DISK_TOKEN" in str(exc.value)


def test_401_gives_reissue_hint(monkeypatch):
    def fake_request(self, method, endpoint, params, body=None):
        raise DiskError(DiskClient._explain_http(401, ""))

    monkeypatch.setattr(DiskClient, "_request", fake_request)
    with pytest.raises(DiskError) as exc:
        DiskClient().list_folder(CONTRACTS)
    assert "oauth.yandex.ru/authorize" in str(exc.value)


def test_explain_404_mentions_path_missing():
    msg = DiskClient._explain_http(404, "")
    assert "не найден" in msg.lower()
```

- [ ] **Step 2: Запустить, убедиться что падает**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_client.py -q
```

Ожидается: `ModuleNotFoundError: No module named 'yandex_disk_mcp.client'`.

- [ ] **Step 3: Реализовать `client.py`**

```python
"""Обёртка над REST API Яндекс.Диска. Ничего не знает про MCP."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .paths import assert_allowed

API_BASE = "https://cloud-api.yandex.net/v1/disk"
CLIENT_ID = "669ccefb345c48278db3776bacaf80bd"
AUTHORIZE_URL = (
    f"https://oauth.yandex.ru/authorize?response_type=token&client_id={CLIENT_ID}"
)


class DiskError(RuntimeError):
    pass


class DiskClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("YANDEX_DISK_TOKEN")
        if not self.token:
            raise DiskError(
                "YANDEX_DISK_TOKEN не задан. Ожидается в ~/YandexDiskMCP/.env"
            )

    @staticmethod
    def _explain_http(code: int, body: str) -> str:
        if code == 401:
            return (
                "Токен Яндекс.Диска протух или отозван. Перевыпустить: "
                f"{AUTHORIZE_URL} — новое значение положить в "
                "~/YandexDiskMCP/.env в YANDEX_DISK_TOKEN"
            )
        if code == 403:
            return "Яндекс отказал в доступе (403). Проверь скоупы приложения."
        if code == 404:
            return "Путь на Диске не найден (404)."
        if code == 409:
            return "Конфликт (409): путь уже существует или родительской папки нет."
        if code == 429:
            return "Слишком много запросов к Диску (429). Подожди и повтори."
        return f"Ошибка API Диска {code}: {body[:200]}"

    def _request(
        self, method: str, endpoint: str, params: dict, body: bytes | None = None
    ) -> dict:
        url = API_BASE + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method, data=body)
        req.add_header("Authorization", "OAuth " + self.token)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise DiskError(self._explain_http(e.code, e.read().decode("utf-8", "replace")))
        except urllib.error.URLError as e:
            raise DiskError(f"Сеть недоступна: {e.reason}")

    @staticmethod
    def _map_item(raw: dict) -> dict:
        return {
            "name": raw.get("name", ""),
            "path": raw.get("path", ""),
            "type": raw.get("type", ""),
            "size": raw.get("size"),
            "modified": (raw.get("modified") or "")[:10],
        }

    def list_folder(self, path: str, limit: int = 1000) -> list[dict[str, Any]]:
        norm = assert_allowed(path)
        data = self._request(
            "GET",
            "/resources",
            {
                "path": norm,
                "limit": limit,
                "fields": "_embedded.items.name,_embedded.items.path,"
                "_embedded.items.type,_embedded.items.size,_embedded.items.modified",
            },
        )
        items = data.get("_embedded", {}).get("items", [])
        return [self._map_item(i) for i in items]

    def get_meta(self, path: str) -> dict[str, Any]:
        norm = assert_allowed(path)
        data = self._request(
            "GET",
            "/resources",
            {"path": norm, "fields": "name,path,type,size,modified,mime_type"},
        )
        out = self._map_item(data)
        out["mime_type"] = data.get("mime_type")
        return out
```

- [ ] **Step 4: Запустить тесты**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/ -q
```

Ожидается: `18 passed`.

- [ ] **Step 5: Коммит**

```bash
cd ~/YandexDiskMCP
git add src/yandex_disk_mcp/client.py tests/test_client.py
git commit -m "Клиент REST API: листинг папки и метаданные

Ошибки переведены в человеческие сообщения; на 401 подсказка со ссылкой
на перевыпуск токена. Сеть изолирована в _request, тесты его подменяют."
```

---

### Task 3: Поиск по именам

**Files:**
- Create: `~/YandexDiskMCP/src/yandex_disk_mcp/search.py`
- Create: `~/YandexDiskMCP/tests/test_search.py`

Нормализация имён вынесена отдельно от `client.py`: это чистые функции без сети, и именно их придётся подкручивать по мере встречи с реальными именами.

**Поиск двухфазный, и это не оптимизация, а необходимость.** Замер на живом API 21.08.2026: один запрос листинга ~1,1 с, в папке 159 подпапок — полный рекурсивный обход занимает ~180 с и вешает клиент. Поэтому: сначала одним запросом читается верхний уровень, затем параллельно (до 8 потоков) листаются только те папки, чьё имя похоже на запрос (`DESCEND_THRESHOLD = 0.3`, мягче основного порога — имя папки «Ламода Тех» не совпадает целиком с запросом «договор ламода 2026»). После правки: 0,9–2,0 с на запрос. Полный обход остаётся доступен через `deep=True`, но зовётся осознанно.

**Interfaces:**
- Consumes: `client.DiskClient.list_folder`, `paths.allowed_roots`
- Produces:
  - `normalize_name(s: str) -> str`
  - `score(query: str, name: str) -> float` — 0.0…1.0
  - `DESCEND_THRESHOLD = 0.3`
  - `SearchIndex(client, ttl: int = 300)` с методами `entries(path: str) -> list[dict]` (листинг одной папки с кэшом) и `find(query: str, limit: int = 50, threshold: float = 0.62, deep: bool = False, max_descend: int = 8) -> list[dict]`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_search.py`:

```python
import pytest
from yandex_disk_mcp.client import DiskClient
from yandex_disk_mcp.search import SearchIndex, normalize_name, score

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS)
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "test-token")

def test_normalize_lowercases_and_drops_legal_form():
    assert normalize_name('ООО "Бейлиш"') == "бейлиш"

def test_normalize_handles_french_quotes():
    assert normalize_name("ООО «Рэддэй»") == "рэддэй"

def test_normalize_strips_trailing_space():
    assert normalize_name("Озон ") == "озон"

def test_normalize_folds_yo():
    assert normalize_name("Королёв") == "королев"

def test_normalize_drops_ip_prefix():
    assert normalize_name("ИП Галкина") == "галкина"

def test_exact_match_scores_one():
    assert score("Сбер", "Сбер") == 1.0

def test_substring_scores_high():
    assert score("Ламода", "Ламода Тех") >= 0.9

def test_typo_still_matches():
    assert score("Райфайзенбанк", "Райффайзенбанк") >= 0.62

def test_unrelated_scores_low():
    assert score("Сбер", "Вкуснятина") < 0.62

def test_synonyms_do_not_match():
    assert score("Тинькофф", "ТБанк") < 0.62

TREE = {
    CONTRACTS: [
        {"name": "Ламода Тех", "path": CONTRACTS + "/Ламода Тех", "type": "dir",
         "size": None, "modified": "2026-05-01"},
        {"name": "Озон ", "path": CONTRACTS + "/Озон ", "type": "dir",
         "size": None, "modified": "2026-04-01"},
        {"name": "Договор Шаблон ООО.docx", "path": CONTRACTS + "/Договор Шаблон ООО.docx",
         "type": "file", "size": 40000, "modified": "2026-02-03"},
    ],
    CONTRACTS + "/Ламода Тех": [
        {"name": "Договор Ламода 2026.docx",
         "path": CONTRACTS + "/Ламода Тех/Договор Ламода 2026.docx",
         "type": "file", "size": 51200, "modified": "2026-05-01"},
    ],
    CONTRACTS + "/Озон ": [],
}

@pytest.fixture
def index(monkeypatch):
    calls = []
    def fake_list(self, path, limit=1000):
        calls.append(path)
        return TREE.get(path, [])
    monkeypatch.setattr(DiskClient, "list_folder", fake_list)
    idx = SearchIndex(DiskClient())
    idx._calls = calls
    return idx

def test_find_locates_folder(index):
    hits = index.find("Ламода")
    assert hits[0]["path"] == CONTRACTS + "/Ламода Тех"

def test_find_locates_file_inside_folder(index):
    hits = index.find("Договор Ламода 2026")
    assert hits[0]["path"].endswith("Договор Ламода 2026.docx")

def test_find_respects_trailing_space_path(index):
    hits = index.find("Озон")
    assert hits[0]["path"] == CONTRACTS + "/Озон "

def test_find_returns_nothing_below_threshold(index):
    assert index.find("Кораблекрушение") == []

def test_find_honours_limit(index):
    assert len(index.find("договор", limit=1)) == 1

def test_root_listing_is_cached(index):
    index.find("Ламода")
    first = len(index._calls)
    index.find("Озон")
    assert index._calls.count(CONTRACTS) == 1, "корень должен листаться один раз"
    assert len(index._calls) >= first


def test_does_not_descend_into_every_folder(index):
    """Главная защита от таймаута: 159 папок обходить нельзя."""
    index.find("Ламода")
    assert len(index._calls) <= 3, f"слишком много запросов: {index._calls}"


def test_deep_mode_descends_everywhere(index):
    index.find("Ламода", deep=True)
    assert CONTRACTS + "/Озон " in index._calls, "deep должен зайти и в непохожие папки"
```

- [ ] **Step 2: Запустить, убедиться что падает**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_search.py -q
```

Ожидается: `ModuleNotFoundError: No module named 'yandex_disk_mcp.search'`.

- [ ] **Step 3: Реализовать `search.py`**

```python
"""Нормализация имён и поиск внутри разрешённых папок.

Собственного поиска у API Диска нет. Полный рекурсивный обход невозможен:
159 подпапок × ~1,1 с на запрос это ~180 с. Поэтому поиск двухфазный —
верхний уровень одним запросом, затем параллельный спуск только в похожие
папки. Нормализация здесь — для сопоставления, она схлопывает пробелы и
режет организационно-правовые формы. К paths.normalize_path отношения не
имеет: там пробелы значимы.
"""

from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any

from .client import DiskClient
from .paths import allowed_roots

_PUNCT = re.compile(r"[«»\"'`,.\-–—_()\[\]/\\]+")
_LEGAL = re.compile(r"(?<!\w)(ооо|оао|зао|пао|ао|ип|ано|чу|до|нко)(?!\w)")

# Порог, ниже которого в папку не спускаемся. Мягче основного: имя папки
# («Ламода Тех») редко совпадает с запросом («договор ламода 2026») целиком.
DESCEND_THRESHOLD = 0.3


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    s = _PUNCT.sub(" ", s)
    s = _LEGAL.sub(" ", s)
    return " ".join(s.split())


def score(query: str, name: str) -> float:
    q, n = normalize_name(query), normalize_name(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n or n in q:
        return 0.9
    return SequenceMatcher(None, q, n).ratio()


class SearchIndex:
    """Поиск по именам с кэшом листингов.

    Полный обход папки договоров нереален: 159 подпапок × ~1.1с на запрос
    это ~3 минуты, клиент отвалится по таймауту. Поэтому ищем в два прохода:
    верхний уровень одним запросом, затем спускаемся только в те папки,
    которые похожи на запрос. Спуски идут параллельно.
    """

    def __init__(self, client: DiskClient, ttl: int = 300) -> None:
        self.client = client
        self.ttl = ttl
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def entries(self, path: str) -> list[dict[str, Any]]:
        """Листинг одной папки с кэшом на ttl секунд."""
        cached = self._cache.get(path)
        if cached and time.monotonic() - cached[0] < self.ttl:
            return cached[1]
        items = self.client.list_folder(path)
        self._cache[path] = (time.monotonic(), items)
        return items

    def find(
        self,
        query: str,
        limit: int = 50,
        threshold: float = 0.62,
        deep: bool = False,
        max_descend: int = 8,
    ) -> list[dict[str, Any]]:
        """Найти папки и файлы по имени.

        deep=True обходит все подпапки — на папке договоров это минуты,
        включать осознанно.
        """
        hits: list[dict] = []
        for root in allowed_roots():
            scored = [(score(query, i["name"]), i) for i in self.entries(root)]
            hits += [{**i, "score": round(s, 3)} for s, i in scored if s >= threshold]

            dirs = [(s, i) for s, i in scored if i["type"] == "dir"]
            if deep:
                candidates = [i for _, i in dirs]
            else:
                candidates = [
                    i for s, i in sorted(dirs, key=lambda x: -x[0])[:max_descend]
                    if s >= DESCEND_THRESHOLD
                ]

            for items in self._entries_many([c["path"] for c in candidates]):
                for i in items:
                    s = score(query, i["name"])
                    if s >= threshold:
                        hits.append({**i, "score": round(s, 3)})

        hits.sort(key=lambda h: (-h["score"], h["path"]))
        return hits[:limit]

    def _entries_many(self, paths: list[str]) -> list[list[dict[str, Any]]]:
        """Листинги нескольких папок параллельно — API отвечает ~1с на запрос."""
        if not paths:
            return []
        if len(paths) == 1:
            return [self.entries(paths[0])]
        with ThreadPoolExecutor(max_workers=min(8, len(paths))) as pool:
            return list(pool.map(self.entries, paths))
```

- [ ] **Step 4: Запустить тесты**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/ -q
```

Ожидается: `36 passed`.

- [ ] **Step 5: Коммит**

```bash
cd ~/YandexDiskMCP
git add src/yandex_disk_mcp/search.py tests/test_search.py
git commit -m "Поиск по именам с нормализацией и кэшом дерева

Режет ООО/ИП/кавычки, терпит опечатки через SequenceMatcher.
Синонимы (Тинькофф/ТБанк) намеренно не раскрываются."
```

---

### Task 4: Ссылки и чтение документов

**Files:**
- Create: `~/YandexDiskMCP/src/yandex_disk_mcp/documents.py`
- Modify: `~/YandexDiskMCP/src/yandex_disk_mcp/client.py` — добавить `download_bytes`
- Create: `~/YandexDiskMCP/tests/test_documents.py`

**Interfaces:**
- Consumes: `paths.assert_allowed`, `client.DiskClient`
- Produces:
  - `build_link(path: str) -> str`
  - `extract_text(filename: str, blob: bytes) -> str` — кидает `UnsupportedFormat` на неизвестном расширении и на нечитаемом PDF
  - `NO_TEXT_LAYER: str`
  - `UnsupportedFormat(RuntimeError)`
  - `DiskClient.download_bytes(path: str) -> bytes`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_documents.py`:

```python
import io

import pytest
from docx import Document

from yandex_disk_mcp.documents import UnsupportedFormat, build_link, extract_text

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS)
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "test-token")


def test_link_encodes_cyrillic_and_keeps_parens():
    link = build_link(CONTRACTS)
    assert link.startswith("https://disk.360.yandex.ru/client/disk/")
    assert "(%D0%A0%D0%B5%D0%B0%D0%BB%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D1%8F)" in link


def test_link_encodes_space_as_percent20():
    assert "%20" in build_link(CONTRACTS)


def test_link_denies_outside_path():
    from yandex_disk_mcp.paths import DiskAccessError

    with pytest.raises(DiskAccessError):
        build_link("disk:/Пароли руководителей")


def _docx_bytes(paragraphs: list[str]) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_from_docx():
    blob = _docx_bytes(["Договор оказания услуг", "Сумма: 500 000 рублей"])
    text = extract_text("Договор.docx", blob)
    assert "Договор оказания услуг" in text
    assert "500 000" in text


def test_extract_text_reads_docx_tables():
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "ИНН"
    table.cell(0, 1).text = "7701234567"
    buf = io.BytesIO()
    doc.save(buf)
    text = extract_text("Реквизиты.docx", buf.getvalue())
    assert "7701234567" in text


def test_unsupported_format_names_the_extension():
    with pytest.raises(UnsupportedFormat) as exc:
        extract_text("скан.jpg", b"\xff\xd8\xff")
    assert ".jpg" in str(exc.value)


def test_pdf_without_text_layer_is_reported_not_silently_empty():
    """Скан без текстового слоя — частый случай; молчать нельзя.

    PDF собираем через PdfWriter: PDF, написанный руками в тесте,
    невалиден и pypdf на нём падает.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    text = extract_text("скан.pdf", buf.getvalue())
    assert "текстового слоя" in text.lower()


def test_broken_pdf_gives_message_not_traceback():
    """Обрезанный файл не должен пробивать наружу исключение pypdf:
    _guard в server.py ловит только DiskError/DiskAccessError/UnsupportedFormat."""
    with pytest.raises(UnsupportedFormat) as exc:
        extract_text("битый.pdf", b"%PDF-1.4\ntruncated garbage")
    assert "не читается" in str(exc.value)
```

- [ ] **Step 2: Запустить, убедиться что падает**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_documents.py -q
```

Ожидается: `ModuleNotFoundError: No module named 'yandex_disk_mcp.documents'`.

- [ ] **Step 3: Добавить `download_bytes` в `client.py`**

Дописать в конец класса `DiskClient`:

```python
    def download_bytes(self, path: str) -> bytes:
        """Скачать файл. Диск отдаёт одноразовый href, по нему идём без токена."""
        norm = assert_allowed(path)
        data = self._request("GET", "/resources/download", {"path": norm})
        href = data.get("href")
        if not href:
            raise DiskError(f"Диск не выдал ссылку на скачивание для «{norm}»")
        req = urllib.request.Request(href)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise DiskError(self._explain_http(e.code, ""))
```

- [ ] **Step 4: Реализовать `documents.py`**

```python
"""Ссылки на файлы и извлечение текста. Единственное место, где
открывается содержимое документа."""

from __future__ import annotations

import io
import urllib.parse

from .paths import DISK_PREFIX, assert_allowed

WEB_BASE = "https://disk.360.yandex.ru/client/disk/"


class UnsupportedFormat(RuntimeError):
    pass


def build_link(path: str) -> str:
    """Приватная ссылка на файл или папку.

    Публичных ссылок не делаем: они открывают документ любому, кто получит
    URL, а в папке лежат договоры с юрлицами.
    """
    norm = assert_allowed(path)
    rest = norm[len(DISK_PREFIX):]
    return WEB_BASE + urllib.parse.quote(rest, safe="/()")


def _from_docx(blob: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(blob))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


NO_TEXT_LAYER = (
    "[В PDF нет текстового слоя — вероятно, это скан. "
    "Распознавание изображений не поддерживается, документ надо открыть глазами.]"
)


def _from_pdf(blob: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(blob))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as e:
        # Битый, обрезанный или зашифрованный PDF. Наружу должно уйти
        # сообщение, а не трейсбек: _guard в server.py ловит только наши типы.
        raise UnsupportedFormat(
            f"PDF не читается ({type(e).__name__}). Файл повреждён, обрезан "
            f"или защищён паролем — открой его по ссылке."
        )
    text = "\n\n".join(p for p in pages if p)
    return text or NO_TEXT_LAYER


def extract_text(filename: str, blob: bytes) -> str:
    lowered = (filename or "").lower()
    if lowered.endswith(".docx"):
        return _from_docx(blob)
    if lowered.endswith(".pdf"):
        return _from_pdf(blob)
    ext = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else "(без расширения)"
    raise UnsupportedFormat(
        f"Формат {ext} не читается. Поддерживаются .docx и .pdf. "
        f"Файл можно открыть по ссылке."
    )
```

- [ ] **Step 5: Запустить тесты**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/ -q
```

Ожидается: `44 passed`.

- [ ] **Step 6: Коммит**

```bash
cd ~/YandexDiskMCP
git add src/yandex_disk_mcp/documents.py src/yandex_disk_mcp/client.py tests/test_documents.py
git commit -m "Ссылки и чтение документов (.docx, .pdf)

Ссылки только приватные. Скан без текстового слоя сообщает об этом
явно, а не возвращает пустую строку."
```

---

### Task 5: Загрузка файла без перезаписи

**Files:**
- Modify: `~/YandexDiskMCP/src/yandex_disk_mcp/client.py` — добавить `upload_file`
- Create: `~/YandexDiskMCP/tests/test_upload.py`

**Interfaces:**
- Consumes: `paths.assert_allowed`, `DiskClient._request`
- Produces: `DiskClient.upload_file(local_path: str, disk_path: str) -> dict` — `{"path", "link", "size"}`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_upload.py`:

```python
import pytest

from yandex_disk_mcp.client import DiskClient, DiskError
from yandex_disk_mcp.paths import DiskAccessError

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS)
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "test-token")


def test_upload_requests_no_overwrite(monkeypatch, tmp_path):
    seen = {}

    def fake_request(self, method, endpoint, params, body=None):
        seen.update(params)
        return {"href": "https://uploader.example/put"}

    def fake_put(self, href, blob):
        return None

    monkeypatch.setattr(DiskClient, "_request", fake_request)
    monkeypatch.setattr(DiskClient, "_put_blob", fake_put)

    src = tmp_path / "Акт.docx"
    src.write_bytes(b"x" * 10)
    DiskClient().upload_file(str(src), CONTRACTS + "/Сбер/Акт.docx")

    assert seen["overwrite"] == "false", "перезапись должна быть запрещена"


def test_upload_denies_outside_path(tmp_path):
    src = tmp_path / "Акт.docx"
    src.write_bytes(b"x")
    with pytest.raises(DiskAccessError):
        DiskClient().upload_file(str(src), "disk:/Пароли руководителей/Акт.docx")


def test_upload_missing_local_file(monkeypatch):
    with pytest.raises(DiskError) as exc:
        DiskClient().upload_file("/nope/Акт.docx", CONTRACTS + "/Сбер/Акт.docx")
    assert "не найден" in str(exc.value).lower()


def test_existing_file_reports_conflict(monkeypatch, tmp_path):
    def fake_request(self, method, endpoint, params, body=None):
        raise DiskError(DiskClient._explain_http(409, ""))

    monkeypatch.setattr(DiskClient, "_request", fake_request)
    src = tmp_path / "Акт.docx"
    src.write_bytes(b"x")
    with pytest.raises(DiskError) as exc:
        DiskClient().upload_file(str(src), CONTRACTS + "/Сбер/Акт.docx")
    assert "уже существует" in str(exc.value)
```

- [ ] **Step 2: Запустить, убедиться что падает**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_upload.py -q
```

Ожидается: `AttributeError: type object 'DiskClient' has no attribute '_put_blob'`.

- [ ] **Step 3: Дописать `upload_file` и `_put_blob` в `client.py`**

```python
    def _put_blob(self, href: str, blob: bytes) -> None:
        req = urllib.request.Request(href, method="PUT", data=blob)
        try:
            urllib.request.urlopen(req).read()
        except urllib.error.HTTPError as e:
            raise DiskError(self._explain_http(e.code, ""))

    def upload_file(self, local_path: str, disk_path: str) -> dict[str, Any]:
        """Загрузить локальный файл. Перезапись запрещена намеренно."""
        norm = assert_allowed(disk_path)
        src = Path(local_path)
        if not src.is_file():
            raise DiskError(f"Локальный файл не найден: {local_path}")
        blob = src.read_bytes()
        data = self._request(
            "GET", "/resources/upload", {"path": norm, "overwrite": "false"}
        )
        href = data.get("href")
        if not href:
            raise DiskError(f"Диск не выдал ссылку на загрузку для «{norm}»")
        self._put_blob(href, blob)
        return {"path": norm, "size": len(blob)}
```

- [ ] **Step 4: Запустить тесты**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/ -q
```

Ожидается: `48 passed`.

- [ ] **Step 5: Коммит**

```bash
cd ~/YandexDiskMCP
git add src/yandex_disk_mcp/client.py tests/test_upload.py
git commit -m "Загрузка файла с overwrite=false

Существующий файл не затирается — возвращается понятная ошибка."
```

---

### Task 6: Тулы MCP и stdio-режим

**Files:**
- Create: `~/YandexDiskMCP/src/yandex_disk_mcp/server.py`
- Create: `~/YandexDiskMCP/tests/test_server.py`

**Interfaces:**
- Consumes: всё из Task 1–5
- Produces: тулы `list_folder`, `find`, `get_link`, `read_document`, `upload`; функции `main()` и `main_http()`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_server.py`:

```python
import pytest

CONTRACTS = "disk:/Договоры с компаниями (Реализация)"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YANDEX_DISK_ALLOWED_PATHS", CONTRACTS)
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "test-token")


def test_destructive_tools_are_absent():
    """Удаления и перемещения не должно существовать. Это не забывчивость."""
    from yandex_disk_mcp import server

    names = {n for n in dir(server) if not n.startswith("_")}
    for forbidden in ("delete", "remove", "move", "rename", "publish", "trash"):
        assert not any(forbidden in n.lower() for n in names), f"найден тул с {forbidden}"


def test_get_link_tool_returns_url():
    from yandex_disk_mcp.server import get_link

    assert get_link(CONTRACTS).startswith("https://disk.360.yandex.ru/client/disk/")


def test_access_error_is_returned_as_message_not_traceback():
    """Отказ доступа должен выглядеть как понятный текст, а не как падение."""
    from yandex_disk_mcp.server import get_link

    out = get_link("disk:/Пароли руководителей")
    assert "вне разрешённых папок" in out


def test_read_document_calls_download_and_extract(monkeypatch):
    import io

    from docx import Document

    from yandex_disk_mcp.client import DiskClient

    doc = Document()
    doc.add_paragraph("Предмет договора")
    buf = io.BytesIO()
    doc.save(buf)

    monkeypatch.setattr(DiskClient, "download_bytes", lambda self, p: buf.getvalue())

    from yandex_disk_mcp.server import read_document

    assert "Предмет договора" in read_document(CONTRACTS + "/Сбер/Договор.docx")
```

- [ ] **Step 2: Запустить, убедиться что падает**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/test_server.py -q
```

Ожидается: `ModuleNotFoundError: No module named 'yandex_disk_mcp.server'`.

- [ ] **Step 3: Реализовать `server.py`**

```python
"""Тулы MCP. Сети не касается — только через client/documents."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import DiskClient, DiskError
from .documents import UnsupportedFormat, build_link, extract_text
from .paths import DiskAccessError, allowed_roots
from .search import SearchIndex

mcp = FastMCP(
    "yandex-disk",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

_index: SearchIndex | None = None


def _client() -> DiskClient:
    return DiskClient()


def _search() -> SearchIndex:
    global _index
    if _index is None:
        _index = SearchIndex(_client())
    return _index


def _guard(fn, *args, **kwargs):
    """Ошибки доступа и API отдаём текстом: агенту нужен ответ, не трейсбек."""
    try:
        return fn(*args, **kwargs)
    except (DiskAccessError, DiskError, UnsupportedFormat) as e:
        return str(e)


@mcp.tool()
def list_folder(path: str) -> Any:
    """Содержимое папки на Диске: имена, тип, размер, дата изменения.

    Содержимое файлов не читается. Работает только внутри разрешённых папок.
    """
    return _guard(lambda: _client().list_folder(path))


@mcp.tool()
def find(query: str, limit: int = 50) -> Any:
    """Поиск папок и файлов по имени внутри разрешённых папок.

    Терпит опечатки и разный регистр, игнорирует ООО/ИП/кавычки.
    Синонимы не раскрывает: «Тинькофф» не найдёт «ТБанк» — это разные имена.
    """
    return _guard(lambda: _search().find(query, limit=limit))


@mcp.tool()
def get_link(path: str) -> str:
    """Приватная ссылка на файл или папку (открывается у тех, у кого есть доступ).

    Публичные ссылки не поддерживаются намеренно — в папке договоры с юрлицами.
    """
    return _guard(lambda: build_link(path))


@mcp.tool()
def read_document(path: str) -> str:
    """Текст документа (.docx или .pdf).

    Единственный тул, открывающий содержимое. Скан без текстового слоя
    об этом скажет — распознавания изображений нет.
    """

    def _run() -> str:
        name = path.rsplit("/", 1)[-1]
        return extract_text(name, _client().download_bytes(path))

    return _guard(_run)


@mcp.tool()
def upload(local_path: str, disk_path: str) -> Any:
    """Загрузить локальный файл на Диск. Существующий файл НЕ перезаписывается.

    Перед вызовом покажи пользователю, что и куда кладёшь, и дождись подтверждения.
    """
    return _guard(lambda: _client().upload_file(local_path, disk_path))


@mcp.tool()
def allowed_folders() -> list[str]:
    """Папки, внутри которых серверу разрешено работать."""
    return allowed_roots()


def main() -> None:
    mcp.run()


def main_http() -> None:
    """HTTP-режим для ozma. Требует YANDEX_DISK_MCP_TOKEN."""
    import uvicorn
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Receive, Scope, Send

    host = os.environ.get("YANDEX_DISK_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("YANDEX_DISK_MCP_PORT", "8005"))
    token = os.environ.get("YANDEX_DISK_MCP_TOKEN")
    if not token:
        raise SystemExit("YANDEX_DISK_MCP_TOKEN must be set for HTTP mode")

    app = mcp.streamable_http_app()

    class TokenAuth:
        def __init__(self, inner: ASGIApp, expected: str) -> None:
            self.inner = inner
            self.expected = expected

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.inner(scope, receive, send)
                return
            qs = scope.get("query_string", b"").decode()
            qtoken = None
            for part in qs.split("&"):
                if part.startswith("token="):
                    qtoken = part[len("token="):]
                    break
            auth_header = None
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    auth_header = v.decode()
                    break
            bearer = None
            if auth_header and auth_header.lower().startswith("bearer "):
                bearer = auth_header.split(" ", 1)[1]
            if qtoken == self.expected or bearer == self.expected:
                await self.inner(scope, receive, send)
                return
            method = scope.get("method", "GET").upper()
            if method not in ("GET", "HEAD", "DELETE"):
                while True:
                    msg = await receive()
                    if msg.get("type") != "http.request":
                        break
                    if not msg.get("more_body", False):
                        break
            resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            await resp(scope, receive, send)

    uvicorn.run(TokenAuth(app, token), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Запустить все тесты**

```bash
cd ~/YandexDiskMCP && .venv/bin/pytest tests/ -q
```

Ожидается: `52 passed`.

- [ ] **Step 5: Проверить, что сервер стартует по stdio**

```bash
cd ~/YandexDiskMCP && timeout 3 .venv/bin/yandex-disk-mcp < /dev/null; echo "exit=$?"
```

Ожидается: `exit=124` (таймаут — сервер жил и ждал ввода) или чистый выход без трейсбека. Трейсбек при старте — провал шага.

- [ ] **Step 6: Коммит**

```bash
cd ~/YandexDiskMCP
git add src/yandex_disk_mcp/server.py tests/test_server.py
git commit -m "Тулы MCP и stdio-режим

Шесть тулов, ни одного разрушающего. Тест проверяет отсутствие
delete/move/rename/publish, чтобы их не дописали по невнимательности."
```

---

### Task 7: Деплой на ozma

**Files:**
- Create: `~/YandexDiskMCP/deploy/env.example`
- Create: `~/YandexDiskMCP/deploy/yandex-disk-mcp.service`
- Create: `~/YandexDiskMCP/deploy/install.sh`
- Create: `~/YandexDiskMCP/README.md`

Порт 8005 — 8004 занят почтовым сервером.

**Interfaces:**
- Consumes: entrypoint `yandex-disk-mcp-http` из Task 6
- Produces: systemd-юнит `yandex-disk-mcp.service`

- [ ] **Step 1: Написать `deploy/env.example`**

```bash
# Скопировать в /etc/yandex-disk-mcp.env на сервере, заполнить, chmod 600
YANDEX_DISK_TOKEN=
YANDEX_DISK_ALLOWED_PATHS=disk:/Договоры с компаниями (Реализация)
YANDEX_DISK_MCP_TOKEN=
YANDEX_DISK_MCP_HOST=127.0.0.1
YANDEX_DISK_MCP_PORT=8005
```

- [ ] **Step 2: Написать `deploy/yandex-disk-mcp.service`**

```ini
[Unit]
Description=Yandex Disk MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/YandexDiskMCP
EnvironmentFile=/etc/yandex-disk-mcp.env
ExecStart=/opt/YandexDiskMCP/.venv/bin/yandex-disk-mcp-http
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Написать `deploy/install.sh`**

```bash
#!/usr/bin/env bash
# Установка на ozma.gogol.school. Запускать из корня склонированного репозитория.
set -euo pipefail

DEST=/opt/YandexDiskMCP

mkdir -p "$DEST"
rsync -a --exclude .venv --exclude .git --exclude .env ./ "$DEST"/

python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -e "$DEST"

if [ ! -f /etc/yandex-disk-mcp.env ]; then
  cp "$DEST/deploy/env.example" /etc/yandex-disk-mcp.env
  chmod 600 /etc/yandex-disk-mcp.env
  echo "Создан /etc/yandex-disk-mcp.env — заполни токены и перезапусти сервис"
fi

cp "$DEST/deploy/yandex-disk-mcp.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now yandex-disk-mcp
systemctl status yandex-disk-mcp --no-pager
```

Сделать исполняемым:

```bash
chmod +x ~/YandexDiskMCP/deploy/install.sh
```

- [ ] **Step 4: Написать `README.md`**

```markdown
# Yandex Disk MCP

MCP-сервер для папки договоров на Яндекс.Диске `bella.fatt@gogol.school`.

## Тулы

- `list_folder(path)` — содержимое папки (имена, тип, размер, дата)
- `find(query, limit)` — поиск по именам, терпит опечатки
- `get_link(path)` — приватная ссылка на файл или папку
- `read_document(path)` — текст из `.docx` / `.pdf`
- `upload(local_path, disk_path)` — загрузка без перезаписи
- `allowed_folders()` — что серверу вообще разрешено

Тулов удаления, перемещения и публичных ссылок нет намеренно.

## Границы

Токен имеет read+write на весь Диск, где кроме договоров лежат
«Пароли руководителей», трудовые договоры и самозанятость мастеров.
Ограничение делается сервером: `YANDEX_DISK_ALLOWED_PATHS` в `.env`,
проверка в `paths.assert_allowed`, тесты в `tests/test_paths.py`.

Расширять белый список — только осознанно, по одной папке.

## Локально

    python -m venv .venv && .venv/bin/pip install -e ".[dev]"
    .venv/bin/pytest -q
    .venv/bin/yandex-disk-mcp

## На сервере

    ./deploy/install.sh

Порт 8005, за токеном в `?token=` или `Authorization: Bearer`.

## Токен Диска

Приложение «GOGOL School Disk MCP», client_id `669ccefb345c48278db3776bacaf80bd`,
скоупы `cloud_api:disk.read` + `cloud_api:disk.write`, живёт год.
Перевыпуск: https://oauth.yandex.ru/authorize?response_type=token&client_id=669ccefb345c48278db3776bacaf80bd
Страница редиректа отдаёт 404 — это нормально, токен в адресной строке после `#access_token=`.
```

- [ ] **Step 5: Проверить, что HTTP-режим отказывает без токена**

```bash
cd ~/YandexDiskMCP && YANDEX_DISK_MCP_TOKEN= .venv/bin/yandex-disk-mcp-http; echo "exit=$?"
```

Ожидается: `YANDEX_DISK_MCP_TOKEN must be set for HTTP mode`, `exit=1`.

- [ ] **Step 6: Коммит**

```bash
cd ~/YandexDiskMCP
git add deploy README.md
git commit -m "Деплой на ozma: systemd-юнит, install.sh, README

Порт 8005 — 8004 занят почтовым сервером."
```

---

### Task 8: Живая проверка и подключение к Claude Code

Всё предыдущее проверялось на моках. Здесь — первое обращение к реальному Диску.

Ожидаемые значения в шагах ниже — не догадки: весь код этого плана был собран в песочнице и прогнан против живого Диска 21.08.2026. Расхождение с этими числами означает, что что-то реализовано иначе.

**Files:**
- Modify: `~/.claude.json` — добавить сервер `yandex-disk` в `mcpServers`

**Interfaces:**
- Consumes: рабочий пакет из Task 1–7

- [ ] **Step 1: Проверить белый список на живом API**

```bash
cd ~/YandexDiskMCP && .venv/bin/python -c "
from yandex_disk_mcp.client import DiskClient
from yandex_disk_mcp.paths import DiskAccessError
c = DiskClient()
items = c.list_folder('disk:/Договоры с компаниями (Реализация)')
print('в папке элементов:', len(items))
try:
    c.list_folder('disk:/Пароли руководителей')
    print('ПРОВАЛ: периметр не сработал')
except DiskAccessError as e:
    print('периметр держит:', str(e)[:60])
"
```

Ожидается: `в папке элементов: 163` (159 папок + 4 шаблона) и строка «периметр держит».

- [ ] **Step 2: Проверить поиск на реальных данных и замерить время**

Время здесь — не придирка, а главный критерий: до двухфазной схемы этот же вызов занимал ~180 с.

```bash
cd ~/YandexDiskMCP && .venv/bin/python -c "
import time
from yandex_disk_mcp.client import DiskClient
from yandex_disk_mcp.search import SearchIndex
from yandex_disk_mcp.documents import build_link
idx = SearchIndex(DiskClient())
for q in ['Ламода', 'Сбер', 'ГПН', 'Договор Шаблон']:
    t0 = time.time(); hits = idx.find(q, limit=4); dt = time.time() - t0
    print(f'{q}: {dt:.2f}s, {len(hits)} совпадений')
    for h in hits[:2]:
        print('   ', h['score'], h['type'], h['path'])
print(build_link('disk:/Договоры с компаниями (Реализация)/Ламода Тех'))
"
```

Ожидается (замер 21.08.2026): первый запрос до **~3,5 с** (холодный кэш — листается корень), последующие **0,6–1,0 с**. «Ламода» → папка «Ламода Тех» (0.9), «Сбер» → папка «Сбер» (1.0) плюс файлы внутри, «ГПН» → файлы в «ГП-РП (от ИП)», «Договор Шаблон» → оба шаблона в корне. Если запрос идёт больше 10 с — двухфазная схема сломана, спуск идёт во все 159 папок.

- [ ] **Step 3: Прочитать реальный шаблон договора**

```bash
cd ~/YandexDiskMCP && .venv/bin/python -c "
from yandex_disk_mcp.client import DiskClient
from yandex_disk_mcp.documents import extract_text
p = 'disk:/Договоры с компаниями (Реализация)/Договор Шаблон ООО.docx'
text = extract_text(p.rsplit('/',1)[-1], DiskClient().download_bytes(p))
print('символов:', len(text))
print(text[:300])
"
```

Ожидается (проверено 21.08.2026): `символов: 13989`, первая строка — «Договор возмездного оказания услуг».

- [ ] **Step 4: Подключить сервер к Claude Code**

```bash
claude mcp add yandex-disk -s user -- ~/YandexDiskMCP/.venv/bin/yandex-disk-mcp
```

Проверить:

```bash
claude mcp list | grep yandex-disk
```

Ожидается: строка `yandex-disk` со статусом подключения.

- [ ] **Step 5: Коммит**

```bash
cd ~/YandexDiskMCP
git add -A
git commit -m "Живая проверка на реальном Диске пройдена" --allow-empty
```

- [ ] **Step 6: Отозвать и перевыпустить токен**

Текущий токен засветился в переписке. После того как всё заработало:

1. id.yandex.ru → Безопасность → Приложения → «GOGOL School Disk MCP» → отозвать доступ
2. Открыть https://oauth.yandex.ru/authorize?response_type=token&client_id=669ccefb345c48278db3776bacaf80bd
3. Новое значение вписать в `~/YandexDiskMCP/.env`, никуда не копируя
4. Перепроверить Step 1
5. Положить токен на страницу Notion «🔐 Токены MCP»

Этот шаг выполняет человек — агент его не делает и не просит токен себе.
