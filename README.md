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

Можно ставить **одну или несколько ролей сразу**. Каждая роль создаёт свою рабочую папку `~/gogol-ai/<role>/`, переключение между ролями — это `cd` в нужную папку.

### macOS / Linux

Одна роль:
```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops
```

Несколько ролей сразу:
```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops analyst client-office-ops
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.ps1 -OutFile $env:TEMP\install.ps1
& $env:TEMP\install.ps1 -Roles doc-fin-ops,analyst
```

### Как это раскладывается на машине

```
~/.claude/                   ← глобально (читается во всех сессиях Claude)
  CLAUDE.md                  ← общий контекст про Gogol School
  knowledge/                 ← shared/knowledge/* (systems.md, database.md)
  marketing.md               ← если установлена маркетинговая роль

~/gogol-ai/                  ← видимая папка с рабочими ролями
  doc-fin-ops/
    CLAUDE.md                ← специфика роли
    ozma.md
    .claude/skills/          ← /bank, /newcontract
  analyst/
    CLAUDE.md                ← правила LTV/Retention
    .claude/skills/
  client-office-ops/
    CLAUDE.md
    .claude/skills/

~/.gogol-ai/                 ← скрытая папка с тех. данными
  repo/                      ← клон gogol-school-ai
  .env                       ← токены MCP
  google_token.json          ← Google service account
```

### Как работать с ролью

```bash
cd ~/gogol-ai/doc-fin-ops    # переключилась на документооборот
claude                       # Claude видит CLAUDE.md роли + общий контекст
```

В другой роли:
```bash
cd ~/gogol-ai/analyst
claude                       # Claude видит правила аналитики, нет /bank
```

MCP-серверы (ozma, gogol-site-remote и т.д.) глобальны — доступны из любой роли.

## Обновление

Запустить ту же команду повторно. Скрипт идемпотентный: подтянет свежие файлы из git, не будет переспрашивать токены. Можно дозапустить с новыми ролями, чтобы добавить их к существующим.
