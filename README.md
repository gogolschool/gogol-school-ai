# gogol-school-ai

AI-ассистенты для сотрудников Gogol School: CLAUDE.md, MCP-конфиги, skills (slash-команды) и типовые промпты — по одной папке на роль.

Витрина с описанием каждой роли живёт в Notion: **Дом GOGOL SCHOOL → Команда → 🤖 AI роли**. Этот репо — источник правды, из него ставятся файлы сотруднику на машину.

## Структура

```
shared/
  CLAUDE.md               ← общий контекст про Gogol School (читают все роли)
  knowledge/
    systems.md            ← обзор Ozma/Bitrix/Notion/Telegram/Sheets
    database.md           ← схема OzmaDB

roles/
  doc-fin-ops/            ← Документооборот + финансы + операции
    CLAUDE.md             ← только специфика роли (общее — в shared/)
    ozma.md               ← как работать с OzmaDB
    .mcp.json             ← список подключаемых MCP-серверов
    skills/
      bank/SKILL.md       ← /bank — внесение банковской выписки
      newcontract/SKILL.md
  client-office-ops/      ← Операционный менеджер
  senior-admin/           ← Администратор
  marketing-assistant/    ← Менеджер по маркетингу
  smm/                    ← СММ
  brand-pr/               ← Бренд-менеджер + PR
  product-manager/        ← Менеджер программ
  student-comms/          ← Менеджер по общению со студентами
  product-assistant/      ← Помощник менеджера продукта

install.sh                ← установщик для macOS / Linux
install.ps1               ← установщик для Windows
```

## Установка ассистента (для сотрудника)

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s <role-slug>
```

Например, для менеджера по документообороту:

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.ps1 -OutFile $env:TEMP\install.ps1
& $env:TEMP\install.ps1 -Role doc-fin-ops
```

### Что делает скрипт

1. Проверяет node, npm, git, claude (на Windows ещё python).
2. Скачивает/обновляет конфигурацию роли в `~/.gogol-ai/repo`.
3. Копирует `CLAUDE.md`, `knowledge/`, `ozma.md`, `skills/` в `~/.claude/`.
4. Для каждого MCP роли спрашивает токены (один раз, кеш в `~/.gogol-ai/.env`) и прописывает сервер в Claude Desktop + Claude Code.

## Обновление

Запустить ту же команду повторно — скрипт идемпотентный, подтянет свежую версию и не будет переспрашивать уже введённые токены.
