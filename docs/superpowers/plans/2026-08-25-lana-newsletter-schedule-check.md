# Лана: ежедневная проверка «Шаблон рассылки» / «Расписание» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Лана раз в день (10:00 МСК, каждый день включая выходные) проверяет продукты со стартом в ближайшие 3 дня на пустые поля «Шаблон рассылки» и «Расписание» (для многозанятийных) и пингует @Beverkar2 в топике `988` чата `-1003933036305`.

**Architecture:** Изменение confined к одному файлу — `/home/agentbot/bots/lana/bot.py` на сервере ozma.gogol.school (systemd `lana-bot`). Новая плановая job по образцу существующих (`mail`/`anketa`/`digest`): константы (топик, час), текстовый промпт с точной FunQL-логикой, ветка в `orch_fire()`, вызов в `scheduler()` — но **вне** блока "выходные — молчим" (`tm_wday >= 5`), в отличие от трёх существующих job. Отдельный skill-файл в репозитории НЕ создаём: у существующей job `digest` бизнес-логика тоже зашита прямо в промпт `bot.py`, а не вынесена в скилл — новая job следует тому же паттерну. Нового доступа (ALLOWED_TOOLS) не требуется — `mcp__ozma__funql_query` уже разрешён. `PERSONA` не трогаем — «Железное правило №2» ограничивает автономные ЗАПИСИ в CRM/Notion (мы только читаем и шлём сообщение, как уже делает `digest`).

**Tech Stack:** Python 3 (stdlib only, без внешних зависимостей — как весь `bot.py`), OzmaDB FunQL (через `mcp__ozma__funql_query` внутри headless `claude`-прогона), Telegram Bot API.

## Global Constraints

- Прод-бот, живой чат — деплой только через `systemctl restart lana-bot` после `py_compile` и ручного dry-run (`ORCH_RUN_ON_START=newsletter`), см. спеку.
- Перед правкой — бэкап `bot.py` на сервере (конвенция репозитория: `bot.py.bak-YYYYMMDD`).
- Тег в сообщении — ровно `@Beverkar2` (подтверждено в брейншторме).
- Топик `988`, чат `-1003933036305` (существующий форум Ланы «NEW GS Возвраты») — НЕ топик `3` («Сводки»), где живут остальные job.
- Час запуска — `10` (МСК), **каждый день**, включая выходные (в отличие от `anketa`/`digest`/`mail`, которые молчат по выходным).
- Даты в query — реальные `сегодня` и `сегодня+3` в ISO (`YYYY-MM-DD`), `class_status` фильтр — id `1` (Запланировано) и `2` (Согласовано).
- B2B-продукты (`is_b2b = true`) исключены из обоих чеков (решение от 25.08.2026, отменяет более ранний вариант «проверять всё») — фильтр `is_b2b IS NULL OR is_b2b = false`.
- Формат сообщения и заголовки блоков — точно как в спеке: `@Beverkar2 НЕ ЗАПОЛНЕН ШАБЛОН РАССЫЛКИ` / `@Beverkar2 НЕ ЗАПОЛНЕНО РАСПИСАНИЕ ДЛЯ РАССЫЛКИ`, список строк `— Название (старт ДД.ММ.ГГГГ) — https://ozma.gogol.school/views/crm/class_form?id=<id>`.

---

### Task 1: Добавить job `newsletter` в bot.py Ланы

**Files:**
- Modify (на сервере, через SSH): `/home/agentbot/bots/lana/bot.py`
- Локальная рабочая копия для правки: `/private/tmp/claude-501/-Users-bellafatt-gogol-school-ai/4e347011-5d91-4dfa-871a-2cbb9e6e97c2/scratchpad/bot.py`

**Interfaces:**
- Использует существующие: `enqueue(chat, thread, prompt, key, silent=False)`, `orch_fire(job)`, `scheduler()`, `STATE["sched"]` (словарь `job_name -> "YYYY-MM-DD"`), `FORUM_CHAT = -1003933036305`.
- Добавляет константы: `NEWSLETTER_THREAD = 988`, `ORCH_NEWSLETTER_HOUR = 10`, `ORCH_NEWSLETTER_PROMPT` (строка).
- Не меняет сигнатуры существующих функций.

- [x] **Step 1: Скачать текущий bot.py с сервера в рабочую копию**

```bash
mkdir -p /private/tmp/claude-501/-Users-bellafatt-gogol-school-ai/4e347011-5d91-4dfa-871a-2cbb9e6e97c2/scratchpad
scp root@ozma.gogol.school:/home/agentbot/bots/lana/bot.py /private/tmp/claude-501/-Users-bellafatt-gogol-school-ai/4e347011-5d91-4dfa-871a-2cbb9e6e97c2/scratchpad/bot.py
```

Проверка: файл существует и весит ~24КБ (756 строк на момент написания плана).

- [x] **Step 2: Добавить константы топика и часа**

В блоке констант (сразу после строки `ORCH_THREAD = 3  # топик «Сводки» ...`) добавить:

```python
NEWSLETTER_THREAD = 988                            # топик проверки шаблона рассылки/расписания (создан 25.08.2026)
```

В блоке `ORCH_*_HOUR` (сразу после `ORCH_ANKETA_HOUR = 11`) добавить:

```python
ORCH_NEWSLETTER_HOUR = 10              # проверка шаблона рассылки/расписания — 10:00, КАЖДЫЙ день (и выходные)
```

- [x] **Step 3: Добавить промпт job'а**

После блока `ORCH_DIGEST_PROMPT = (...)` (перед строкой `# --- Лимиты прогонов claude ---`) добавить:

```python
ORCH_NEWSLETTER_PROMPT = (
    "(Автозапуск: ежедневная проверка шаблона рассылки и расписания перед стартом продукта. "
    "10:00, каждый день, включая выходные.) "
    "Выполни funql_query — продукты, стартующие в ближайшие 3 дня БЕЗ заполненного «Шаблона рассылки»:\n"
    "SELECT id, name, pl_start_date FROM crm.actions WHERE type = 'Продукт' AND is_deleted = false "
    "AND class_status IN (1, 2) AND (is_b2b IS NULL OR is_b2b = false) "
    "AND pl_start_date >= '<сегодня>'::date "
    "AND pl_start_date <= '<сегодня+3>'::date AND template_newsletter IS NULL ORDER BY pl_start_date "
    "(class_status: 1 = Запланировано, 2 = Согласовано; B2B-продукты (is_b2b = true) ИСКЛЮЧЕНЫ — у них нет "
    "единой рассылки; вместо <сегодня>/<сегодня+3> подставь реальные даты в ISO, сегодня — по времени сервера). "
    "Затем ВТОРЫМ запросом — те же продукты (тот же фильтр type/is_deleted/class_status/is_b2b/pl_start_date), "
    "но где schedule_text IS NULL И у продукта БОЛЬШЕ ОДНОГО дочернего занятия — посчитай отдельным "
    "подзапросом count(*) по crm.actions, где parent_action = id, type = 'Занятие', is_deleted = false, "
    "и оставь только те продукты, где этот счётчик > 1. "
    "Если первый список непуст — сформируй блок:\n"
    "«@Beverkar2 НЕ ЗАПОЛНЕН ШАБЛОН РАССЫЛКИ»\n"
    "и построчно (дефис): Название (старт ДД.ММ.ГГГГ) — "
    "https://ozma.gogol.school/views/crm/class_form?id=<id>. "
    "Если второй список непуст — ОТДЕЛЬНЫМ блоком после строки --- на отдельной строке:\n"
    "«@Beverkar2 НЕ ЗАПОЛНЕНО РАСПИСАНИЕ ДЛЯ РАССЫЛКИ»\n"
    "и такой же построчный список по второму запросу. "
    "Пустой список — блок полностью пропускай, ни слова о нём. "
    "Если оба списка пусты — ответь РОВНО одним словом NOOP (служебный маркер тишины, в чат не уходит). "
    "Никаких других слов, отчётов о прогоне, названий таблиц/полей/SQL в итоговое сообщение не добавляй — "
    "только сам блок(и) в указанном формате."
)
```

- [x] **Step 4: Добавить ветку `newsletter` в `orch_fire()`**

Найти функцию `orch_fire(job)` и после блока `elif job == "digest": ...` добавить:

```python
    elif job == "newsletter":
        print("orch: проверка шаблона рассылки/расписания", flush=True)
        nkey = "%s:%s" % (FORUM_CHAT, NEWSLETTER_THREAD)
        enqueue(FORUM_CHAT, NEWSLETTER_THREAD, ORCH_NEWSLETTER_PROMPT, nkey)
```

(Без `silent=True` — прогон должен слать результат в чат, как `anketa`/`digest`.)

- [x] **Step 5: Вызывать job каждый день, включая выходные — правка `scheduler()`**

Текущий код (после `boot`-блока):

```python
    while True:
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        hour_stamp = time.strftime("%Y-%m-%d %H", now)
        if now.tm_wday >= 5:   # выходные: команда просила тишину (Света, 07.07.2026)
            time.sleep(30)
            continue
        try:
            if now.tm_hour == ORCH_ANKETA_HOUR and sched.get("anketa") != today:
```

Заменить на (вставить проверку `newsletter` ДО блока `if now.tm_wday >= 5`, чтобы она срабатывала и по выходным):

```python
    while True:
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        hour_stamp = time.strftime("%Y-%m-%d %H", now)
        try:
            if now.tm_hour == ORCH_NEWSLETTER_HOUR and sched.get("newsletter") != today:
                sched["newsletter"] = today
                save_state()
                orch_fire("newsletter")
        except Exception as e:
            print("scheduler err:", e, flush=True)
        if now.tm_wday >= 5:   # выходные: почта/анкеты/сводка молчат по просьбе Светы (07.07.2026);
            time.sleep(30)     # newsletter-проверка выше работает и по выходным
            continue
        try:
            if now.tm_hour == ORCH_ANKETA_HOUR and sched.get("anketa") != today:
```

Дальше код (`anketa`/`digest`/`mail` блоки и `time.sleep(30)` в конце цикла) остаётся без изменений.

- [x] **Step 6: Обновить docstring/комментарий планировщика**

Строка `"""Планировщик Ланы: разбор почты каждый час 10:05–18:05, анкеты в 11:00, сводка в 12:00 (МСК, будни)."""` → добавить упоминание новой job:

```python
    """Планировщик Ланы: разбор почты каждый час 10:05–18:05, анкеты в 11:00, сводка в 12:00
    (МСК, будни); проверка шаблона рассылки/расписания в 10:00 (МСК, каждый день, включая выходные)."""
```

- [x] **Step 7: Проверить локальный синтаксис**

```bash
python3 -m py_compile /private/tmp/claude-501/-Users-bellafatt-gogol-school-ai/4e347011-5d91-4dfa-871a-2cbb9e6e97c2/scratchpad/bot.py
```

Ожидается: без вывода (успех), файл `bot.py.pyc`/`__pycache__` появляется рядом.

- [x] **Step 8: Забэкапить прод-файл и залить новую версию на сервер**

```bash
ssh root@ozma.gogol.school "cp /home/agentbot/bots/lana/bot.py /home/agentbot/bots/lana/bot.py.bak-20260825"
scp /private/tmp/claude-501/-Users-bellafatt-gogol-school-ai/4e347011-5d91-4dfa-871a-2cbb9e6e97c2/scratchpad/bot.py root@ozma.gogol.school:/home/agentbot/bots/lana/bot.py
ssh root@ozma.gogol.school "chown agentbot:agentbot /home/agentbot/bots/lana/bot.py && python3 -m py_compile /home/agentbot/bots/lana/bot.py && echo COMPILE_OK && grep -c 'ORCH_NEWSLETTER_HOUR' /home/agentbot/bots/lana/bot.py"
```

Ожидается: `COMPILE_OK` и число `3` (константа + сравнение в scheduler + упоминание в комментарии/промпте — хотя бы 2).

- [x] **Step 9: Ручной dry-run job'а без перезапуска сервиса**

Остановить продовый процесс на время теста, прогнать job один раз через `ORCH_RUN_ON_START`, посмотреть, что реально ушло в топик 988:

```bash
ssh root@ozma.gogol.school "systemctl stop lana-bot && sleep 2 && cd /home/agentbot/bots/lana && sudo -u agentbot bash -c 'set -a; source .env; set +a; ORCH_RUN_ON_START=newsletter timeout 300 python3 bot.py' 2>&1 | tail -60"
```

Ожидается в выводе: строка `orch: проверка шаблона рассылки/расписания`, без трейсбэков. Проверить в Telegram (топик 988), что пришли ожидаемые сообщения — по данным на 25.08.2026 (после исключения B2B) это ровно один блок «НЕ ЗАПОЛНЕН ШАБЛОН РАССЫЛКИ» с единственным пунктом MK03-202608 (id 12270); блока про расписание быть не должно (у MK03-202608 всего 1 занятие). Сравнить с уже отправленным и отредактированным вручную сообщением `989` в топике 988 — набор должен совпадать, если за это время никто не заполнил поля.

- [x] **Step 10: Перезапустить боевой сервис**

```bash
ssh root@ozma.gogol.school "systemctl start lana-bot && sleep 3 && systemctl is-active lana-bot && journalctl -u lana-bot -n 20 --no-pager -q"
```

Ожидается: `active`, в логе `Lana Refund bot started (hermione v3 base)` без ошибок импорта/синтаксиса.

- [x] **Step 11: Закоммитить изменение в репозиторий как заметку о деплое**

Изменения живут только на сервере (bot.py не версионируется в `gogol-school-ai` — это не скилл-часть), поэтому в git фиксируем только сам факт и итоговый код в спеке/памяти. Обновить спеку пометкой о реализации:

```bash
cd /Users/bellafatt/gogol-school-ai
```

В файле `docs/superpowers/specs/2026-08-25-lana-newsletter-schedule-check-design.md` добавить в конец короткую секцию:

```markdown

## Реализовано

25.08.2026 — job `newsletter` добавлена в `/home/agentbot/bots/lana/bot.py` (без отдельного skill-файла в
репозитории — логика зашита в промпт по образцу job `digest`). Бэкап прод-файла:
`bot.py.bak-20260825`. Первая проверка (вручную, до включения job) отправлена в топик 988 сообщениями
id 989/990.
```

- [x] **Step 12: Commit**

```bash
git add docs/superpowers/specs/2026-08-25-lana-newsletter-schedule-check-design.md
git commit -m "$(cat <<'EOF'
Лана: job проверки шаблона рассылки/расписания задеплоена на сервер

bot.py на ozma (не версионируется в репо) получил новую плановую job
newsletter (10:00, каждый день) — пингует @Beverkar2 в топике 988 при
пустых полях "Шаблон рассылки"/"Расписание" у продуктов со стартом
в ближайшие 3 дня. Спека дополнена отметкой о реализации.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Rollback

Если после Step 10 что-то пошло не так (ошибки в логах, спам в чат, некорректные сообщения):

```bash
ssh root@ozma.gogol.school "systemctl stop lana-bot && cp /home/agentbot/bots/lana/bot.py.bak-20260825 /home/agentbot/bots/lana/bot.py && systemctl start lana-bot && systemctl is-active lana-bot"
```
