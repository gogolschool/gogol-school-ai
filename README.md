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
  analyst/                ← Аналитик

install.sh                ← установщик для macOS / Linux
install.ps1               ← установщик для Windows
```

## Установка ассистента (для сотрудника)

Можно ставить **одну или несколько ролей в общую рабочую папку**. Сотрудник всегда работает из этой одной папки — даже если у него мультироль.

### macOS / Linux

Одна роль:
```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops
```

Несколько ролей в одну папку:
```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops analyst client-office-ops
```

Кастомное имя папки:
```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s -- --folder bella doc-fin-ops analyst
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.ps1 -OutFile $env:TEMP\install.ps1
& $env:TEMP\install.ps1 -Roles doc-fin-ops,analyst
# или с кастомным именем:
& $env:TEMP\install.ps1 -Roles doc-fin-ops,analyst -Folder bella
```

### Как это раскладывается на машине

```
~/.claude/                   ← глобально (читается во всех сессиях Claude)
  CLAUDE.md                  ← общий контекст про Gogol School
  knowledge/                 ← systems.md, database.md
  marketing.md               ← бренд (для всех ролей доступно)

~/gogol-ai/work/             ← рабочая папка (default name: work)
  CLAUDE.md                  ← склеенный из CLAUDE.md всех выбранных ролей
  ozma.md                    ← склеен, если у нескольких ролей есть
  .claude/skills/            ← все скилы из всех выбранных ролей
    bank/                    ← из doc-fin-ops
    newcontract/             ← из doc-fin-ops
    refund/                  ← из client-office-ops
    ...

~/.gogol-ai/                 ← скрытое (тех. данные)
  repo/                      ← клон gogol-school-ai
  .env                       ← токены MCP
  google_token.json
```

### Как работать

```bash
cd ~/gogol-ai/work          # всегда одна папка
claude                       # Claude видит CLAUDE.md всех твоих ролей + общий контекст
```

В чате доступны скилы всех ролей: `/bank`, `/newcontract`, `/refund` и т.д. — что у тебя установлено.

MCP-серверы глобальны: доступны из любой папки, не нужно настраивать дважды.

## Обновление и добавление ролей

Запусти ту же команду:
- **С тем же списком** — подтянет свежие файлы из git
- **С расширенным списком** — добавит новые роли в ту же папку

```bash
# Было: doc-fin-ops + analyst
# Стало: + marketing-assistant
bash install.sh doc-fin-ops analyst marketing-assistant
```

Токены не переспрашиваются — кэшируются в `~/.gogol-ai/.env`.
