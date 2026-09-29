# Теги в таблице продуктов → задачи: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Тег Кати Романовой или Бэллы в комментарии карточки «🗂️ Таблицы с продуктами» за ≤10 минут превращается в задачу в «✏️ Документооборот и точка».

**Architecture:** Один Python-скрипт без зависимостей (stdlib + Notion REST API) на ozma, запускается systemd-таймером раз в 10 минут. Чистые функции (разбор тегов, сборка блоков/свойств) + исполнитель `run()` с состоянием в JSON. Исходник и тесты живут в репо `scripts/notion_mentions/`, на сервер копируются в `/home/agentbot/bots/notion-mentions/`.

**Tech Stack:** Python 3.10 (сервер) / 3.14 (локально), urllib, pytest (только локально), systemd timer.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-09-29-notion-mentions-to-tasks-design.md`.
- Наблюдаемые: Катя `d5b39214-66db-4761-8ef7-0447637286d0`, Бэлла `40cc4df7-607d-40cb-9ab3-f3d2004a633a`.
- Базы: продукты `1d8612c762af80519c25e6c7b07a19f8`, задачи `38940f6e6dc341d79ce55c0ef6db79bc`.
- Задача: title «Задачи» = «Название программы (внутр)»; «Ответственный» = тегнутый; «Процессы» = «Продукты»; «Дедлайн» = дата тега (МСК); «Status» = «Не начато».
- Одна задача на пару (карточка, человек); повторный тег — дописать тело, Дедлайн = дата тега, «Готово»/«Проверка» → «Не начато».
- Первый запуск без `--backfill-days` задач не создаёт.
- 3 падения подряд → ⚠️ Бэлле (chat 197654998) через бот Тейлор.
- Python 3.10-совместимо (сервер): без `match`, без `datetime.UTC`.
- Notion-Version `2022-06-28` (как у hypotheses.py Тейлор).

## File Structure

- Create: `scripts/notion_mentions/notion_mentions.py` — весь скрипт.
- Create: `scripts/notion_mentions/test_notion_mentions.py` — pytest.
- Create: `scripts/notion_mentions/notion-mentions.service`, `scripts/notion_mentions/notion-mentions.timer` — systemd.
- Сервер: `/home/agentbot/bots/notion-mentions/{notion_mentions.py,state.json,fails.json}`, `/etc/systemd/system/notion-mentions.{service,timer}`; env берётся из `/home/agentbot/bots/taylor/.env` (NOTION_TOKEN, BOT_TOKEN).

---

### Task 1: Чистые функции

**Files:**
- Create: `scripts/notion_mentions/notion_mentions.py`
- Test: `scripts/notion_mentions/test_notion_mentions.py`

**Interfaces — Produces:**
- `watched_mentions(comment: dict, watch: dict = WATCH) -> list[str]` — user id наблюдаемых из rich_text, без повторов, в порядке появления.
- `msk_date(iso: str) -> str` — `YYYY-MM-DD` по МСК.
- `comment_link(page_id: str, discussion_id: str) -> str`.
- `product_title(page: dict) -> str`.
- `task_properties(title: str, uid: str, date: str) -> dict`.
- `mention_blocks(comment: dict, author: str) -> list[dict]`.

- [ ] **Step 1: тесты** — `test_watched_mentions_*`, `test_msk_date_crosses_midnight`, `test_comment_link`, `test_task_properties`, `test_mention_blocks` (код — в файле теста, Task 2 Step 1 дополняет его).
- [ ] **Step 2:** `cd scripts/notion_mentions && python3 -m pytest -q` → FAIL (нет модуля).
- [ ] **Step 3:** реализовать функции (код — в `notion_mentions.py`, секция «чистые функции»).
- [ ] **Step 4:** pytest → PASS.

### Task 2: Исполнитель `run()`

**Interfaces — Consumes:** Task 1. **Produces:**
- `notion(method, path, body=None) -> dict`, `class NotionError(Exception)` с `.code`.
- `run(dry=False, backfill_days=None, now=None) -> list[str]` — список строк-действий (для лога/dry-run).
- Состояние `STATE_FILE`: `{"seen": [comment_id...], "tasks": {"<product_id>:<uid>": task_id}}`.

- [ ] **Step 1: тесты** с фейковым Notion (monkeypatch `nm.notion`): первый запуск ничего не создаёт; новый тег → create с верными свойствами; два наблюдаемых в одном комменте → две задачи; повторный тег → PATCH children + PATCH дедлайна без create; «Готово» → «Не начато»; архивная задача → новая; не-наблюдаемый тег → ничего; dry-run не пишет ни в Notion, ни в state; backfill берёт только свежие.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** реализовать `notion`, `list_products`, `list_comments`, `author_name`, `handle`, `run`, `load_state/save_state`.
- [ ] **Step 4:** pytest → PASS.

### Task 3: CLI и алерт о падениях

**Produces:** `main(argv=None) -> int`, `record_failure(err: str)`, `alert(text: str)`; файл `FAILS_FILE` = `{"fails": N}`.

- [ ] **Step 1: тесты** — 3 падения подряд → `alert` вызван ровно один раз; успех сбрасывает счётчик.
- [ ] **Step 2–4:** FAIL → реализация → PASS.
- [ ] **Step 5: коммит** `scripts/notion_mentions/`.

### Task 4: Подготовка Notion

- [ ] **Step 1:** добавить вариант «Продукты» в multi-select «Процессы» базы задач (рабочий Notion MCP, `notion-update-data-source`), проверить fetch-ем.
- [ ] **Step 2 (Бэлла, вручную):** в notion.so/profile/integrations → «Claude Agents» → Capabilities включить **Read comments**; в базе «✏️ Документооборот и точка» → ••• → Connections → добавить «Claude Agents».
- [ ] **Step 3:** проверка с сервера: `GET /databases/38940f6e…` = 200, `GET /comments?block_id=3e3612c7…` = 200.

### Task 5: Деплой

- [ ] **Step 1:** `scp` скрипта в `/home/agentbot/bots/notion-mentions/`, `chown -R agentbot:agentbot`.
- [ ] **Step 2:** `sudo -u agentbot bash -c 'set -a; . /home/agentbot/bots/taylor/.env; python3 notion_mentions.py --dry-run --backfill-days 7'` — видно, какие задачи создались бы (ожидаем теги Бэллы из «ИНАЧЕ 4/5/6», «ИНТ …»).
- [ ] **Step 3:** unit-файлы в `/etc/systemd/system/`, `systemctl daemon-reload`, `systemctl start notion-mentions.service` (первый прогон = инициализация state, задач нет), `journalctl -u notion-mentions -n 20`.
- [ ] **Step 4:** `systemctl enable --now notion-mentions.timer`, `systemctl list-timers | grep notion`.

### Task 6: Живая проверка и память

- [ ] **Step 1:** после того как кто-то тегнет Катю/Бэллу в карточке (или по согласию — тестовый коммент) — задача появилась в «Документообороте» ≤10 мин.
- [ ] **Step 2:** memory-файл `reference_notion_mentions_tasks.md` + строка в MEMORY.md; коммит спеки/плана.
